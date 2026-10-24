"""Shared published optical constants and reference batch fit, independent of GUI/SDK."""
from pathlib import Path
from functools import lru_cache
import json
import numpy as np
SI_SOURCE = Path(__file__).resolve().parent / "data" / "Si_Green_2008.yml"

@lru_cache(maxsize=1)
def photoresist_record():
    return json.loads((SI_SOURCE.parent / "AZ_ECI_3027_unexposed.json").read_text(encoding="utf-8"))

def photoresist_index(wavelength_nm):
    """Supplier's unbleached AZ ECI 3027 Cauchy reference, not a measured bake recipe."""
    record = photoresist_record()
    wave = np.asarray(wavelength_nm, float)/1000
    low, high = record["application_range_um"]
    if np.any((wave < low) | (wave > high)):
        raise ValueError("Use 500–850 nm for the provisional AZ ECI 3027 transparent-region model.")
    a, b, c = record["cauchy_coefficients"]
    return (a+b/wave**2+c/wave**4).astype(complex)

@lru_cache(maxsize=1)
def transparent_index_records():
    return json.loads((SI_SOURCE.parent / "transparent_indices.json").read_text(encoding="utf-8"))

def ordinary_index(wavelength_nm, material):
    """Published ordinary-ray Sellmeier formula 1; wavelength in micrometres.

    For propagation along the c axis, the transverse electric field samples
    the ordinary index. Visible absorption is neglected in AlN and sapphire.
    """
    record = transparent_index_records()[material]
    wave = np.asarray(wavelength_nm, dtype=float) / 1000
    lo, hi = record["wavelength_range_um"]
    if np.any((wave < lo) | (wave > hi)):
        raise ValueError(f"Outside the published {material} {lo}–{hi} µm range")
    coefficients = record["coefficients"]
    square = wave**2
    index_squared = np.full_like(square, 1 + coefficients[0])
    for amplitude, resonance in zip(coefficients[1::2], coefficients[2::2]):
        index_squared += amplitude * square / (square - resonance**2)
    return np.sqrt(index_squared)

def bilayer_reflectance(wavelength_nm, oxide_nm, aln_nm):
    """Air / SiO2 / c-axis-normal AlN / c-plane sapphire, normal incidence.

    Sapphire is a semi-infinite substrate; backside reflection is omitted.
    Thickness arguments can be arrays with a trailing singleton dimension.
    """
    wave = np.asarray(wavelength_nm, dtype=float)
    oxide, aln = silica_index(wave), ordinary_index(wave, "AlN")
    sapphire = ordinary_index(wave, "sapphire")
    r01, r12, r23 = ((1-oxide)/(1+oxide), (oxide-aln)/(oxide+aln), (aln-sapphire)/(aln+sapphire))
    p1, p2 = np.exp(4j*np.pi*oxide*oxide_nm/wave), np.exp(4j*np.pi*aln*aln_nm/wave)
    below = (r12 + r23*p2) / (1 + r12*r23*p2)
    amplitude = (r01 + below*p1) / (1 + r01*below*p1)
    return 100*np.abs(amplitude)**2

def model_description(model):
    if hasattr(model, "film_layers"):
        materials = [layer.material for layer in model.film_layers()]+[model.substrate_material()]
        description = "Air / " + " / ".join("AZ 3027" if m == "AZ3027E" else m for m in materials) + "; normal incidence; published wavelength-dependent indices; c-plane nitrides and sapphire; no substrate backside reflection"
        if "AZ3027E" in materials:
            description += "; AZ 3027 uses provisional AZ ECI 3027 unexposed Cauchy reference (500–850 nm, k=0 assumed)"
        return description
    if model == "sio2_aln_sapphire":
        return "Air / SiO2 / AlN / sapphire; normal incidence; Malitson SiO2, Pastrnak–Roskovcova AlN, Malitson–Dodge sapphire; ordinary indices; no backside reflection"
    return "Air / SiO2 / Si; normal incidence; Malitson 1965 + Green 2008"

def optical_sources(model):
    if hasattr(model, "film_layers"):
        materials = {layer.material for layer in model.film_layers()} | {model.substrate_material()}
        records = transparent_index_records()
        available = {"SiO2": "https://doi.org/10.1364/JOSA.55.001205",
                     "Si": "https://doi.org/10.1016/j.solmat.2008.06.009",
                     "AlN": records["AlN"]["publication"], "sapphire": records["sapphire"]["dataset"],
                     "GaN": "https://doi.org/10.1049/el:19931172",
                     "AZ3027E": photoresist_record()["source"]}
        return {material: available[material] for material in sorted(materials)}
    sources = {"SiO2": "https://doi.org/10.1364/JOSA.55.001205"}
    if model == "sio2_aln_sapphire":
        records = transparent_index_records()
        sources.update({"AlN": records["AlN"]["publication"], "sapphire": records["sapphire"]["dataset"]})
    else:
        sources["Si"] = "https://doi.org/10.1016/j.solmat.2008.06.009"
    return sources

def load_si_constants(path=SI_SOURCE):
    # Only numeric, three-column rows in the cached YAML are read.
    rows = []
    for line in Path(path).read_text(encoding="utf-8").splitlines():
        fields = line.split()
        if len(fields) == 3:
            try:
                rows.append([float(value) for value in fields])
            except ValueError:
                continue
    table = np.asarray(rows)
    if table.ndim != 2 or table.shape[1] != 3 or len(table) < 2:
        raise ValueError(f"Invalid silicon optical constants: {path}")
    if not np.isfinite(table).all() or np.any(np.diff(table[:, 0]) <= 0):
        raise ValueError("Silicon wavelengths must be finite and increasing")
    return table

@lru_cache(maxsize=1)
def load_gan_constants():
    # Lin et al. ordinary n for transparent wurtzite GaN on c-plane sapphire.
    # The tabulated figure data do not measure k; visible absorption is omitted.
    rows = []
    for line in (SI_SOURCE.parent / "GaN_Lin_1993.yml").read_text(encoding="utf-8").splitlines():
        fields = line.split()
        if len(fields) == 2:
            try:
                rows.append([float(fields[0]), float(fields[1]), 0.])
            except ValueError:
                pass
    table = np.asarray(rows)
    if len(table) < 20 or not np.isfinite(table).all() or np.any(np.diff(table[:, 0]) <= 0):
        raise ValueError("Invalid GaN ordinary-index table")
    return table

def material_index(wavelength_nm, material):
    wave = np.asarray(wavelength_nm, float)
    if material == "SiO2":
        return silica_index(wave).astype(complex)
    if material == "AZ3027E":
        return photoresist_index(wave)
    if material in ("AlN", "sapphire"):
        return ordinary_index(wave, material).astype(complex)
    if material == "Si":
        table = load_si_constants()
    elif material == "GaN":
        table = load_gan_constants()
    else:
        raise ValueError(f"Unknown material: {material}")
    if np.any((wave < table[0, 0]*1000) | (wave > table[-1, 0]*1000)):
        raise ValueError(f"Outside the bundled {material} wavelength range")
    return np.interp(wave/1000, table[:, 0], table[:, 1])+1j*np.interp(wave/1000, table[:, 0], table[:, 2])

class StackOptics:
    """Recursive Fresnel amplitudes and analytic derivatives for ordered films."""
    def __init__(self, wave, materials, substrate):
        wave = np.asarray(wave, float)
        indices = [np.ones_like(wave, complex)]+[material_index(wave, m) for m in materials]+[material_index(wave, substrate)]
        self.indices = indices[1:-1]
        self.interfaces = [(a-b)/(a+b) for a, b in zip(indices[:-1], indices[1:])]
        self.phase_rates = [4j*np.pi*n/wave for n in self.indices]

    def reflectance(self, thicknesses, derivatives=False):
        thicknesses = np.asarray(thicknesses, float)
        batched = thicknesses.ndim == 2
        amplitude = self.interfaces[-1]
        jac = np.zeros((len(amplitude), len(self.indices)), complex) if derivatives else None
        for layer in reversed(range(len(self.indices))):
            phase = np.exp(self.phase_rates[layer]*(thicknesses[:, layer, None] if batched else thicknesses[layer]))
            interface = self.interfaces[layer]
            denominator = 1+interface*amplitude*phase
            factor = (1-interface**2)/denominator**2
            if derivatives:
                jac = phase[:, None]*factor[:, None]*jac
                jac[:, layer] += amplitude*factor*phase*self.phase_rates[layer]
            amplitude = (interface+amplitude*phase)/denominator
        reflectance = 100*np.abs(amplitude)**2
        if derivatives:
            return reflectance, 200*np.real(np.conj(amplitude)[:, None]*jac)
        return reflectance

def stack_reflectance(wavelength_nm, materials, thicknesses_nm, substrate):
    return StackOptics(wavelength_nm, materials, substrate).reflectance(thicknesses_nm)

def silica_index(wavelength_nm):
    """Malitson fused silica; convert nm to micrometres for Sellmeier."""
    wave_um = np.asarray(wavelength_nm, dtype=float) / 1000
    if np.any((wave_um < 0.21) | (wave_um > 3.71)):
        raise ValueError("Outside the Malitson 0.21-3.71 um range")
    square = wave_um**2
    return np.sqrt(
        1
        + 0.6961663 * square / (square - 0.0684043**2)
        + 0.4079426 * square / (square - 0.1162414**2)
        + 0.8974794 * square / (square - 9.896161**2)
    )

def film_reflectance(wavelength_nm, thickness_nm, si_table, n633=None):
    """Coherent single film on a semi-infinite Si substrate, normal incidence.

    n633 rescales silica dispersion to a chosen oxide index at 633 nm.
    This is a sensitivity model, not a measured PECVD dispersion relation.
    """
    wave = np.asarray(wavelength_nm, dtype=float)
    if np.any((wave < 1000 * si_table[0, 0]) | (wave > 1000 * si_table[-1, 0])):
        raise ValueError("Wavelengths outside cached Si optical constants")
    oxide = silica_index(wave)
    if n633 is not None:
        oxide = oxide * n633 / float(silica_index(633.0))
    silicon = np.interp(wave / 1000, si_table[:, 0], si_table[:, 1]) + 1j * np.interp(
        wave / 1000, si_table[:, 0], si_table[:, 2]
    )
    r01 = (1 - oxide) / (1 + oxide)
    r12 = (oxide - silicon) / (oxide + silicon)
    phase = np.exp(4j * np.pi * oxide * thickness_nm / wave)
    amplitude = (r01 + r12 * phase) / (1 + r01 * r12 * phase)
    return 100 * np.abs(amplitude) ** 2

def fit_spectrum(wave, signal, si_table, n633, bounds=(100.0, 1200.0), affine=True):
    """Search a broad thickness grid, then refine its best minimum.

    Profile out a positive amplitude scale and a constant offset. A 500 nm
    starting guess is never used. A thickness-only fit is available as a
    diagnostic, but is not an absolute fit to a calibrated Al reference.
    """
    from scipy.optimize import minimize_scalar
    if len(wave) < 20:
        raise ValueError("Too few points in fit interval")

    def evaluate(thickness):
        physical = film_reflectance(wave, thickness, si_table, n633)
        if affine:
            design = np.column_stack([physical, np.ones(len(wave))])
            scale, offset = np.linalg.lstsq(design, signal, rcond=None)[0]
            if scale <= 0:
                return float("inf"), scale, offset, physical
        else:
            scale, offset = 1.0, 0.0
        prediction = scale * physical + offset
        mse = float(np.mean((signal - prediction) ** 2))
        return mse, float(scale), float(offset), prediction

    grid = np.linspace(bounds[0], bounds[1], int((bounds[1] - bounds[0]) / 2) + 1)
    loss = np.asarray([evaluate(thickness)[0] for thickness in grid])
    winner = int(np.argmin(loss))
    if winner in (0, len(grid) - 1):
        raise RuntimeError("Minimum at search boundary; expand thickness bounds")
    optimum = minimize_scalar(
        lambda thickness: evaluate(thickness)[0],
        bounds=(grid[winner - 1], grid[winner + 1]),
        method="bounded",
        options={"xatol": 1e-6},
    )
    if not optimum.success:
        raise RuntimeError(optimum.message)
    mse, scale, offset, prediction = evaluate(optimum.x)
    result = {
        "thickness_nm": float(optimum.x),
        "n633_assumed": n633,
        "scale": scale,
        "offset_percentage_points": offset,
        "rmse_percentage_points": float(np.sqrt(mse)),
        "wavelength_min_nm": float(wave.min()),
        "wavelength_max_nm": float(wave.max()),
        "points": len(wave),
    }
    return result, prediction, grid, loss
