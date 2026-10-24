from dataclasses import replace

import numpy as np
import pytest
from PySide6.QtCore import Qt
from PySide6.QtTest import QTest

from reflectometry_live.domain import AcquisitionSettings, Calibration, FitSettings, Frame, LayerSettings
from reflectometry_live.fitting import CachedFitter
from reflectometry_live.gui import MainWindow
from reflectometry_live.io import load_replay, write_snapshot
from reflectometry_live.optics import material_index, photoresist_record, stack_reflectance


def test_joint_statistical_error_matches_numeric_jacobian_and_nuisance_covariance():
    wave = np.linspace(400, 850, 601)
    materials, truth = ("SiO2", "AlN"), (520., 580.)
    settings = FitSettings(model="sio2_aln_sapphire").as_stack()
    random = np.random.default_rng(42)
    signal = .82*stack_reflectance(wave, materials, truth, "sapphire")+7.9+random.normal(0, .12, len(wave))
    fitter = CachedFitter()
    result = fitter.fit_signal(wave, signal, settings)
    assert result.status == "Good"
    values = result.layer_values()
    derivative = []
    for i in range(2):
        plus, minus = values.copy(), values.copy()
        plus[i] += .001; minus[i] -= .001
        derivative.append(result.scale*(stack_reflectance(wave, materials, plus, "sapphire")-
                                        stack_reflectance(wave, materials, minus, "sapphire"))/.002)
    physical = stack_reflectance(wave, materials, values, "sapphire")
    numeric_jac = np.column_stack((*derivative, physical, np.ones(len(wave))))
    variance = result.residual @ result.residual/(len(wave)-4)
    expected = np.linalg.inv(numeric_jac.T@numeric_jac)*variance
    np.testing.assert_allclose(result.thickness_covariance_nm2, expected[:2, :2], rtol=1e-6)
    np.testing.assert_allclose(result.layer_std_nm, np.sqrt(np.diag(expected)[:2]), rtol=1e-6)
    assert not result.layer_std_nm.flags.writeable
    assert all(0 < x < 1 for x in result.layer_std_nm)
    assert "independent residual noise" in result.summary()["uncertainty"]
    # A reused model cache still recomputes the statistical error for each frame.
    noisier = fitter.fit_signal(wave, result.prediction+2*result.residual, settings)
    np.testing.assert_allclose(noisier.layer_std_nm/result.layer_std_nm, 2, rtol=.01)


def test_unreliable_solutions_have_no_numeric_error_bars():
    wave = np.linspace(400, 850, 201)
    layers = (LayerSettings("SiO2", 500, 100, 1200, True),)*2
    settings = FitSettings(model="stack", layers=layers, substrate="Si")
    signal = stack_reflectance(wave, ("SiO2", "SiO2"), (500, 500), "Si")
    result = CachedFitter().fit_signal(wave, signal, settings)
    assert result.status == "Ambiguous"
    assert np.isnan(result.layer_std_nm).all()
    assert all(row["fit_std_nm"] is None for row in result.summary()["layers"])
    poor = CachedFitter._with_uncertainty(replace(result, status="Poor fit"))
    assert np.isnan(poor.layer_std_nm).all()


@pytest.mark.parametrize("materials,truth,substrate", [(("AZ3027E",), (2734.2,), "Si"),
                                                    (("AZ3027E", "GaN"), (2694.3, 638.2), "sapphire")])
def test_unexposed_photoresist_synthetic_recovery_and_reference(materials, truth, substrate):
    wave = np.linspace(500, 850, 351)
    layers = tuple(LayerSettings(m, d, d-250, d+250, True) for m, d in zip(materials, truth))
    settings = FitSettings(500, 850, model="stack", layers=layers, substrate=substrate)
    signal = .94*stack_reflectance(wave, materials, truth, substrate)+1.7
    fitter = CachedFitter()
    result = fitter.fit_signal(wave, signal, settings)
    np.testing.assert_allclose(result.layer_values(), truth, atol=.001)
    assert result.status == "Good"
    assert "provisional AZ ECI 3027" in result.summary()["model"]
    record = photoresist_record()
    assert record["cauchy_coefficients"] == [1.6018, .009896, .000886]
    expected = 1.6018+.009896/.633**2+.000886/.633**4
    assert material_index(633, "AZ3027E").real == pytest.approx(expected)
    assert material_index(633, "AZ3027E").imag == 0
    with pytest.raises(ValueError, match="500"):
        replace(settings, wavelength_min_nm=400).validate()
    with pytest.raises(ValueError, match="500"):
        material_index(435, "AZ3027E")


def test_photoresist_snapshot_reproduces_fit_and_statistical_error(tmp_path):
    wave = np.linspace(500, 850, 251)
    settings = FitSettings(500, 850, model="stack", layers=(LayerSettings("AZ3027E", 2700, 2400, 3000, True),), substrate="Si")
    reflectance = stack_reflectance(wave, ("AZ3027E",), (2734.2,), "Si")+np.random.default_rng(1).normal(0, .1, len(wave))
    acquisition = AcquisitionSettings()
    frame = Frame(wave, 10+400*reflectance, np.zeros(len(wave), bool), "SIM", acquisition, 1)
    cal = Calibration(wave, np.full(len(wave), 10.), np.full(len(wave), 40010.), np.zeros(len(wave)),
                      np.zeros(len(wave)), np.zeros(len(wave), bool), "SIM", acquisition, None, None)
    result = CachedFitter().process(frame, cal, settings)
    saved = write_snapshot(tmp_path, "soft baked resist", frame, cal, result)
    restored_frame, restored_cal, metadata = load_replay(saved/"spectrum.csv")
    restored = CachedFitter().process(restored_frame, restored_cal, FitSettings.from_dict(metadata["fit_settings"]))
    np.testing.assert_allclose(restored.layer_std_nm, result.layer_std_nm, atol=1e-10)
    np.testing.assert_allclose(restored.layer_values(), result.layer_values(), atol=1e-5)
    assert metadata["result"]["layers"][0]["fit_std_nm"] == pytest.approx(float(result.layer_std_nm[0]))


def test_fullscreen_direct_layer_buttons_error_display_and_resist_selection(qapp, wait, app_root):
    path = next((app_root/"demo").glob("Si-SiO2 pirmas_*.TXT"))
    window = MainWindow(replay_path=path, preferences_path=False)
    window.showFullScreen()
    try:
        wait(lambda: window.latest is not None)
        assert window.isFullScreen() and window.fullscreen_action.isChecked()
        assert not window.windowIcon().isNull()
        row = window.layer_editor.rows[0]
        assert row.uncertainty.text().startswith("±")
        assert "not total measurement accuracy" in row.uncertainty.toolTip()
        assert not row.move_up.isEnabled() and not row.move_down.isEnabled() and not row.remove_button.isEnabled()
        QTest.keyClick(window, Qt.Key.Key_Escape)
        assert not window.isFullScreen()
        window.fullscreen_action.trigger()
        assert window.isFullScreen()
        window.stop_measurement()
        wait(lambda: not window.pending)
        window.layer_editor.add_layer(0, "AlN")
        assert window.latest is None and all(r.uncertainty.text() == "—" for r in window.layer_editor.rows)
        window.layer_editor.rows[0].move_down.click()
        assert [r.layer.material for r in window.layer_editor.rows] == ["SiO2", "AlN"]
        window.layer_editor.rows[1].move_up.click()
        assert [r.layer.material for r in window.layer_editor.rows] == ["AlN", "SiO2"]
        window.layer_editor.rows[0].remove_button.click()
        assert len(window.layer_editor.rows) == 1
        row = window.layer_editor.rows[0]
        row.material.setCurrentIndex(row.material.findData("AZ3027E"))
        row.estimate.setValue(2700)
        wait(lambda: window.fit_settings.layers[0].material == "AZ3027E")
        assert window.fit_settings.wavelength_min_nm == 500
        assert window.fit_enabled
    finally:
        window.close()
        wait(lambda: window.allow_close and not window.acquirer.is_alive() and not window.fitter.is_alive(), timeout=12)
