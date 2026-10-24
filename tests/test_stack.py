from dataclasses import asdict, replace
import numpy as np
import pytest
from reflectometry_live.domain import FitSettings, LayerSettings
from reflectometry_live.fitting import CachedFitter
from reflectometry_live.optics import StackOptics, bilayer_reflectance, material_index, stack_reflectance


def settings(materials, substrate="sapphire", estimates=None):
    return FitSettings(model="stack", layers=tuple(LayerSettings(m, e, 100, 1200, True) for m, e in
                       zip(materials, estimates or [500]*len(materials))), substrate=substrate)


@pytest.mark.parametrize("materials,thicknesses", [(("GaN",), (738.4,)), (("AlN",), (523.7,)),
                                                   (("AlN", "SiO2"), (587.4, 508.3))])
def test_nitride_and_reverse_stacks(materials, thicknesses):
    wave = np.linspace(400, 850, 401)
    signal = 1.07*stack_reflectance(wave, materials, thicknesses, "sapphire")+1.3
    result = CachedFitter().fit_signal(wave, signal, settings(materials))
    np.testing.assert_allclose(result.layer_thicknesses_nm, thicknesses, atol=.001)
    assert result.status == "Good"
    assert result.settings.film_layers()[0].material == materials[0]
    assert result.summary()["layers"][0]["material"] == materials[0]
    if materials == ("GaN",):
        assert result.summary()["sio2_thickness_nm"] is None


def test_three_films_estimates_and_sampled_search_are_explicit():
    wave = np.linspace(400, 850, 301)
    materials, truth = ("SiO2", "AlN", "GaN"), (501.3, 487.2, 913.7)
    signal = stack_reflectance(wave, materials, truth, "sapphire")
    fit = CachedFitter().fit_signal(wave, signal, settings(materials, estimates=(450, 550, 950)))
    np.testing.assert_allclose(fit.layer_thicknesses_nm, truth, atol=.001)
    assert fit.status == "Good"
    assert fit.search_kind == "sobol_multistart"
    assert fit.search_points_nm.shape == (4608, 3)
    assert "not an exhaustive" in fit.message


def test_general_reflectance_derivatives_and_identical_layers():
    wave = np.linspace(400, 850, 100)
    np.testing.assert_allclose(stack_reflectance(wave, ("SiO2", "AlN"), (508, 587), "sapphire"),
                               bilayer_reflectance(wave, 508, 587), atol=1e-12)
    optics = StackOptics(wave, ("SiO2", "AlN", "GaN"), "sapphire")
    pair = np.array([508., 587., 930.])
    _, jac = optics.reflectance(pair, derivatives=True)
    for i in range(3):
        plus, minus = pair.copy(), pair.copy(); plus[i] += .001; minus[i] -= .001
        numeric = (optics.reflectance(plus)-optics.reflectance(minus))/.002
        np.testing.assert_allclose(jac[:, i], numeric, atol=1e-8, rtol=1e-6)
    assert np.real(material_index(400, "GaN")) > np.real(material_index(850, "GaN"))
    assert np.imag(material_index(400, "GaN")) >= 0
    same = settings(("SiO2", "SiO2"), "Si")
    result = CachedFitter().fit_signal(wave, stack_reflectance(wave, ("SiO2", "SiO2"), (500, 500), "Si"), same)
    assert result.status == "Ambiguous"


def test_stack_metadata_and_estimate_bounds_roundtrip():
    old = FitSettings(model="sio2_aln_sapphire").as_stack()
    restored = FitSettings.from_dict(asdict(old))
    assert restored == old
    assert restored.layers[0].bounds_manual
    auto = LayerSettings("GaN").with_estimate(3000)
    assert auto.minimum_nm < 3000 < auto.maximum_nm
    assert auto.maximum_nm > 1200
    manual = replace(auto, bounds_manual=True).with_estimate(3200)
    assert manual.minimum_nm == auto.minimum_nm


def test_saved_october_measurement_uses_wrong_stack_and_corrected_stack_recovers(app_root):
    from reflectometry_live.io import load_replay
    path = app_root/"tests"/"20261008T093852.952227Z_measurement"/"metadata.json"
    frame, cal, metadata = load_replay(path)
    assert metadata["fit_settings"]["model"] == "sio2_si"
    assert frame.timestamp_utc == metadata["frame"]["timestamp_utc"]
    assert frame.sdk_timestamp == metadata["frame"]["sdk_timestamp"]
    correct = FitSettings(model="sio2_aln_sapphire").as_stack()
    result = CachedFitter().process(frame, cal, correct)
    assert result.status == "Good"
    assert result.thickness_nm == pytest.approx(508.270, abs=.01)
    assert result.aln_thickness_nm == pytest.approx(587.414, abs=.01)
    assert result.rmse_pp < 1
    assert result.rmse_pp < metadata["result"]["rmse_percentage_points"]/2
