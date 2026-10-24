"""Launch the live application from any current directory."""

import argparse
import os
from pathlib import Path
import sys
import time

from reflectometry_live.paths import APP_ROOT, ASSET_ROOT
os.environ.setdefault("MPLCONFIGDIR", str(APP_ROOT / ".cache" / "matplotlib"))
os.environ.setdefault("PYQTGRAPH_QT_LIB", "PySide6")


def main(startup_started=None):
    startup_started = startup_started if startup_started is not None else time.perf_counter()
    parser = argparse.ArgumentParser(description="Live Avantes SiO2-on-Si or SiO2/AlN-on-sapphire thickness fitting")
    parser.add_argument("--replay", type=Path, help="Open an Avantes export or saved measurement in replay mode")
    parser.add_argument("--sdk-check", action="store_true", help="Load only the application-local DLL and list USB devices, without acquiring spectra")
    parser.add_argument("--self-test", type=Path, metavar="REPORT_JSON", help=argparse.SUPPRESS)
    parser.add_argument("--startup-check", type=Path, metavar="REPORT_JSON", help=argparse.SUPPRESS)
    args = parser.parse_args()
    if args.self_test:
        from reflectometry_live.packaged_check import run_check
        return run_check(args.self_test)
    if args.sdk_check:
        from reflectometry_live.avantes import AvantesSource
        source = AvantesSource()
        try:
            source._load()
            count = source.lib.AVS_Init(0)
            if count < 0:
                raise RuntimeError(f"SDK initialization error {count}")
            source.initialized = True
            count = source.lib.AVS_UpdateUSBDevices()
            if count < 0:
                raise RuntimeError(f"USB discovery error {count}")
            print("Loaded application-local SDK:", APP_ROOT / "avaspecx64.dll")
            print("Connected USB spectrometers:", count)
            return 0
        finally:
            source.close()
    from PySide6.QtWidgets import QApplication
    qt_imported = time.perf_counter()
    from reflectometry_live.gui import MainWindow
    gui_imported = time.perf_counter()
    application = QApplication(sys.argv[:1])
    application.setApplicationName("Reflectometry Live")
    application.setOrganizationName("Laboratorija")
    from PySide6.QtGui import QIcon
    application.setWindowIcon(QIcon(str(ASSET_ROOT/"reflectometry.ico")))
    if os.name == "nt":
        import ctypes
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("Laboratorija.ReflectometryLive")
    options = {}
    if args.startup_check:
        from reflectometry_live.startup_check import RawSource
        options = {"source_factory": lambda descriptor: RawSource(), "preferences_path": False}
    window = MainWindow(replay_path=args.replay, **options)
    constructed = time.perf_counter()
    window.showFullScreen()
    if args.startup_check:
        from reflectometry_live.startup_check import observe_startup
        observe_startup(application, window, args.startup_check, startup_started,
                        {"qt_import_seconds": qt_imported-startup_started,
                         "gui_import_seconds": gui_imported-qt_imported,
                         "construction_seconds": constructed-gui_imported})
    else:
        print(f"Window shown in {time.perf_counter()-startup_started:.3f} s")
    return application.exec()


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as error:
        print(f"Reflectometry Live: {error}", file=sys.stderr)
        raise SystemExit(1)
