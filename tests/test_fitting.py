from dataclasses import replace

import numpy as np
import pytest

from reflectometry_live.domain import AcquisitionSettings, Calibration, FitSettings, Frame
from reflectometry_live.fitting import CachedFitter, FitRejected
from reflectometry_live.io import load_replay
from reflectometry_live.optics import film_reflectance, fit_spectrum, load_si_constants, silica_index


def synthetic(thickness=523.7):
    wave = np.linspace(400, 850, 401)
    signal = 1.07 * film_reflectance(wave, thickness, load_si_constants()) + 1.3
    settings = AcquisitionSettings()
    frame = Frame(wave, 10 + signal * 400, np.zeros(len(wave), bool), "TEST", settings, 1)
    calibration = Calibration(wave, np.full(len(wave), 10), np.full(len(wave), 40010),
                              np.zeros(len(wave)), np.zeros(len(wave)), np.zeros(len(wave), bool),
                              "TEST", settings, "dark", "reference")
    return frame, calibration


@pytest.mark.parametrize("name,expected", [("pirmas", 528.207), ("antras", 513.865), ("trecias", 506.040)])
def test_measured_regression(app_root, name, expected):
    path = next((app_root / "demo").glob(f"Si-SiO2 {name}_*.TXT"))
    frame, calibration, _ = load_replay(path)
    result = CachedFitter().process(frame, calibration)
    assert abs(result.thickness_nm - expected) < 0.01
    assert result.status == "Good"
    assert len(frame.counts) == 1624
    assert len(result.wavelength_nm) == 791


@pytest.mark.parametrize("thickness", [350.4, 523.7, 871.2])
def test_known_thickness_and_reference_search(thickness):
    frame, calibration = synthetic(thickness)
    fitter = CachedFitter()
    result = fitter.process(frame, calibration)
    assert abs(result.thickness_nm - thickness) < 0.001
    assert abs(result.scale - 1.07) < 1e-5
    assert abs(result.offset_pp - 1.3) < 1e-4
    reference, _, grid, mse = fit_spectrum(result.wavelength_nm, result.measured, load_si_constants(),
                                          float(silica_index(633)))
    assert abs(result.thickness_nm - reference["thickness_nm"]) < 1e-4
    valid = np.isfinite(mse)
    assert np.max(np.abs(result.grid_rmse_pp[valid]**2 - mse[valid])) < 1e-9
    fitter.process(frame, calibration)
    assert fitter.cache_builds == 1
    fitter.process(frame, calibration, FitSettings(wavelength_min_nm=450))
    assert fitter.cache_builds == 2


def test_bare_si_limit_and_dispersion():
    si = load_si_constants()
    wave = np.linspace(400, 850, 200)
    index = np.interp(wave/1000, si[:, 0], si[:, 1]) + 1j*np.interp(wave/1000, si[:, 0], si[:, 2])
    assert np.max(np.abs(film_reflectance(wave, 0, si) - 100*np.abs((1-index)/(1+index))**2)) < 1e-10
    assert float(silica_index(400)) > float(silica_index(850))


@pytest.mark.parametrize("change", ["exposure", "device", "wavelength", "adc", "smoothing"])
def test_calibration_mismatch(change):
    frame, cal = synthetic()
    if change == "exposure": frame = replace(frame, settings=replace(frame.settings, integration_ms=7))
    if change == "device": frame = replace(frame, device_serial="OTHER")
    if change == "wavelength": frame = replace(frame, wavelength_nm=frame.wavelength_nm+0.01)
    if change == "adc": frame = replace(frame, settings=replace(frame.settings, high_res_adc=False))
    if change == "smoothing": frame = replace(frame, settings=replace(frame.settings, smoothing_pixels=3))
    with pytest.raises(FitRejected, match="Calibration does not match"):
        CachedFitter().process(frame, cal)


def test_averaging_keeps_mean_count_calibration():
    frame, cal = synthetic()
    frame = replace(frame, settings=replace(frame.settings, averages=5))
    assert abs(CachedFitter().process(frame, cal).thickness_nm-523.7) < 0.001


def test_weak_reference_saturation_and_nonfinite():
    frame, cal = synthetic()
    fitter = CachedFitter()
    with pytest.raises(FitRejected, match="Too few usable"):
        fitter.process(frame, replace(cal, reference=cal.dark+1))
    flags = frame.saturated.copy()
    flags[80] = True
    with pytest.raises(FitRejected, match="Saturation"):
        fitter.process(replace(frame, saturated=flags), cal)
    with pytest.raises(FitRejected, match="Saturation"):
        fitter.process(frame, replace(cal, saturated=flags))
    counts = frame.counts.copy()
    counts[80] = np.nan
    result = fitter.process(replace(frame, counts=counts), cal)
    assert len(result.wavelength_nm) == len(frame.counts)-1
    assert abs(result.thickness_nm-523.7) < 0.001


def test_quality_states():
    fitter = CachedFitter()
    wave = np.linspace(400, 850, 401)
    with pytest.raises(FitRejected, match="Flat"):
        fitter.fit_signal(wave, np.full(len(wave), 20))
    assert fitter.fit_signal(wave, film_reflectance(wave, 100, load_si_constants())).status == "Boundary"
    rng = np.random.default_rng(17)
    noisy = film_reflectance(wave, 500, load_si_constants()) + rng.normal(0, 4, len(wave))
    assert fitter.fit_signal(wave, noisy).status == "Poor fit"
    narrow = np.linspace(600, 610, 101)
    result = fitter.fit_signal(narrow, film_reflectance(narrow, 500, load_si_constants()),
                               FitSettings(wavelength_min_nm=600, wavelength_max_nm=610))
    assert result.status == "Ambiguous"


def test_frame_owns_immutable_arrays():
    frame, _ = synthetic()
    original = np.arange(401, dtype=float)
    frame = replace(frame, counts=original)
    original[:] = 0
    assert frame.counts[-1] == 400
    with pytest.raises(ValueError):
        frame.counts[0] = 12
