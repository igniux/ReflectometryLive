import os
from pathlib import Path
import sys
import time

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("PYQTGRAPH_QT_LIB", "PySide6")
APP_ROOT = Path(__file__).resolve().parents[1]
os.environ.setdefault("MPLCONFIGDIR", str(APP_ROOT / ".cache" / "matplotlib"))
sys.path.insert(0, str(APP_ROOT))

import pytest


@pytest.fixture(scope="session")
def app_root():
    return APP_ROOT


@pytest.fixture(scope="session")
def qapp():
    from PySide6.QtWidgets import QApplication
    app = QApplication.instance() or QApplication([])
    app.setQuitOnLastWindowClosed(False)
    yield app
    # Match the QApplication aboutToQuit graphics cleanup in production.
    import pyqtgraph as pg
    pg.cleanup()


@pytest.fixture
def wait(qapp):
    def until(condition, timeout=6):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            qapp.processEvents()
            if condition():
                return
            time.sleep(0.01)
        raise AssertionError("Asynchronous operation did not complete before timeout")
    return until
