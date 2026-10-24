import json
import os
from pathlib import Path
import subprocess
import sys
import threading


def test_fresh_process_shows_raw_data_without_loading_fitting_libraries(app_root, tmp_path):
    report_path = tmp_path/"startup.json"
    environment = dict(os.environ, QT_QPA_PLATFORM="offscreen", PYQTGRAPH_QT_LIB="PySide6")
    completed = subprocess.run([sys.executable, str(app_root/"run.py"), "--startup-check", str(report_path)],
                               cwd=tmp_path, env=environment, capture_output=True, text=True, timeout=30)
    assert completed.returncode == 0, completed.stdout+completed.stderr
    report = json.loads(report_path.read_text(encoding="utf-8"))
    assert report["success"] and report["fullscreen"] and report["workers_closed"]
    assert not report["scipy_loaded_before_raw_spectrum"]
    assert not report["matplotlib_loaded_before_raw_spectrum"]
    assert 0 < report["first_paint_seconds"] <= report["raw_spectrum_seconds"]


def test_worker_discards_frame_held_during_initialization(monkeypatch, qapp, wait):
    import reflectometry_live.fitting as fitting
    from reflectometry_live.workers import Events, FitWorker
    initializing, proceed = threading.Event(), threading.Event()
    processed, delivered = [], []

    class SlowInitialFitter:
        def __init__(self):
            initializing.set()
            assert proceed.wait(3)

        def process(self, frame, calibration, settings):
            processed.append(frame)
            return "fit of " + frame

    monkeypatch.setattr(fitting, "CachedFitter", SlowInitialFitter)
    events = Events()
    events.event.connect(delivered.append)
    worker = FitWorker(events)
    worker.start()
    try:
        old = {"frame": "old", "calibration": None, "settings": None, "generation": 1, "fit_revision": 1}
        latest = dict(old, frame="latest", generation=2, fit_revision=3)
        worker.submit(old)
        wait(initializing.is_set)
        worker.submit(latest)
        proceed.set()
        wait(lambda: bool(delivered))
        assert processed == ["latest"]
        assert delivered[0]["generation"] == 2 and delivered[0]["fit_revision"] == 3
    finally:
        proceed.set()
        worker.shutdown()
        worker.join(4)
        assert not worker.is_alive()
