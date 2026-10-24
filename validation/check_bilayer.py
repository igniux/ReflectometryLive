"""Synthetic numerical benchmark and optional desktop preview; no USB access."""
import argparse
import json
import os
from pathlib import Path
import sys
import time
from dataclasses import replace

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
REPORTS = ROOT/"validation"/"reports"
PREVIEWS = REPORTS/"previews"
PREVIEWS.mkdir(parents=True, exist_ok=True)
(REPORTS/"benchmarks").mkdir(parents=True, exist_ok=True)
sys.path.insert(0, str(ROOT))
os.environ.setdefault("MPLCONFIGDIR", str(ROOT / ".cache" / "matplotlib"))
from reflectometry_live.domain import AcquisitionSettings, Calibration, FitSettings, Frame
from reflectometry_live.fitting import CachedFitter
from reflectometry_live.optics import bilayer_reflectance


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--preview", action="store_true")
    parser.add_argument("--images-only", action="store_true", help="Refresh desktop images without replacing the benchmark report")
    parser.add_argument("--seconds", type=float, default=10, help="Duration of simulated GUI acquisition")
    args = parser.parse_args()
    wave = np.linspace(400, 850, 791)
    settings = FitSettings(model="sio2_aln_sapphire")
    acquisition = AcquisitionSettings()
    zeros = np.zeros(len(wave))
    calibration = Calibration(wave, zeros+10, zeros+40010, zeros, zeros, zeros.astype(bool),
                              "SYNTHETIC", acquisition, None, None)
    signal = 1.07*bilayer_reflectance(wave, 523.7, 487.2)+1.3
    frame = Frame(wave, 10+400*signal, zeros.astype(bool), "SYNTHETIC", acquisition, 0, source_kind="Simulation")
    fitter = CachedFitter()
    first = fitter.process(frame, calibration, settings)
    elapsed = []
    for _ in range(50):
        result = fitter.process(frame, calibration, settings)
        elapsed.append(result.fit_time_ms)
    report = {"kind": "Synthetic two-layer fitting, not a hardware measurement", "channels": len(wave),
              "grid_shape": list(result.search_rmse_pp.shape), "frames": len(elapsed),
              "cold_fit_ms": first.fit_time_ms, "warm_fit_median_ms": float(np.median(elapsed)),
              "warm_fit_p95_ms": float(np.percentile(elapsed, 95)), "warm_fit_max_ms": float(np.max(elapsed)),
              "result": result.summary()}
    if args.preview or args.images_only:
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        from PySide6.QtWidgets import QApplication
        from reflectometry_live.gui import MainWindow
        class Simulation:
            def connect(self, supplied_settings):
                return {"serial": "SYNTHETIC", "pixels": len(wave), "source_kind": "Simulation",
                        "settings": acquisition, "calibration": calibration}
            def configure(self, supplied_settings): pass
            def read(self, cancel):
                return replace(frame, sequence=int(time.monotonic()*10), captured_at=time.monotonic())
            def stop(self): pass
            def close(self): return []
        app = QApplication([])
        window = MainWindow(source_factory=lambda descriptor: Simulation(), preferences_path=False, initial_fit_settings=settings)
        window.show()
        deadline = time.monotonic()+10
        while not window.connected and time.monotonic() < deadline:
            app.processEvents(); time.sleep(.01)
        while window.latest is None and time.monotonic() < deadline:
            app.processEvents(); time.sleep(.01)
        if window.latest is None:
            raise RuntimeError("Preview did not produce a completed fit")
        warm_until = time.monotonic()+3
        while time.monotonic() < warm_until:
            app.processEvents(); time.sleep(.002)
        window.grab().save(str(PREVIEWS / "bilayer_window.png"))
        from PySide6.QtWidgets import QTabWidget
        window.findChild(QTabWidget).setCurrentIndex(1)
        app.processEvents()
        window.grab().save(str(PREVIEWS / "bilayer_search.png"))
        # Finish painting the newly selected heatmap before timing acquisition.
        settle_until = time.monotonic()+1
        while time.monotonic() < settle_until:
            app.processEvents(); time.sleep(.002)
        fitted_at = []
        def collect(event):
            if event["kind"] == "fit":
                fitted_at.append(time.monotonic())
        window.events.event.connect(collect)
        end = time.monotonic()+(0 if args.images_only else args.seconds)
        while time.monotonic() < end:
            app.processEvents(); time.sleep(.002)
        if len(fitted_at) >= 2:
            report["simulated_gui_update_hz"] = (len(fitted_at)-1)/(fitted_at[-1]-fitted_at[0])
            report["simulated_gui_seconds"] = args.seconds
            report["simulated_completed_frames"] = len(fitted_at)
        window.close()
        while not window.allow_close and time.monotonic() < end+10:
            app.processEvents(); time.sleep(.01)
        report["workers_closed"] = not window.fitter.is_alive() and not window.acquirer.is_alive()
    if not args.images_only:
        (REPORTS / "benchmarks" / "bilayer_benchmark.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(json.dumps({k: v for k, v in report.items() if k != "result"}, indent=2))


if __name__ == "__main__":
    main()
