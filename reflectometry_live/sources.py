"""Replay acquisition uses the same immutable frame pipeline as USB acquisition."""

from .avantes import MeasurementCancelled
from .domain import Frame, FitSettings
from .io import load_replay


class ReplaySource:
    def __init__(self, path):
        self.path = path
        self.sequence = 0
        self.pending = False

    def connect(self, settings):
        self.original, self.calibration, self.metadata = load_replay(self.path)
        if self.metadata.get("fit_settings"):
            FitSettings.from_dict(self.metadata["fit_settings"])
        self.settings = self.original.settings
        return {"serial": self.original.device_serial, "name": "Replay: " + str(self.path),
                "pixels": len(self.original.counts), "source_kind": "Replay", "settings": self.settings,
                "calibration": self.calibration, "fit_settings": self.metadata.get("fit_settings")}

    def configure(self, settings):
        if settings != self.settings:
            raise ValueError("Replay uses the acquisition settings stored in the file. Select USB to change exposure or averaging.")

    def read(self, cancel):
        if cancel.is_set():
            raise MeasurementCancelled()
        self.sequence += 1
        return Frame(self.original.wavelength_nm, self.original.counts, self.original.saturated,
                     self.original.device_serial, self.settings, self.sequence,
                     timestamp_utc=self.original.timestamp_utc, sdk_timestamp=self.original.sdk_timestamp,
                     source_kind="Replay")

    def stop(self):
        pass

    def close(self):
        return []


def create_source(descriptor):
    if descriptor["kind"] == "replay":
        return ReplaySource(descriptor["path"])
    if descriptor["kind"] == "avantes":
        from .avantes import AvantesSource
        return AvantesSource(descriptor.get("serial"))
    raise ValueError("Unknown spectrum source.")
