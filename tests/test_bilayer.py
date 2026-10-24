from dataclasses import replace
import numpy as np
import pytest

from reflectometry_live.bilayer import BilayerSearch
from reflectometry_live.domain import FitSettings
from reflectometry_live.fitting import CachedFitter, FitRejected
from reflectometry_live.optics import bilayer_reflectance, ordinary_index, silica_index


SETTINGS = FitSettings(model="sio2_aln_sapphire", oxide_estimate_nm=800, aln_estimate_nm=200)


@pytest.mark.parametrize("oxide,aln", [(500., 500.), (528.2, 486.7), (350.4, 750.2), (801.3, 301.6)])
def test_recover_both_layers_with_wrong_manual_estimates(oxide, aln):
    wave = np.linspace(400, 850, 401)
    signal = 1.07*bilayer_reflectance(wave, oxide, aln)+1.3
    fitter = CachedFitter()
    result = fitter.fit_signal(wave, signal, SETTINGS)
    assert abs(result.thickness_nm-oxide) < 0.001
    assert abs(result.aln_thickness_nm-aln) < 0.001
    assert abs(result.scale-1.07) < 1e-6
    assert abs(result.offset_pp-1.3) < 1e-5
    assert result.status == "Good"
    fitter.fit_signal(wave, signal, replace(SETTINGS, oxide_estimate_nm=400))
    assert fitter.bilayer.cache_builds == 1
    fitter.fit_signal(wave, signal, replace(SETTINGS, aln_max_nm=1000))
    assert fitter.bilayer.cache_builds == 2


def test_reflection_against_independent_characteristic_matrix_and_limits():
    wave = np.linspace(400, 850, 100)
    n1, n2, ns = silica_index(wave), ordinary_index(wave, "AlN"), ordinary_index(wave, "sapphire")
    for d1, d2 in [(500, 500), (0, 500), (500, 0), (0, 0)]:
        product = np.tile(np.eye(2, dtype=complex), (len(wave), 1, 1))
        for n, thickness in [(n1, d1), (n2, d2)]:
            phase = 2*np.pi*n*thickness/wave
            matrix = np.zeros_like(product)
            matrix[:, 0, 0] = matrix[:, 1, 1] = np.cos(phase)
            matrix[:, 0, 1] = 1j*np.sin(phase)/n
            matrix[:, 1, 0] = 1j*n*np.sin(phase)
            product = product@matrix
        admittance = (product[:, 1, 0]+product[:, 1, 1]*ns)/(product[:, 0, 0]+product[:, 0, 1]*ns)
        reference = 100*np.abs((1-admittance)/(1+admittance))**2
        np.testing.assert_allclose(bilayer_reflectance(wave, d1, d2), reference, atol=1e-12)
    np.testing.assert_allclose(bilayer_reflectance(wave, 0, 0), 100*((1-ns)/(1+ns))**2, atol=1e-12)
    assert ordinary_index(400, "AlN") > ordinary_index(850, "AlN")
    assert ordinary_index(400, "sapphire") > ordinary_index(850, "sapphire")


def test_cached_grid_and_derivative_match_independent_evaluation():
    wave = np.linspace(400, 850, 200)
    settings = replace(SETTINGS, thickness_min_nm=400, thickness_max_nm=650, oxide_estimate_nm=500,
                       aln_min_nm=350, aln_max_nm=650, aln_estimate_nm=500)
    search = BilayerSearch()
    search.prepare(wave, settings)
    model, jac = search.physical([523.4, 487.2])
    np.testing.assert_allclose(model, bilayer_reflectance(wave, 523.4, 487.2), atol=1e-12)
    delta = 1e-3
    for layer in (0, 1):
        pair1 = np.array([523.4, 487.2]); pair1[layer] += delta
        pair2 = np.array([523.4, 487.2]); pair2[layer] -= delta
        numeric = (bilayer_reflectance(wave, *pair1)-bilayer_reflectance(wave, *pair2))/(2*delta)
        np.testing.assert_allclose(jac[:, layer], numeric, atol=1e-9, rtol=1e-7)
    signal = 1.1*model+0.7
    result = search.fit(wave, signal, settings)
    for i, j in [(0, 0), (4, 9), (len(search.grid)-1, len(search.aln_grid)-1)]:
        trial = bilayer_reflectance(wave, search.grid[i], search.aln_grid[j])
        a, b = np.linalg.lstsq(np.column_stack([trial, np.ones(len(wave))]), signal, rcond=None)[0]
        expected = np.sqrt(np.mean((signal-a*trial-b)**2)) if a > 0 else np.inf
        assert result["search_rmse_pp"][i, j] == pytest.approx(expected, abs=1e-8)


def test_bilayer_quality_checks_and_search_limits():
    wave = np.linspace(400, 850, 401)
    fitter = CachedFitter()
    assert fitter.fit_signal(wave, bilayer_reflectance(wave, 100, 500), SETTINGS).status == "Boundary"
    assert fitter.fit_signal(wave, bilayer_reflectance(wave, 500, 100), SETTINGS).status == "Boundary"
    narrow = np.linspace(600, 605, 60)
    assert fitter.fit_signal(narrow, bilayer_reflectance(narrow, 500, 500), SETTINGS).status == "Ambiguous"
    rng = np.random.default_rng(9)
    noisy = bilayer_reflectance(wave, 500, 500)+rng.normal(0, 3, len(wave))
    assert fitter.fit_signal(wave, noisy, SETTINGS).status == "Poor fit"
    with pytest.raises(FitRejected, match="Flat"):
        fitter.fit_signal(wave, np.full(len(wave), 10), SETTINGS)
    with pytest.raises(FitRejected, match="too large"):
        other_wave = np.linspace(400, 850, 101)
        fitter.fit_signal(other_wave, bilayer_reflectance(other_wave, 500, 500),
                          replace(SETTINGS, thickness_max_nm=10000, aln_max_nm=10000))
    restored = fitter.fit_signal(wave, bilayer_reflectance(wave, 500, 500), SETTINGS)
    assert restored.thickness_nm == pytest.approx(500, abs=0.001)
    assert restored.aln_thickness_nm == pytest.approx(500, abs=0.001)
    with pytest.raises(ValueError, match="rough AlN"):
        replace(SETTINGS, aln_estimate_nm=1500).validate()


def test_cached_refinement_tracks_a_changed_thickness_and_signal():
    wave = np.linspace(400, 850, 401)
    fitter = CachedFitter()
    for oxide, aln, scale, offset in [(500, 500, 1., 0.), (571.3, 443.2, 1.2, 2.), (350.4, 811.7, .9, -1.)]:
        signal = scale*bilayer_reflectance(wave, oxide, aln)+offset
        result = fitter.fit_signal(wave, signal, SETTINGS)
        assert result.thickness_nm == pytest.approx(oxide, abs=.001)
        assert result.aln_thickness_nm == pytest.approx(aln, abs=.001)
        assert result.scale == pytest.approx(scale, abs=1e-6)
    assert fitter.bilayer.cache_builds == 1
