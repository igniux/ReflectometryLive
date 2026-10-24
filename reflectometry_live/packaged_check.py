"""Opt-in executable validation: replay only; load the SDK without USB discovery."""
import hashlib
import json
from pathlib import Path
import sys
import time
import traceback

from .paths import APP_ROOT, RESOURCE_ROOT, ASSET_ROOT, DEMO_ROOT


def run_check(report_path):
    report_path = Path(report_path).resolve()
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report = {"frozen": bool(getattr(sys, "frozen", False)), "executable": sys.executable,
              "application_root": str(APP_ROOT), "resource_root": str(RESOURCE_ROOT),
              "working_directory": str(Path.cwd()), "hardware_acquisition": False,
              "checks": [], "success": False}
    window = app = None
    try:
        import numpy as np
        from PySide6.QtWidgets import QApplication
        from PySide6.QtCore import QSize
        from PySide6.QtGui import QIcon
        import pyqtgraph as pg
        from .avantes import AvantesSource, SDK_PATH
        from .domain import FitSettings, LayerSettings
        from .fitting import CachedFitter
        from .io import load_replay
        from .optics import stack_reflectance
        from .gui import MainWindow

        def wait_for(condition, timeout=20):
            deadline = time.monotonic()+timeout
            while not condition() and time.monotonic() < deadline:
                app.processEvents()
                time.sleep(.005)
            if not condition():
                raise AssertionError("Packaged application operation timed out")

        fitter = CachedFitter()
        estimates = {}
        for word, expected in (("pirmas", 528.207), ("antras", 513.865), ("trecias", 506.040)):
            path = next(DEMO_ROOT.glob(f"Si-SiO2 {word}_*.TXT"))
            frame, cal, _ = load_replay(path)
            result = fitter.process(frame, cal)
            assert abs(result.thickness_nm-expected) < .02, result.summary()
            estimates[word] = result.thickness_nm
        report["historical_thicknesses_nm"] = estimates
        report["checks"].append("All three bundled Avantes exports reproduce their historical fits")

        wave = np.linspace(500, 850, 251)
        for materials, truth, substrate in ((("SiO2", "AlN"), (520., 580.), "sapphire"),
                                            (("GaN",), (734.2,), "sapphire"),
                                            (("AZ3027E",), (2734.2,), "Si")):
            layers = tuple(LayerSettings(m, d, max(0, d-250), d+250, True) for m, d in zip(materials, truth))
            settings = FitSettings(500, 850, model="stack", layers=layers, substrate=substrate)
            signal = stack_reflectance(wave, materials, truth, substrate)
            result = fitter.fit_signal(wave, signal, settings)
            np.testing.assert_allclose(result.layer_values(), truth, atol=.002)
        report["checks"].append("Bundled Si/SiO2/AlN/sapphire/GaN/photoresist data and numerical searches work")

        sdk = AvantesSource()
        report["sdk_path"] = str(SDK_PATH)
        report["sdk_load_checked"] = False
        try:
            assert SDK_PATH == APP_ROOT/"avaspecx64.dll"
            if SDK_PATH.is_file():
                sdk._load()  # Deliberately no AVS_Init/Connect/measurement on a physical device.
                report["sdk_sha256"] = hashlib.sha256(SDK_PATH.read_bytes()).hexdigest()
                report["sdk_load_checked"] = True
        finally:
            sdk.close()
        report["checks"].append("Application-local x64 SDK loads without relying on the original SDK location"
                                if report["sdk_load_checked"] else "Optional vendor SDK absent; replay-only validation, USB SDK loading skipped")

        app = QApplication.instance() or QApplication([])
        app.setQuitOnLastWindowClosed(False)
        for name in ("reflectometry.ico", "delete.svg", "window-minimize.svg", "window-maximize.svg",
                     "window-restore.svg", "window-close.svg"):
            icon = QIcon(str(ASSET_ROOT/name))
            assert not icon.isNull() and not icon.pixmap(QSize(24, 24)).isNull(), name
        window = MainWindow(replay_path=next(DEMO_ROOT.glob("Si-SiO2 pirmas_*.TXT")), preferences_path=False)
        window.showFullScreen()
        wait_for(lambda: window.latest is not None)
        assert window.isFullScreen()
        assert window.layer_editor.rows[0].uncertainty.text().startswith("±")
        assert window.layer_editor.rows[0].material.itemText(window.layer_editor.rows[0].material.findData("AZ3027E")) == "AZ 3027"
        report["checks"].append("Fullscreen GUI, SVG trash icon, app icon, latest labels and live replay work")
        window.window_maximize_button.click()
        wait_for(lambda: not window.isFullScreen() and not window.isMaximized())
        assert not window.window_controls.isVisible()
        window.showMaximized()
        wait_for(window.isMaximized)
        window.fullscreen_action.trigger()
        wait_for(window.isFullScreen)
        sequence = window.latest[0].sequence
        window.window_minimize_button.click()
        wait_for(window.isMinimized)
        wait_for(lambda: window.latest[0].sequence > sequence)
        window.showMaximized()
        window.fullscreen_action.trigger()
        wait_for(window.isFullScreen)
        assert window.window_maximize_button.toolTip() == "Restore down"
        assert window.window_controls.isVisible()
        report["checks"].append("Window minimize and maximize/restore buttons work; live fitting continues while minimized")
        window.grab().save(str(report_path.with_suffix(".png")))

        sequences = []
        deadline = time.monotonic()+3
        while time.monotonic() < deadline:
            app.processEvents()
            if window.latest is not None:
                sequences.append(window.latest[0].sequence)
            time.sleep(.005)
        report["distinct_replay_frames_in_3_seconds"] = len(set(sequences))
        assert len(set(sequences)) >= 10

        frozen = window.latest
        future = window.save_to(report_path.parent/"snapshot_validation", "Packaged replay validation", snapshot=frozen)
        wait_for(lambda: future.done(), timeout=30)
        saved = future.result()
        frame, cal, metadata = load_replay(saved/"spectrum.csv")
        restored = fitter.process(frame, cal, FitSettings.from_dict(metadata["fit_settings"]))
        np.testing.assert_allclose(restored.layer_values(), frozen[2].layer_values(), atol=1e-5)
        np.testing.assert_allclose(restored.layer_std_nm, frozen[2].layer_std_nm, atol=1e-9)
        assert {path.name for path in saved.iterdir()} == {"spectrum.csv", "fit.png"}
        assert (saved/"fit.png").stat().st_size > 1000
        report["snapshot"] = str(saved)
        report["checks"].append("Two-file CSV/PNG export and CSV-only reload reproduce the captured fit and fit error")

        window.stop_measurement()
        wait_for(lambda: not window.pending and not window.running)
        sequence = window.latest[0].sequence
        window.start_measurement()
        wait_for(lambda: window.latest is not None and window.latest[0].sequence > sequence)
        report["checks"].append("Stop and restart work in the executable")
        report["success"] = True
    except Exception:
        report["error"] = traceback.format_exc()
    finally:
        if window is not None:
            window.window_close_button.click()
            deadline = time.monotonic()+15
            while not window.allow_close and time.monotonic() < deadline:
                app.processEvents()
                time.sleep(.01)
            closed = window.allow_close and not window.acquirer.is_alive() and not window.fitter.is_alive()
            report["workers_closed"] = closed
            report["success"] = report["success"] and closed
            if closed:
                report["checks"].append("Window close button releases acquisition and fit workers cleanly")
        if app is not None:
            import pyqtgraph as pg
            pg.cleanup()
        report_path.write_text(json.dumps(report, indent=2, allow_nan=False), encoding="utf-8")
        print(json.dumps(report, indent=2))
    return 0 if report["success"] else 1
