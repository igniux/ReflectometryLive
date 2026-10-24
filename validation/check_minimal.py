"""Preview the real October spectrum in the compact interface and time live replay."""
from collections import Counter
import json
import os
from pathlib import Path
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
REPORTS = ROOT/"validation"/"reports"
PREVIEWS = REPORTS/"previews"
PREVIEWS.mkdir(parents=True, exist_ok=True)
(REPORTS/"benchmarks").mkdir(parents=True, exist_ok=True)
sys.path.insert(0, str(ROOT))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("PYQTGRAPH_QT_LIB", "PySide6")
os.environ.setdefault("MPLCONFIGDIR", str(ROOT / ".cache" / "matplotlib"))
from PySide6.QtWidgets import QApplication
from reflectometry_live.gui import MainWindow
from reflectometry_live.panels import SaveDialog


def main():
    app = QApplication([])
    app.setQuitOnLastWindowClosed(False)
    comparison = json.loads((ROOT/"validation"/"oct08_refit"/"comparison.json").read_text(encoding="utf-8"))
    saved = Path(comparison["snapshot"])
    path = saved/("metadata.json" if (saved/"metadata.json").exists() else "spectrum.csv")
    window = MainWindow(replay_path=path, preferences_path=False)
    window.show()
    deadline = time.monotonic()+15
    while window.latest is None and time.monotonic() < deadline:
        app.processEvents(); time.sleep(.005)
    assert window.latest is not None, window.fit_status.text()
    end = time.monotonic()+3
    while time.monotonic() < end:
        app.processEvents(); time.sleep(.005)
    window.grab().save(str(PREVIEWS/"minimal_window.png"))
    window.tabs.setCurrentIndex(1)
    app.processEvents()
    window.grab().save(str(PREVIEWS/"minimal_search.png"))
    window.tabs.setCurrentIndex(0)
    dialog = SaveDialog(window.latest, ROOT/"Measurements", "SiO2-AlN wafer · spot 1", window.spectrum_plot.grab(), window)
    dialog.show(); app.processEvents()
    dialog.grab().save(str(PREVIEWS/"save_dialog.png"))
    dialog.close(); dialog.deleteLater()
    # Finish the dialog's layout/paint events before collecting the live rate.
    settle = time.monotonic()+2
    while time.monotonic() < settle:
        app.processEvents(); time.sleep(.003)
    records = []
    def collect(event):
        if event["kind"] == "fit":
            frame, _, result = event["payload"]
            records.append((frame.sequence, time.monotonic(), result.fit_time_ms, result.status))
    window.events.event.connect(collect)
    end = time.monotonic()+30
    while time.monotonic() < end:
        app.processEvents(); time.sleep(.003)
    report = {"backend": "Replay of the real October 8 spectrum; no new instrument measurements",
              "duration_seconds": 30, "completed_frames": len(records),
              "distinct_frames": len(set(row[0] for row in records)),
              "updates_hz": (len(records)-1)/(records[-1][1]-records[0][1]),
              "median_fit_ms": sorted(row[2] for row in records)[len(records)//2],
              "statuses": dict(Counter(row[3] for row in records)),
              "fit": window.latest[2].summary()}
    window.close()
    end = time.monotonic()+10
    while not window.allow_close and time.monotonic() < end:
        app.processEvents(); time.sleep(.01)
    assert window.allow_close
    report["workers_closed"] = not window.acquirer.is_alive() and not window.fitter.is_alive()
    (REPORTS/"benchmarks"/"minimal_benchmark.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(json.dumps({k: v for k, v in report.items() if k != "fit"}, indent=2))


if __name__ == "__main__":
    main()
