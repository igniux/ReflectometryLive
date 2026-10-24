"""Opt-in startup timing of the actual entry point, using raw simulated acquisition."""
import json
from pathlib import Path
import sys
import time

import numpy as np
from PySide6.QtCore import QTimer
from .domain import Frame
from .avantes import MeasurementCancelled


class RawSource:
    def __init__(self):
        self.sequence = 0
        self.wave = np.linspace(400, 850, 200)

    def connect(self, settings):
        self.settings = settings
        return {"serial": "STARTUP_SIM", "name": "Startup diagnostic", "pixels": len(self.wave),
                "source_kind": "Avantes USB", "settings": settings}

    def configure(self, settings):
        self.settings = settings

    def read(self, cancel):
        if cancel.wait(.005):
            raise MeasurementCancelled()
        self.sequence += 1
        counts = 20000+5000*np.sin(self.wave/50)
        return Frame(self.wave, counts, np.zeros(len(counts), bool), "STARTUP_SIM", self.settings, self.sequence)

    def stop(self):
        pass

    def close(self):
        return []


def observe_startup(app, window, report_path, started, stages):
    app.setQuitOnLastWindowClosed(False)
    report_path = Path(report_path).resolve()
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report = {"frozen": bool(getattr(sys, "frozen", False)), "hardware_acquisition": False,
              "executable": sys.executable, "stages": stages, "success": False}
    observer = QTimer(app)
    observer.setInterval(20)
    finishing = False

    def poll():
        nonlocal finishing
        if not finishing:
            if window.first_paint_at is not None and window.last_raw is not None:
                report["first_paint_seconds"] = window.first_paint_at-started
                report["raw_spectrum_seconds"] = time.perf_counter()-started
                report["scipy_loaded_before_raw_spectrum"] = "scipy.optimize" in sys.modules
                report["matplotlib_loaded_before_raw_spectrum"] = "matplotlib" in sys.modules
                report["fullscreen"] = window.isFullScreen()
                report["success"] = not report["scipy_loaded_before_raw_spectrum"] and report["fullscreen"]
                finishing = True
                window.close()
            elif time.perf_counter()-started > 25:
                report["error"] = "Startup timed out"
                finishing = True
                window.close()
        if finishing and window.allow_close:
            observer.stop()
            report["workers_closed"] = not window.acquirer.is_alive() and not window.fitter.is_alive()
            report["success"] = report["success"] and report["workers_closed"]
            report_path.write_text(json.dumps(report, indent=2), encoding="utf-8")
            print(json.dumps(report, indent=2))
            app.exit(0 if report["success"] else 1)

    observer.timeout.connect(poll)
    observer.start()
