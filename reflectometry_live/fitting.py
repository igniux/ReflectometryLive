"""Full cached thickness search, affine calibration and quality diagnostics."""

from dataclasses import dataclass, field, replace
import time

import numpy as np
from scipy.optimize import minimize_scalar

from .domain import FitSettings, frozen_array
from .optics import film_reflectance, load_si_constants, silica_index, model_description, StackOptics
from .bilayer import BilayerSearch
from .stack_fitting import StackSearch


class FitRejected(ValueError):
    pass


@dataclass(frozen=True)
class FitResult:
    thickness_nm: float
    scale: float
    offset_pp: float
    rmse_pp: float
    status: str
    message: str
    wavelength_nm: np.ndarray
    measured: np.ndarray
    prediction: np.ndarray
    residual: np.ndarray
    reflectance_full: np.ndarray
    valid_mask: np.ndarray
    grid_nm: np.ndarray
    grid_rmse_pp: np.ndarray
    settings: FitSettings
    fit_time_ms: float
    aln_thickness_nm: float | None = None
    aln_grid_nm: np.ndarray = field(default_factory=lambda: np.empty(0))
    aln_grid_rmse_pp: np.ndarray = field(default_factory=lambda: np.empty(0))
    search_rmse_pp: np.ndarray = field(default_factory=lambda: np.empty((0, 0)))
    candidates: tuple = ()
    layer_thicknesses_nm: np.ndarray = field(default_factory=lambda: np.empty(0))
    layer_materials: tuple = ()
    layer_grids_nm: tuple = ()
    layer_profiles_rmse_pp: tuple = ()
    search_points_nm: np.ndarray = field(default_factory=lambda: np.empty((0, 0)))
    search_errors_pp: np.ndarray = field(default_factory=lambda: np.empty(0))
    search_kind: str = "full_grid"
    layer_std_nm: np.ndarray = field(default_factory=lambda: np.empty(0))
    thickness_covariance_nm2: np.ndarray = field(default_factory=lambda: np.empty((0, 0)))

    def __post_init__(self):
        for name in ("wavelength_nm", "measured", "prediction", "residual", "reflectance_full",
                     "valid_mask", "grid_nm", "grid_rmse_pp", "aln_grid_nm", "aln_grid_rmse_pp", "search_rmse_pp",
                     "layer_thicknesses_nm", "search_points_nm", "search_errors_pp", "layer_std_nm", "thickness_covariance_nm2"):
            object.__setattr__(self, name, frozen_array(getattr(self, name), bool if name == "valid_mask" else float))
        for name in ("layer_grids_nm", "layer_profiles_rmse_pp"):
            object.__setattr__(self, name, tuple(frozen_array(v) for v in getattr(self, name)))

    def layer_values(self):
        if len(self.layer_thicknesses_nm):
            return self.layer_thicknesses_nm
        return np.array([self.thickness_nm]+([self.aln_thickness_nm] if self.aln_thickness_nm is not None else []))

    def summary(self):
        layers = self.settings.film_layers()
        values = self.layer_values()
        errors = [float(x) if np.isfinite(x) else None for x in self.layer_std_nm]
        oxide = next((float(value) for layer, value in zip(layers, values) if layer.material == "SiO2"), None)
        return {
            "thickness_nm": self.thickness_nm, "scale": self.scale,
            "sio2_thickness_nm": oxide, "aln_thickness_nm": self.aln_thickness_nm,
            "layers": [{"material": layer.material, "thickness_nm": float(value),
                        "fit_std_nm": errors[i] if i < len(errors) else None} for i, (layer, value) in enumerate(zip(layers, values))],
            "thickness_covariance_nm2": self.thickness_covariance_nm2.tolist() if np.isfinite(self.thickness_covariance_nm2).all() else None,
            "substrate": self.settings.substrate_material(), "search_kind": self.search_kind,
            "refined_layer_pairs": self.candidates,
            "offset_percentage_points": self.offset_pp, "rmse_percentage_points": self.rmse_pp,
            "status": self.status, "message": self.message, "points": len(self.wavelength_nm),
            "fit_time_ms": self.fit_time_ms,
            "oxide_n633": float(silica_index(633)),
            "model": model_description(self.settings),
            "signal": "100*(sample-dark)/(reference-dark), aluminium mirror reference",
            "uncertainty": "Approximate local 1-sigma statistical fit error from the full thickness/amplitude/offset Jacobian and residual variance, assuming independent residual noise. Conditional on the optical model; excludes refractive-index, calibration and model errors. No error is reported for poor, ambiguous, boundary or ill-conditioned fits. RMSE is signal error, not thickness uncertainty.",
        }


class CachedFitter:
    def __init__(self):
        self.si = load_si_constants()
        self._key = None
        self.cache_builds = 0
        self.bilayer = BilayerSearch()
        self.stack = StackSearch()

    def _prepare(self, wave, settings):
        key = (wave.tobytes(), settings.thickness_min_nm, settings.thickness_max_nm)
        if key == self._key:
            return
        self.wave = wave.copy()
        self.grid = np.linspace(settings.thickness_min_nm, settings.thickness_max_nm,
                                int(np.ceil((settings.thickness_max_nm - settings.thickness_min_nm) / 2)) + 1)
        self.models = np.stack([film_reflectance(wave, d, self.si) for d in self.grid])
        self.means = self.models.mean(axis=1)
        self.centered = self.models - self.means[:, None]
        self.variance = np.einsum("ij,ij->i", self.centered, self.centered)
        self._key = key
        self.cache_builds += 1

    def fit_signal(self, wave, signal, settings=None):
        """Fit and estimate conditional statistical uncertainty, without changing the optimum."""
        start = time.perf_counter()
        result = self._fit_signal(wave, signal, settings)
        result = self._with_uncertainty(result)
        return replace(result, fit_time_ms=(time.perf_counter()-start)*1000)

    @staticmethod
    def _with_uncertainty(result):
        # Joint Jacobian includes nuisance parameters: ignoring their covariance
        # would understate the errors, especially for low-contrast stacks.
        layers = result.settings.film_layers()
        count = len(layers)
        unavailable = np.full(count, np.nan)
        if result.status != "Good" or len(result.measured) <= count+2:
            return replace(result, layer_std_nm=unavailable)
        optics = StackOptics(result.wavelength_nm, [layer.material for layer in layers], result.settings.substrate_material())
        physical, derivative = optics.reflectance(result.layer_values(), derivatives=True)
        jacobian = np.column_stack((result.scale*derivative, physical, np.ones(len(physical))))
        norms = np.linalg.norm(jacobian, axis=0)
        if np.any(norms <= 1e-12):
            return replace(result, layer_std_nm=unavailable)
        _, singular, vectors = np.linalg.svd(jacobian/norms, full_matrices=False)
        if singular[-1] < singular[0]*1e-6:
            return replace(result, layer_std_nm=unavailable)
        noise_variance = float(result.residual @ result.residual)/(len(physical)-count-2)
        covariance = ((vectors.T/singular**2) @ vectors)*noise_variance/np.outer(norms, norms)
        thickness_covariance = covariance[:count, :count]
        return replace(result, layer_std_nm=np.sqrt(np.maximum(np.diag(thickness_covariance), 0)),
                       thickness_covariance_nm2=thickness_covariance)

    def _fit_signal(self, wave, signal, settings=None):
        """Fit already normalized finite data; used also for reference tests."""
        start = time.perf_counter()
        settings = (settings or FitSettings()).validate()
        wave, signal = np.asarray(wave, dtype=float), np.asarray(signal, dtype=float)
        if len(wave) < 20 or wave.shape != signal.shape or not np.isfinite(signal).all():
            raise FitRejected("At least 20 finite signal channels are required.")
        if not np.isfinite(wave).all() or np.any(np.diff(wave) <= 0):
            raise FitRejected("Wavelengths must be finite and increasing.")
        if np.std(signal) < 1e-6:
            raise FitRejected("Flat signal: thickness cannot be determined.")
        if settings.model == "stack":
            from dataclasses import replace
            layers = settings.layers
            if len(layers) == 1 and layers[0].material == "SiO2" and settings.substrate == "Si" and layers[0].maximum_nm <= 10000:
                legacy = FitSettings(settings.wavelength_min_nm, settings.wavelength_max_nm,
                                     layers[0].minimum_nm, layers[0].maximum_nm, settings.max_rmse_pp)
                result = self._fit_signal(wave, signal, legacy)
                return replace(result, settings=settings, layer_thicknesses_nm=np.array([result.thickness_nm]),
                               layer_materials=("SiO2",), layer_grids_nm=(result.grid_nm,),
                               layer_profiles_rmse_pp=(result.grid_rmse_pp,))
            if (len(layers) == 2 and tuple(l.material for l in layers) == ("SiO2", "AlN")
                    and settings.substrate == "sapphire" and all(l.maximum_nm <= 10000 for l in layers)):
                legacy = FitSettings(settings.wavelength_min_nm, settings.wavelength_max_nm,
                                     layers[0].minimum_nm, layers[0].maximum_nm, settings.max_rmse_pp,
                                     "sio2_aln_sapphire", layers[0].estimate_nm, layers[1].estimate_nm,
                                     layers[1].minimum_nm, layers[1].maximum_nm)
                result = self._fit_signal(wave, signal, legacy)
                points = np.stack(np.meshgrid(result.grid_nm, result.aln_grid_nm, indexing="ij"), axis=-1).reshape(-1, 2)
                return replace(result, settings=settings, layer_thicknesses_nm=np.array([result.thickness_nm, result.aln_thickness_nm]),
                               layer_materials=("SiO2", "AlN"), layer_grids_nm=(result.grid_nm, result.aln_grid_nm),
                               layer_profiles_rmse_pp=(result.grid_rmse_pp, result.aln_grid_rmse_pp),
                               search_points_nm=points, search_errors_pp=result.search_rmse_pp.ravel())
            try:
                return FitResult(**self.stack.fit(wave, signal, settings, start))
            except ValueError as error:
                raise FitRejected(str(error)) from error
        if settings.model == "sio2_aln_sapphire":
            try:
                return FitResult(**self.bilayer.fit(wave, signal, settings, start))
            except ValueError as error:
                raise FitRejected(str(error)) from error
        self._prepare(wave, settings)
        y_mean = signal.mean()
        y_centered = signal - y_mean
        covariance = self.centered @ y_centered
        scale = np.divide(covariance, self.variance, out=np.zeros_like(covariance), where=self.variance > 1e-12)
        # Profile affine parameters analytically, without a 550 x 791 residual array.
        loss = np.mean(y_centered**2) - scale * covariance / len(signal)
        loss = np.maximum(loss, 0)
        loss[(scale <= 0) | (self.variance <= 1e-12)] = np.inf
        if not np.isfinite(loss).any():
            raise FitRejected("No physically positive amplitude fit was found.")
        winner = int(np.argmin(loss))

        def evaluate(d):
            physical = film_reflectance(wave, d, self.si)
            a, b = np.linalg.lstsq(np.column_stack([physical, np.ones(len(wave))]), signal, rcond=None)[0]
            if a <= 0:
                return np.inf, a, b, physical
            prediction = a * physical + b
            return float(np.mean((signal - prediction)**2)), float(a), float(b), prediction

        boundary = winner in (0, len(self.grid) - 1)
        thickness = float(self.grid[winner])
        if not boundary:
            optimum = minimize_scalar(lambda d: evaluate(d)[0],
                                      bounds=(self.grid[winner - 1], self.grid[winner + 1]),
                                      method="bounded", options={"xatol": 1e-6})
            if not optimum.success:
                raise FitRejected("Thickness refinement failed.")
            thickness = float(optimum.x)
        mse, a, b, prediction = evaluate(thickness)
        if not np.isfinite(mse) or a <= 0 or not np.isfinite(prediction).all():
            raise FitRejected("The refined fit is not finite or has an invalid amplitude scale.")
        rmse = float(np.sqrt(mse))
        status, message = "Good", "Full thickness search completed."
        if boundary:
            status, message = "Boundary", "Best fit reaches a search bound; widen the thickness interval."
        else:
            minima = np.flatnonzero((loss[1:-1] < loss[:-2]) & (loss[1:-1] <= loss[2:])) + 1
            competitors = [i for i in minima if abs(self.grid[i] - thickness) >= 10
                           and np.sqrt(loss[i]) <= max(rmse * 1.25, rmse + 0.05)]
            if competitors:
                status, message = "Ambiguous", "Another distinct thickness minimum gives a similar error."
            elif rmse > settings.max_rmse_pp:
                status, message = "Poor fit", f"RMSE exceeds {settings.max_rmse_pp:g} percentage points."
        return FitResult(thickness, a, b, rmse, status, message, wave, signal, prediction,
                         signal - prediction, signal, np.ones(len(wave), dtype=bool),
                         self.grid, np.sqrt(loss), settings, (time.perf_counter() - start) * 1000)

    def process(self, frame, calibration, settings=None):
        settings = (settings or FitSettings()).validate()
        if calibration is None:
            raise FitRejected("Capture dark and mirror reference first.")
        if not calibration.compatible(frame):
            raise FitRejected("Calibration does not match the instrument or acquisition settings.")
        wave = frame.wavelength_nm
        interval = (wave >= settings.wavelength_min_nm) & (wave <= settings.wavelength_max_nm)
        if np.any((frame.saturated | calibration.saturated) & interval):
            raise FitRejected("Saturation in the fitting interval; reduce integration time and recalibrate.")
        denominator = calibration.reference - calibration.dark
        threshold = np.maximum(10.0, 5 * np.nan_to_num(calibration.dark_std, nan=np.inf))
        usable = (np.isfinite(frame.counts) & np.isfinite(calibration.dark) & np.isfinite(denominator)
                  & (denominator > threshold) & ~calibration.saturated & ~frame.saturated)
        reflectance = np.full(len(wave), np.nan)
        np.divide(100 * (frame.counts - calibration.dark), denominator, out=reflectance, where=usable)
        valid = interval & usable & np.isfinite(reflectance)
        if valid.sum() < 20:
            raise FitRejected("Too few usable reference channels in the fitting interval.")
        result = self.fit_signal(wave[valid], reflectance[valid], settings)
        from dataclasses import replace
        return replace(result, reflectance_full=reflectance, valid_mask=valid)
