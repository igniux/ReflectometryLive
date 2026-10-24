from PySide6.QtCore import Qt

from reflectometry_live.gui import MainWindow


def test_window_controls_restore_maximize_minimize_live_and_close_during_save(qapp, wait, app_root, tmp_path):
    window = MainWindow(replay_path=next((app_root/"demo").glob("Si-SiO2 pirmas_*.TXT")), preferences_path=False)
    window.showFullScreen()
    try:
        wait(lambda: window.latest is not None)
        assert window.menuBar().cornerWidget(Qt.Corner.TopRightCorner) is window.window_controls
        for button in (window.window_minimize_button, window.window_maximize_button, window.window_close_button):
            assert button.isVisible() and button.isEnabled()
            assert not button.icon().pixmap(button.iconSize()).isNull()
            assert button.accessibleName()
        assert window.window_maximize_button.toolTip() == "Restore down"
        window.window_maximize_button.click()
        wait(lambda: not window.isFullScreen() and not window.isMaximized())
        assert not window.fullscreen_action.isChecked()
        assert not window.window_controls.isVisible()
        assert window.window_maximize_button.toolTip() == "Maximize"
        window.showMaximized()  # Native caption controls take over outside fullscreen.
        wait(window.isMaximized)
        assert not window.window_controls.isVisible()
        assert window.window_maximize_button.toolTip() == "Restore down"
        window.showNormal()
        wait(lambda: not window.isMaximized())
        window.fullscreen_action.trigger()
        wait(window.isFullScreen)
        assert window.window_controls.isVisible()
        state = window.windowState()
        sequence = window.latest[0].sequence
        window.window_minimize_button.click()
        wait(window.isMinimized)
        wait(lambda: window.latest[0].sequence > sequence)
        assert window.running and window.acquirer.is_alive() and window.fitter.is_alive()
        window.setWindowState(state)
        window.show()
        wait(lambda: window.isFullScreen() and not window.isMinimized())
        future = window.save_to(tmp_path, "Close during save", snapshot=window.latest)
        window.window_close_button.click()
        assert window.closing
        wait(lambda: window.allow_close and not window.acquirer.is_alive() and not window.fitter.is_alive(), timeout=15)
        assert future.done()
        assert {p.name for p in future.result().iterdir()} == {"spectrum.csv", "fit.png"}
    finally:
        if not window.closing:
            window.close()
        wait(lambda: window.allow_close and not window.acquirer.is_alive() and not window.fitter.is_alive(), timeout=15)
