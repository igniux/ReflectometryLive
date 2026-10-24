"""Avantes replay input and self-contained, reproducible measurement snapshots."""

from dataclasses import asdict
from datetime import datetime, timezone
import csv
import hashlib
import json
from pathlib import Path
import re

import numpy as np

from . import __version__
from .domain import AcquisitionSettings, Calibration, Frame, utc_now
from .optics import model_description, optical_sources, photoresist_record
from .paths import APP_ROOT


def load_replay(path):
    path = Path(path).resolve()
    metadata = {}
    if path.suffix.lower() == ".json":
        metadata = json.loads(path.read_text(encoding="utf-8"))
        path = path.parent / "spectrum.csv"
    elif path.name == "spectrum.csv" and (path.parent / "metadata.json").exists():
        metadata = json.loads((path.parent / "metadata.json").read_text(encoding="utf-8"))
    if (path.parent / ".incomplete").exists():
        raise ValueError("This measurement was not saved completely.")
    header, rows, info = None, [], {}
    with path.open(encoding="utf-8-sig", newline="") as handle:
        for row in csv.reader(handle, delimiter=";"):
            fields = [item.strip() for item in row]
            if fields and fields[0] == "# ReflectometryLive metadata":
                if len(fields) != 2:
                    raise ValueError("Invalid embedded measurement metadata.")
                metadata = json.loads(fields[1])
                continue
            if len(fields) == 1 and ":" in fields[0]:
                key, value = fields[0].split(":", 1)
                info[key.strip()] = value.strip()
            if fields and fields[0] == "Wave":
                header = fields
                continue
            if header is None or len(fields) != len(header):
                continue
            try:
                rows.append([float(value.replace(",", ".")) if value else np.nan for value in fields])
            except ValueError:
                continue
    needed = ("Wave", "Sample", "Dark", "Reference", "Reflectance")
    if header is None or not rows or not all(name in header for name in needed):
        raise ValueError("Select an Avantes five-column reflectance export or a saved measurement.")
    array = np.asarray(rows, dtype=float)
    data = {name: array[:, header.index(name)] for name in header}
    wave = data["Wave"]
    if not np.isfinite(wave).all() or np.any(np.diff(wave) <= 0):
        raise ValueError("Replay wavelengths must be finite and increasing.")
    settings = AcquisitionSettings(
        integration_ms=float(str(info.get("Integration time [ms]", "6")).replace(",", ".")),
        averages=int(info.get("Averaging Nr. [scans]", 11)),
        smoothing_pixels=int(info.get("Smoothing Nr. [pixels]", 0)),
    )
    frame_meta = metadata.get("frame", {})
    if "settings" in frame_meta:
        settings = AcquisitionSettings(**frame_meta["settings"])
    settings.validate()
    serial = frame_meta.get("device_serial", info.get("Data measured with spectrometer [name]", "Replay"))
    frame = Frame(wave, data["Sample"], data.get("Saturated", np.zeros(len(wave), dtype=bool)),
                  serial, settings, 0, timestamp_utc=frame_meta.get("timestamp_utc") or utc_now(),
                  sdk_timestamp=frame_meta.get("sdk_timestamp"), source_kind="Replay")
    cal_meta = metadata.get("calibration", {})
    calibration = Calibration(
        wave, data["Dark"], data["Reference"], data.get("DarkStd", np.zeros(len(wave))),
        data.get("ReferenceStd", np.zeros(len(wave))), data.get("CalibrationSaturated", np.zeros(len(wave), dtype=bool)),
        cal_meta.get("device_serial", serial), AcquisitionSettings(**cal_meta["settings"]) if "settings" in cal_meta else settings,
        cal_meta.get("dark_timestamp_utc"), cal_meta.get("reference_timestamp_utc"),
        cal_meta.get("captures", 1),
    )
    return frame, calibration, metadata


def material_name(material, pretty=False):
    if material == "AZ3027E":
        return "AZ 3027"
    return "SiO₂" if pretty and material == "SiO2" else material


def capture_local_time(frame):
    captured = datetime.fromisoformat(frame.timestamp_utc.replace("Z", "+00:00"))
    if captured.tzinfo is None:
        captured = captured.replace(tzinfo=timezone.utc)
    return captured.astimezone()


def snapshot_folder_name(name, frame, result):
    """Capture time, optional wafer ID and films in exposed-surface order."""
    label = " ".join(re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", name.strip()).split()).strip(". ")[:64]
    films = " ".join(f"{material_name(layer.material)} {value:.0f}" for layer, value
                     in zip(result.settings.film_layers(), result.layer_values()))
    return " ".join(part for part in (capture_local_time(frame).strftime("%Y%m%dT%H%M%S"), label, films) if part)


def write_snapshot(parent, name, frame, calibration, result):
    """Save one reproducible CSV and one PNG for the immutable completed frame."""
    if not calibration.compatible(frame):
        raise ValueError("Snapshot calibration does not match its frame.")
    if (result.valid_mask.shape != frame.wavelength_nm.shape or
            not np.array_equal(frame.wavelength_nm[result.valid_mask], result.wavelength_nm)):
        raise ValueError("The fit does not belong to the captured spectrum.")
    parent = Path(parent).resolve()
    parent.mkdir(parents=True, exist_ok=True)
    label = snapshot_folder_name(name, frame, result)
    counter = 1
    while True:
        folder = parent / (label if counter == 1 else f"{label} ({counter})")
        try:
            folder.mkdir(exist_ok=False)
            break
        except FileExistsError:
            counter += 1
    incomplete = folder / ".incomplete"
    incomplete.write_text("Snapshot export in progress.\n", encoding="utf-8")
    layers = result.settings.film_layers()
    sdk = APP_ROOT / "avaspecx64.dll"
    metadata = {
        "schema_version": 4, "application_version": __version__, "measurement_name": name,
        "folder_name": folder.name, "capture_time_local": capture_local_time(frame).isoformat(),
        "saved_at_utc": utc_now(), "units": {"Wave": "nm", "Sample": "counts", "Dark": "counts",
                                            "Reference": "counts", "Reflectance": "relative percent"},
        "frame": {"device_serial": frame.device_serial, "source_kind": frame.source_kind,
                  "timestamp_utc": frame.timestamp_utc, "sequence": frame.sequence,
                  "sdk_timestamp": frame.sdk_timestamp, "settings": asdict(frame.settings)},
        "calibration": {"device_serial": calibration.device_serial, "settings": asdict(calibration.settings),
                        "dark_timestamp_utc": calibration.dark_timestamp_utc,
                        "reference_timestamp_utc": calibration.reference_timestamp_utc, "captures": calibration.captures},
        "fit_settings": asdict(result.settings), "result": result.summary(),
        "optical_sources": optical_sources(result.settings),
        "photoresist_reference": photoresist_record() if any(layer.material == "AZ3027E" for layer in layers) else None,
        "search_description": ("Sobol samples of complete bounds plus manual estimate, with multistart refinement; not exhaustive. Profiles hold other films at their fitted values."
                               if result.search_kind == "sobol_multistart" else "Full coarse grid, affine profiled error and continuous refinement."),
        "sdk": {"file": "avaspecx64.dll", "sha256": hashlib.sha256(sdk.read_bytes()).hexdigest() if sdk.exists() else None},
    }
    fitted = np.full(len(frame.wavelength_nm), np.nan)
    residual = np.full(len(frame.wavelength_nm), np.nan)
    fitted[result.valid_mask] = result.prediction
    residual[result.valid_mask] = result.residual
    columns = [frame.wavelength_nm, frame.counts, calibration.dark, calibration.reference,
               result.reflectance_full, fitted, residual, frame.saturated.astype(int),
               result.valid_mask.astype(int), calibration.dark_std, calibration.reference_std,
               calibration.saturated.astype(int)]
    with (folder / "spectrum.csv").open("w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle, delimiter=";")
        writer.writerow(["# ReflectometryLive metadata", json.dumps(metadata, allow_nan=False)])
        writer.writerow(["Wave", "Sample", "Dark", "Reference", "Reflectance", "FittedReflectance",
                         "Residual", "Saturated", "ValidFit", "DarkStd", "ReferenceStd", "CalibrationSaturated"])
        writer.writerows([float(value) if np.isfinite(value) else "" for value in row] for row in zip(*columns))
    # Matplotlib is used solely by this serial export worker, not for live graphics.
    import matplotlib
    matplotlib.use("Agg")
    from matplotlib.figure import Figure
    from matplotlib.backends.backend_agg import FigureCanvasAgg
    from matplotlib.patches import FancyBboxPatch
    figure = Figure(figsize=(12, 8.5), layout="constrained", facecolor="white")
    FigureCanvasAgg(figure)
    grid = figure.add_gridspec(4, 1, height_ratios=[.35, 1.15, 3.2, .85])
    header = figure.add_subplot(grid[0]); header.axis("off")
    stack = " / ".join(["Air"]+[material_name(layer.material, True) for layer in layers]+[result.settings.substrate_material()])
    heading = f"{name.strip()}  ·  {stack}" if name.strip() else stack
    header.text(0, .85, heading, fontsize=17, weight="bold", color="#263746", va="top")
    timestamp = capture_local_time(frame).strftime("%Y-%m-%d %H:%M:%S %Z")
    header.text(0, .05, timestamp, fontsize=10, color="#5b6770", va="bottom")
    color = "#187445" if result.status == "Good" else "#9e620b"
    header.text(1, .05, f"{result.status}  ·  RMSE {result.rmse_pp:.3f} pp", fontsize=11,
                weight="bold", color=color, va="bottom", ha="right")
    cards = figure.add_subplot(grid[1]); cards.axis("off")
    count = len(layers)
    for index, (layer, value) in enumerate(zip(layers, result.layer_values())):
        x = index/count+.006
        width = 1/count-.012
        card = FancyBboxPatch((x, .08), width, .82, boxstyle="round,pad=0.005,rounding_size=0.025",
                              transform=cards.transAxes, facecolor="#eef7f0" if result.status == "Good" else "#fff5e8",
                              edgecolor=color, linewidth=1.3)
        cards.add_patch(card)
        center = x+width/2
        qualifier = "" if result.status == "Good" else " · candidate"
        cards.text(center, .75, f"{index+1}. {material_name(layer.material, True)}{qualifier}",
                   transform=cards.transAxes, ha="center", fontsize=12, color="#3c505e")
        cards.text(center, .44, f"{value:.1f} nm", transform=cards.transAxes,
                   ha="center", fontsize=30 if count <= 2 else 24, weight="bold", color=color)
        sigma = result.layer_std_nm[index] if index < len(result.layer_std_nm) else np.nan
        error = f"± {sigma:.2g} nm · fit σ" if np.isfinite(sigma) and sigma > 1e-6 else "± <0.001 nm · fit σ" if np.isfinite(sigma) else "Fit error unavailable"
        cards.text(center, .19, error, transform=cards.transAxes, ha="center", fontsize=10, color="#53675b")
    fit_axis = figure.add_subplot(grid[2])
    residual_axis = figure.add_subplot(grid[3], sharex=fit_axis)
    fit_axis.plot(result.wavelength_nm, result.measured, color="#33485a", linewidth=1.5, label="Measured")
    fit_axis.plot(result.wavelength_nm, result.prediction, color="#16804a", linewidth=2.2, label="Fit")
    fit_axis.set(ylabel="Relative reflectance (%)")
    fit_axis.legend(loc="best", frameon=False)
    fit_axis.tick_params(labelbottom=False)
    residual_axis.plot(result.wavelength_nm, result.residual, color="#657d8b", linewidth=1)
    residual_axis.axhline(0, color="#263746", linewidth=.7)
    residual_axis.set(xlabel="Wavelength (nm)", ylabel="Residual (pp)")
    for axis in (fit_axis, residual_axis):
        axis.grid(alpha=.18)
        axis.spines["top"].set_visible(False); axis.spines["right"].set_visible(False)
    figure.supxlabel("Thicknesses are in top-to-bottom order. ± is statistical fit error; index, calibration and model errors are excluded.",
                     fontsize=9, color="#5b6770")
    figure.savefig(folder / "fit.png", dpi=180)
    incomplete.unlink()
    return folder
