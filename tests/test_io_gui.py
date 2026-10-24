from dataclasses import replace
from pathlib import Path
import time

import numpy as np
import pytest

from reflectometry_live.avantes import DeviceChoiceRequired, MeasurementCancelled
from reflectometry_live.domain import AcquisitionSettings, FitSettings, Frame, LayerSettings
from reflectometry_live.fitting import CachedFitter
from reflectometry_live.gui import MainWindow
from reflectometry_live.io import load_replay, write_snapshot
from reflectometry_live.optics import bilayer_reflectance, film_reflectance, load_si_constants


def close_window(window, wait):
    window.close()
    wait(lambda: window.allow_close and not window.acquirer.is_alive() and not window.fitter.is_alive(), timeout=12)


def test_snapshot_roundtrip_and_no_overwrite(app_root, tmp_path):
    path = next((app_root / "demo").glob("Si-SiO2 pirmas_*.TXT"))
    frame, calibration, _ = load_replay(path)
    assert calibration.dark_timestamp_utc is None
    assert calibration.reference_timestamp_utc is None
    fitter = CachedFitter()
    result = fitter.process(frame, calibration)
    folder = write_snapshot(tmp_path, "wafer 1 / spot 2", frame, calibration, result)
    restored_frame, restored_cal, metadata = load_replay(folder / "spectrum.csv")
    restored = fitter.process(restored_frame, restored_cal, result.settings)
    assert abs(restored.thickness_nm-result.thickness_nm) < 1e-5
    assert np.array_equal(frame.counts, restored_frame.counts)
    assert metadata["frame"]["sequence"] == frame.sequence
    assert metadata["calibration"]["dark_timestamp_utc"] is None
    assert metadata["calibration"]["settings"] == metadata["frame"]["settings"]
    assert restored_frame.timestamp_utc == frame.timestamp_utc
    assert not (folder / ".incomplete").exists()
    assert {p.name for p in folder.iterdir()} == {"spectrum.csv", "fit.png"}
    assert (folder / "fit.png").stat().st_size > 1000
    second = write_snapshot(tmp_path, "wafer 1 / spot 2", frame, calibration, result)
    assert second != folder and folder.exists()


def test_replay_start_stop_save_while_running_and_reconnect(qapp, wait, app_root, tmp_path):
    path = next((app_root / "demo").glob("Si-SiO2 pirmas_*.TXT"))
    window = MainWindow(replay_path=path, preferences_path=False)
    window.show()
    try:
        wait(lambda: window.latest is not None)
        assert window.layer_editor.rows[0].fitted.text() == "528.2"
        frame_sequence = window.latest[0].sequence
        future = window.save_to(tmp_path, "snapshot while streaming")
        wait(lambda: future.done() and window.latest[0].sequence > frame_sequence, timeout=12)
        folder = future.result()
        _, _, metadata = load_replay(folder / "spectrum.csv")
        assert metadata["frame"]["sequence"] == frame_sequence
        assert window.latest[0].sequence > frame_sequence
        window.stop_measurement()
        wait(lambda: not window.pending)
        frozen = window.latest[0].sequence
        for _ in range(20): qapp.processEvents(); time.sleep(0.01)
        assert window.latest[0].sequence == frozen
        window.start_measurement()
        wait(lambda: window.latest[0].sequence > frozen)
        window.disconnect_source()
        wait(lambda: not window.pending and not window.connected)
        assert window.latest is None
        window.connect_source()
        wait(lambda: window.latest is not None)
    finally:
        close_window(window, wait)


class SimulatedSource:
    def __init__(self):
        self.wave = np.linspace(400, 850, 200)
        self.signal = film_reflectance(self.wave, 523.7, load_si_constants())
        self.mode = "sample"
        self.sequence = 0
        self.fail = False
        self.closed = False
        self.read_calls = 0

    def connect(self, settings):
        self.settings = settings
        self.closed = False
        return {"serial": "SIM2048", "name": "Simulation", "pixels": len(self.wave),
                "source_kind": "Avantes USB", "settings": settings, "sdk_version": "simulated"}

    def configure(self, settings): self.settings = settings

    def read(self, cancel):
        if cancel.wait(0.004): raise MeasurementCancelled()
        if self.fail: raise RuntimeError("Simulated USB disconnection")
        self.read_calls += 1
        self.sequence += 1
        if self.mode == "dark": counts = np.full(len(self.wave), 10.)
        elif self.mode == "reference": counts = np.full(len(self.wave), 40010.)
        else: counts = 10 + self.signal*400
        return Frame(self.wave, counts, np.zeros(len(self.wave), bool), "SIM2048", self.settings, self.sequence)

    def stop(self): pass
    def close(self): self.closed = True; return []


def test_calibration_capture_live_resume_setting_change_and_usb_error(qapp, wait):
    source = SimulatedSource()
    window = MainWindow(source_factory=lambda descriptor: source, preferences_path=False, initial_fit_settings=FitSettings())
    window.show()
    try:
        window.connect_source()
        wait(lambda: window.connected and not window.pending)
        wait(lambda: window.last_raw is not None)
        assert window.running  # Acquisition starts on connection, before calibration.
        assert window.latest is None
        source.mode = "dark"
        before = source.read_calls
        window.capture("dark")
        wait(lambda: window.dark_capture is not None)
        assert source.read_calls >= before+5
        assert np.all(window.dark_capture.mean == 10)
        source.mode = "reference"
        window.capture("reference")
        wait(lambda: window.calibration is not None)
        source.mode = "sample"
        wait(lambda: window.latest is not None)
        assert abs(window.latest[2].thickness_nm-523.7) < 0.001
        assert window.running and window.acquirer.running
        window.apply_acquisition(replace(window.acquisition_settings, integration_ms=7))
        assert window.calibration is None and window.latest is None
        wait(lambda: not window.pending and source.settings.integration_ms == 7)
        assert window.latest is None
        source.fail = True
        wait(lambda: not window.connected)
        assert window.latest is None and window.layer_editor.rows[0].fitted.text() == "—"
        assert "Simulated USB disconnection" in window.fit_status.text()
        assert source.closed
    finally:
        close_window(window, wait)


def test_old_fit_results_are_ignored_and_stale_values_cleared(qapp, wait, app_root):
    window = MainWindow(replay_path=next((app_root / "demo").glob("Si-SiO2 antras_*.TXT")), preferences_path=False)
    window.show()
    try:
        wait(lambda: window.latest is not None)
        latest = window.latest
        window.fit_revision += 1
        window.on_event({"kind": "fit", "payload": latest, "generation": window.generation,
                         "fit_revision": window.fit_revision-1})
        assert window.latest is latest
        frame, calibration, result = latest
        window.latest = (replace(frame, captured_at=time.monotonic()-10), calibration, result)
        window.health_tick()
        assert window.latest is None
        assert "Stale" in window.fit_status.text()
        window.stop_measurement()
        wait(lambda: not window.pending)
        window.on_event({"kind": "fit", "payload": latest, "generation": window.generation-1,
                         "fit_revision": window.fit_revision})
        assert window.latest is None
    finally:
        close_window(window, wait)


def test_automatic_connection_compact_controls_and_two_layer_save(qapp, wait, tmp_path):
    from PySide6.QtWidgets import QGroupBox
    source = SimulatedSource()
    source.signal = bilayer_reflectance(source.wave, 523.7, 487.2)
    attempts = []
    def factory(descriptor):
        attempts.append(descriptor)
        if len(attempts) == 1:
            raise RuntimeError("No USB instrument detected")
        return source
    window = MainWindow(source_factory=factory, preferences_path=False)
    window.show()
    try:
        wait(lambda: len(attempts) == 1 and not window.pending)
        assert not window.connected
        window.last_connection_check -= 3
        window.check_connection()
        wait(lambda: window.connected and not window.pending)
        assert len(attempts) == 2
        assert "Spectrum source" not in [g.title() for g in window.findChildren(QGroupBox)]
        assert "Dark and mirror reference" not in [g.title() for g in window.findChildren(QGroupBox)]
        window.set_fit_settings(FitSettings(model="sio2_aln_sapphire", oxide_estimate_nm=800, aln_estimate_nm=200))
        assert window.layer_editor.substrate.currentData() == "sapphire"
        source.mode = "dark"
        window.capture("dark")
        wait(lambda: window.dark_capture is not None)
        assert window.dark_button.property("calibrated")
        source.mode = "reference"
        window.capture("reference")
        wait(lambda: window.calibration is not None)
        assert window.reference_button.property("calibrated")
        source.mode = "sample"
        window.start_measurement()
        wait(lambda: window.latest is not None)
        assert window.layer_editor.rows[0].fitted.text() == "523.7"
        assert window.layer_editor.rows[1].fitted.text() == "487.2"
        assert window.residual_plot.getAxis("left").autoSIPrefixScale == 1
        assert window.map_plot.getAxis("bottom").labelUnitPrefix == ""
        assert window.search_image.image.shape == window.latest[2].search_rmse_pp.shape
        future = window.save_to(tmp_path, "two-layer snapshot")
        wait(lambda: future.done(), timeout=12)
        saved = future.result()
        assert {p.name for p in saved.iterdir()} == {"spectrum.csv", "fit.png"}
        frame, cal, metadata = load_replay(saved / "spectrum.csv")
        settings = FitSettings.from_dict(metadata["fit_settings"])
        restored = CachedFitter().process(frame, cal, settings)
        assert abs(restored.thickness_nm-523.7) < 0.001
        assert abs(restored.aln_thickness_nm-487.2) < 0.001
        assert "AlN" in metadata["optical_sources"]
        assert metadata["result"]["aln_thickness_nm"] == restored.aln_thickness_nm
        window.disconnect_source()
        wait(lambda: not window.pending and not window.connected)
        window.last_connection_check -= 3
        window.check_connection()
        assert len(attempts) == 2  # Explicit disconnect pauses automatic checks.
    finally:
        close_window(window, wait)
    replay = MainWindow(replay_path=saved / "spectrum.csv", preferences_path=False)
    replay.show()
    try:
        wait(lambda: replay.latest is not None)
        assert [l.material for l in replay.fit_settings.layers] == ["SiO2", "AlN"]
        assert replay.layer_editor.rows[1].fitted.text() == "487.2"
        assert replay.layer_editor.rows[0].estimate.value() == 800
        assert "Replay" in replay.fit_status.text()
    finally:
        close_window(replay, wait)


def test_multiple_usb_devices_require_selection_before_retry(qapp, wait):
    source = SimulatedSource()
    attempts = []
    def factory(descriptor):
        attempts.append(descriptor)
        if descriptor["serial"] is None:
            raise DeviceChoiceRequired([{"serial": "SIM2048", "name": "First"}, {"serial": "OTHER", "name": "Second"}])
        return source
    window = MainWindow(source_factory=factory, preferences_path=False)
    window.show()
    try:
        wait(lambda: window.awaiting_device_choice and not window.pending)
        window.last_connection_check -= 10
        window.check_connection()
        assert len(attempts) == 1
        assert window.device_selector.isVisible()
        window.device_selector.setCurrentIndex(1)
        window.select_device(1)
        wait(lambda: window.connected)
        assert attempts[-1]["serial"] == "SIM2048"
        assert not window.device_selector.isVisible()
    finally:
        close_window(window, wait)


def test_material_estimate_and_added_layers_apply_without_an_apply_button(qapp, wait):
    source = SimulatedSource()
    window = MainWindow(source_factory=lambda descriptor: source, preferences_path=False, initial_fit_settings=FitSettings())
    window.show()
    try:
        wait(lambda: window.last_raw is not None)
        assert window.running
        row = window.layer_editor.rows[0]
        row.material.setCurrentIndex(row.material.findData("AlN"))
        assert window.latest is None
        wait(lambda: window.fit_settings.layers[0].material == "AlN" and window.fit_enabled)
        row.estimate.setValue(650)
        wait(lambda: window.fit_settings.layers[0].estimate_nm == 650)
        window.layer_editor.substrate.setCurrentIndex(window.layer_editor.substrate.findData("sapphire"))
        window.layer_editor.add_layer(0, "SiO2")
        wait(lambda: len(window.fit_settings.layers) == 2 and window.fit_enabled)
        assert [l.material for l in window.fit_settings.layers] == ["SiO2", "AlN"]
        window.layer_editor.move(0, 1)
        wait(lambda: window.fit_settings.layers[0].material == "AlN")
        window.layer_editor.remove(1)
        wait(lambda: len(window.fit_settings.layers) == 1)
        assert window.fit_settings.substrate == "sapphire"
    finally:
        close_window(window, wait)


def test_save_dialog_freezes_frame_and_preferences_restore(qapp, wait, app_root, tmp_path):
    from reflectometry_live.panels import SaveDialog
    path = next((app_root / "demo").glob("Si-SiO2 pirmas_*.TXT"))
    preferences = tmp_path/"preferences.json"
    window = MainWindow(replay_path=path, preferences_path=preferences)
    window.show()
    try:
        wait(lambda: window.latest is not None)
        frozen = window.latest
        dialog = SaveDialog(frozen, tmp_path, "wafer X", parent=window)
        wait(lambda: window.latest[0].sequence > frozen[0].sequence)
        assert dialog.snapshot is frozen
        folder, name = dialog.values()
        future = window.save_to(folder, name, snapshot=dialog.snapshot)
        wait(lambda: future.done(), timeout=12)
        _, _, metadata = load_replay(future.result()/"spectrum.csv")
        assert metadata["frame"]["sequence"] == frozen[0].sequence
        assert metadata["result"]["layers"][0]["material"] == "SiO2"
        assert metadata["fit_settings"]["layers"][0]["material"] == "SiO2"
        dialog.deleteLater()
        window.set_preset(("AlN",), "sapphire")
    finally:
        close_window(window, wait)
    restored = MainWindow(source_factory=lambda descriptor: SimulatedSource(), preferences_path=preferences)
    restored.show()
    try:
        assert restored.fit_settings.layers[0].material == "AlN"
        assert restored.layer_editor.substrate.currentData() == "sapphire"
    finally:
        close_window(restored, wait)


def test_invalid_material_interval_can_be_fixed_in_settings(qapp):
    from reflectometry_live.panels import FittingDialog
    invalid = FitSettings(model="stack", layers=(LayerSettings("GaN"),), substrate="sapphire", wavelength_min_nm=250)
    dialog = FittingDialog(invalid)
    try:
        with pytest.raises(ValueError, match="GaN"):
            dialog.values()
        dialog.low.setValue(400)
        assert dialog.values().validate().layers[0].material == "GaN"
    finally:
        dialog.deleteLater()
