"""Immutable frames and explicit acquisition/calibration metadata."""

from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
import time

import numpy as np


def utc_now():
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


def frozen_array(values, dtype=float):
    result = np.array(values, dtype=dtype, copy=True)
    result.setflags(write=False)
    return result


@dataclass(frozen=True)
class AcquisitionSettings:
    integration_ms: float = 6.0
    averages: int = 11
    smoothing_pixels: int = 0
    high_res_adc: bool = True

    def validate(self):
        if not isinstance(self.high_res_adc, bool):
            raise ValueError("ADC mode must be a boolean.")
        if not np.isfinite(self.integration_ms) or not 0 < self.integration_ms <= 600000:
            raise ValueError("Integration time must be greater than zero and at most 600000 ms.")
        if not 1 <= self.averages <= 100000 or int(self.averages) != self.averages:
            raise ValueError("Averaging must be an integer from 1 to 100000.")
        if not 0 <= self.smoothing_pixels <= 100 or int(self.smoothing_pixels) != self.smoothing_pixels:
            raise ValueError("Smoothing must be an integer from 0 to 100 pixels.")
        return self

    def calibration_key(self):
        # Averaging changes noise, not the mean count scale returned by AvaSpec.
        return self.integration_ms, self.smoothing_pixels, self.high_res_adc


@dataclass(frozen=True)
class LayerSettings:
    material: str = "GaN"
    estimate_nm: float = 500.0
    minimum_nm: float = 100.0
    maximum_nm: float = 1200.0
    bounds_manual: bool = False

    def with_estimate(self, estimate):
        from dataclasses import replace
        if self.bounds_manual:
            return replace(self, estimate_nm=estimate)
        return replace(self, estimate_nm=estimate, minimum_nm=max(0., estimate*.25),
                       maximum_nm=min(100000., max(estimate*1.75, estimate+200.)))

    def validate(self):
        if self.material not in ("SiO2", "AlN", "GaN", "AZ3027E"):
            raise ValueError(f"Unsupported film material: {self.material}")
        if not all(np.isfinite(x) for x in (self.estimate_nm, self.minimum_nm, self.maximum_nm)):
            raise ValueError("Layer thicknesses must be finite.")
        if not 0 <= self.minimum_nm < self.maximum_nm <= 100000 or self.maximum_nm-self.minimum_nm < 4:
            raise ValueError("Film search bounds must be 0–100000 nm and at least 4 nm apart.")
        if not self.minimum_nm <= self.estimate_nm <= self.maximum_nm:
            raise ValueError(f"The {self.material} estimate must lie inside its search bounds.")
        return self


@dataclass(frozen=True)
class FitSettings:
    wavelength_min_nm: float = 400.0
    wavelength_max_nm: float = 850.0
    thickness_min_nm: float = 100.0
    thickness_max_nm: float = 1200.0
    max_rmse_pp: float = 2.0
    model: str = "sio2_si"
    oxide_estimate_nm: float = 500.0
    aln_estimate_nm: float = 500.0
    aln_min_nm: float = 100.0
    aln_max_nm: float = 1200.0
    layers: tuple[LayerSettings, ...] = ()
    substrate: str = "Si"

    @classmethod
    def from_dict(cls, values):
        values = dict(values)
        values["layers"] = tuple(LayerSettings(**item) if isinstance(item, dict) else item
                                 for item in values.get("layers", ()))
        return cls(**values).validate()

    def film_layers(self):
        if self.model == "stack":
            return self.layers
        oxide = LayerSettings("SiO2", float(np.clip(self.oxide_estimate_nm, self.thickness_min_nm, self.thickness_max_nm)),
                              self.thickness_min_nm, self.thickness_max_nm, True)
        if self.model == "sio2_aln_sapphire":
            return (oxide, LayerSettings("AlN", self.aln_estimate_nm, self.aln_min_nm, self.aln_max_nm, True))
        return (oxide,)

    def substrate_material(self):
        return self.substrate if self.model == "stack" else "sapphire" if self.model == "sio2_aln_sapphire" else "Si"

    def as_stack(self):
        from dataclasses import replace
        return replace(self, model="stack", layers=self.film_layers(), substrate=self.substrate_material()).validate()

    def validate(self):
        values = [v for k, v in asdict(self).items() if k not in ("model", "layers", "substrate")]
        if not all(np.isfinite(v) for v in values):
            raise ValueError("Fitting settings must be finite numbers.")
        if not 250 <= self.wavelength_min_nm < self.wavelength_max_nm <= 1450:
            raise ValueError("Choose a wavelength interval within the Si data range, 250–1450 nm.")
        if not 0 <= self.thickness_min_nm < self.thickness_max_nm <= 10000:
            raise ValueError("Choose thickness bounds within 0–10000 nm.")
        if self.thickness_max_nm - self.thickness_min_nm < 4:
            raise ValueError("Thickness search interval must be at least 4 nm wide.")
        if self.max_rmse_pp <= 0:
            raise ValueError("The poor-fit RMSE threshold must be positive.")
        if self.model not in ("sio2_si", "sio2_aln_sapphire", "stack"):
            raise ValueError("Unknown optical model.")
        if self.model == "sio2_aln_sapphire":
            if not 0 <= self.aln_min_nm < self.aln_max_nm <= 10000 or self.aln_max_nm-self.aln_min_nm < 4:
                raise ValueError("AlN search bounds must be 0–10000 nm and at least 4 nm apart.")
            if not self.thickness_min_nm <= self.oxide_estimate_nm <= self.thickness_max_nm:
                raise ValueError("The rough SiO₂ estimate must lie inside its search bounds.")
            if not self.aln_min_nm <= self.aln_estimate_nm <= self.aln_max_nm:
                raise ValueError("The rough AlN estimate must lie inside its search bounds.")
        if self.model == "stack":
            if not 1 <= len(self.layers) <= 4:
                raise ValueError("Choose one to four finite films above the substrate.")
            for layer in self.layers:
                layer.validate()
            if self.substrate not in ("sapphire", "Si"):
                raise ValueError("Choose sapphire or Si as the substrate.")
            if any(layer.material == "GaN" for layer in self.layers) and (self.wavelength_min_nm < 369 or self.wavelength_max_nm > 991.7):
                raise ValueError("Choose 369–991.7 nm for the bundled ordinary GaN optical data.")
            if any(layer.material == "AZ3027E" for layer in self.layers) and (self.wavelength_min_nm < 500 or self.wavelength_max_nm > 850):
                raise ValueError("Choose 500–850 nm for the provisional unexposed AZ ECI 3027 photoresist reference; the absorbing blue/UV interval is excluded.")
        return self


@dataclass(frozen=True)
class Frame:
    wavelength_nm: np.ndarray
    counts: np.ndarray
    saturated: np.ndarray
    device_serial: str
    settings: AcquisitionSettings
    sequence: int
    timestamp_utc: str = field(default_factory=utc_now)
    captured_at: float = field(default_factory=time.monotonic)
    sdk_timestamp: int | None = None
    source_kind: str = "Avantes USB"

    def __post_init__(self):
        for name, dtype in (("wavelength_nm", float), ("counts", float), ("saturated", bool)):
            object.__setattr__(self, name, frozen_array(getattr(self, name), dtype))
        if self.wavelength_nm.ndim != 1 or len(self.wavelength_nm) < 20:
            raise ValueError("A spectrum needs at least 20 wavelength channels.")
        if self.counts.shape != self.wavelength_nm.shape or self.saturated.shape != self.counts.shape:
            raise ValueError("Wavelength, counts and saturation arrays must have equal lengths.")
        if not np.isfinite(self.wavelength_nm).all() or np.any(np.diff(self.wavelength_nm) <= 0):
            raise ValueError("Wavelengths must be finite and strictly increasing.")


@dataclass(frozen=True)
class Calibration:
    wavelength_nm: np.ndarray
    dark: np.ndarray
    reference: np.ndarray
    dark_std: np.ndarray
    reference_std: np.ndarray
    saturated: np.ndarray
    device_serial: str
    settings: AcquisitionSettings
    dark_timestamp_utc: str | None
    reference_timestamp_utc: str | None
    captures: int = 5

    def __post_init__(self):
        for name in ("wavelength_nm", "dark", "reference", "dark_std", "reference_std", "saturated"):
            object.__setattr__(self, name, frozen_array(getattr(self, name), bool if name == "saturated" else float))
        size = self.wavelength_nm.shape
        if any(getattr(self, name).shape != size for name in ("dark", "reference", "dark_std", "reference_std", "saturated")):
            raise ValueError("Calibration arrays must have equal lengths.")

    def compatible(self, frame):
        return (
            self.device_serial == frame.device_serial
            and self.settings.calibration_key() == frame.settings.calibration_key()
            and np.array_equal(self.wavelength_nm, frame.wavelength_nm)
        )


@dataclass(frozen=True)
class CalibrationCapture:
    kind: str
    frame: Frame
    mean: np.ndarray
    std: np.ndarray
    saturated: np.ndarray

    def __post_init__(self):
        for name in ("mean", "std", "saturated"):
            object.__setattr__(self, name, frozen_array(getattr(self, name), bool if name == "saturated" else float))


def combine_calibration(dark, reference):
    if (dark.frame.device_serial != reference.frame.device_serial
            or dark.frame.settings.calibration_key() != reference.frame.settings.calibration_key()
            or not np.array_equal(dark.frame.wavelength_nm, reference.frame.wavelength_nm)):
        raise ValueError("Dark and reference must use the same instrument, exposure and processing.")
    return Calibration(
        dark.frame.wavelength_nm, dark.mean, reference.mean, dark.std, reference.std,
        dark.saturated | reference.saturated, dark.frame.device_serial, reference.frame.settings,
        dark.frame.timestamp_utc, reference.frame.timestamp_utc,
    )
