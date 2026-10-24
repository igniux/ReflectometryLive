"""Exercise the real GUI + acquisition + fit threads for 30 s without hardware."""

import argparse
from collections import Counter
from dataclasses import asdict
import json
import os
from pathlib import Path
import sys
import time

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("PYQTGRAPH_QT_LIB", "PySide6")
os.environ.setdefault("MPLCONFIGDIR", str(ROOT / ".cache" / "matplotlib"))

from PySide6.QtWidgets import QApplication
from reflectometry_live.gui import MainWindow


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--seconds", type=float, default=30)
    args = parser.parse_args()
    app = QApplication([])
    app.setQuitOnLastWindowClosed(False)
    path = next((ROOT / "demo").glob("Si-SiO2 pirmas_*.TXT"))
    window = MainWindow(replay_path=path, preferences_path=False)
    window.show()
    records = []
    collecting = False
    def collect(event):
        if collecting and event["kind"] == "fit":
            frame, calibration, result = event["payload"]
            records.append((frame.sequence, time.monotonic(), result.fit_time_ms, result.status,
                            result.thickness_nm, (time.monotonic()-frame.captured_at)*1000))
    window.events.event.connect(collect)
    start_timeout = time.monotonic()+10
    while window.latest is None and time.monotonic()<start_timeout:
        app.processEvents(); time.sleep(0.005)
    assert window.latest is not None, window.fit_status.text()
    collecting = True
    start = time.monotonic()
    deadline = start+args.seconds
    while time.monotonic()<deadline:
        app.processEvents(); time.sleep(0.005)
    elapsed = time.monotonic()-start
    collecting = False
    out = ROOT / "validation" / "reports" / "benchmarks"
    out.mkdir(parents=True, exist_ok=True)
    previews = ROOT / "validation" / "reports" / "previews"
    previews.mkdir(parents=True, exist_ok=True)
    app.processEvents()
    window.grab().save(str(previews / "replay_window.png"))
    fit_times = [item[2] for item in records]
    rate = (len(records)-1)/(records[-1][1]-records[0][1])
    report = {
        "backend": "Replay of a stored spectrum; not live instrument measurements",
        "duration_seconds": elapsed,
        "distinct_playback_frames_fitted": len(set(item[0] for item in records)),
        "mean_update_hz": rate, "target_update_hz": 5.0,
        "median_fit_ms": float(np.median(fit_times)), "p95_fit_ms": float(np.percentile(fit_times, 95)),
        "maximum_fit_ms": max(fit_times), "p95_acquisition_to_display_ms": float(np.percentile([r[5] for r in records], 95)),
        "fit_statuses": dict(Counter(item[3] for item in records)),
        "thickness_nm": records[-1][4], "acquisition_settings": asdict(window.acquisition_settings),
        "hardware_validation": "Pending: no USB spectrometer found during SDK discovery",
    }
    (out / "replay_benchmark.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(json.dumps(report, indent=2), flush=True)
    window.close()
    deadline = time.monotonic()+10
    while not window.allow_close and time.monotonic()<deadline:
        app.processEvents(); time.sleep(0.01)
    assert window.allow_close, "Workers did not shut down cleanly"
    assert len(set(item[0] for item in records)) == len(records)
    assert rate >= 4.9, f"Update rate below target tolerance: {rate}"
    assert report["p95_fit_ms"] < 50


if __name__ == "__main__":
    main()
