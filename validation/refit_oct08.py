"""Diagnose the October 8 measurement without changing its original files."""
from dataclasses import replace
import json
import os
from pathlib import Path
import sys
import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.environ.setdefault("MPLCONFIGDIR", str(ROOT / ".cache" / "matplotlib"))
from reflectometry_live.domain import FitSettings, LayerSettings
from reflectometry_live.fitting import CachedFitter
from reflectometry_live.io import load_replay, write_snapshot
from reflectometry_live.optics import bilayer_reflectance


def main():
    path = ROOT / "tests" / "20261008T093852.952227Z_measurement" / "metadata.json"
    frame, calibration, original = load_replay(path)
    settings = FitSettings.from_dict(original["fit_settings"])
    corrected = replace(settings, model="sio2_aln_sapphire")
    fitter = CachedFitter()
    result = fitter.process(frame, calibration, corrected)
    trial = bilayer_reflectance(result.wavelength_nm, 500, 500)
    a, b = np.linalg.lstsq(np.column_stack([trial, np.ones(len(trial))]), result.measured, rcond=None)[0]
    comparison = {"original": original["result"], "corrected": result.summary(),
                  "500_nm_each_rmse_pp": float(np.sqrt(np.mean((result.measured-a*trial-b)**2))),
                  "intervals": []}
    reversed_stack = FitSettings(model="stack", layers=(LayerSettings("AlN", 500, 100, 1200, True),
                                                       LayerSettings("SiO2", 500, 100, 1200, True)), substrate="sapphire")
    reverse = fitter.process(frame, calibration, reversed_stack)
    comparison["reversed_stack"] = reverse.summary()
    for low, high in [(400, 850), (450, 850), (500, 850), (400, 750)]:
        interval_result = fitter.process(frame, calibration, replace(corrected, wavelength_min_nm=low, wavelength_max_nm=high))
        comparison["intervals"].append({"wavelength_nm": [low, high], **interval_result.summary()})
    output = ROOT / "validation" / "oct08_refit"
    output.mkdir(exist_ok=True)
    comparison["snapshot"] = str(write_snapshot(output, "SiO2-AlN-sapphire corrected model", frame, calibration, result))
    comparison["reversed_snapshot"] = str(write_snapshot(output, "AlN-SiO2-sapphire alternative order", frame, calibration, reverse))
    (output / "comparison.json").write_text(json.dumps(comparison, indent=2), encoding="utf-8")
    print(json.dumps({"original_model": settings.model, "corrected_fit": result.summary(),
                      "500_nm_each_rmse_pp": comparison["500_nm_each_rmse_pp"],
                      "reversed_stack": comparison["reversed_stack"], "snapshot": comparison["snapshot"]}, indent=2))


if __name__ == "__main__":
    main()
