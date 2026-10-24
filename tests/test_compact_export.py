import csv
from dataclasses import replace
from datetime import datetime, timezone

import numpy as np
import pytest

from reflectometry_live.domain import FitSettings
from reflectometry_live.fitting import CachedFitter
from reflectometry_live.io import load_replay, snapshot_folder_name, write_snapshot


def real_bilayer(app_root):
    frame, cal, _ = load_replay(app_root/"tests/20261008T093852.952227Z_measurement/metadata.json")
    frame = replace(frame, timestamp_utc="2026-10-09T10:08:11+00:00")
    result = CachedFitter().process(frame, cal, FitSettings(model="sio2_aln_sapphire").as_stack())
    return frame, cal, result


def test_folder_order_wafer_optional_timestamp_and_repeat_suffix(app_root, tmp_path):
    frame, cal, result = real_bilayer(app_root)
    local = datetime(2026, 10, 9, 10, 8, 11, tzinfo=timezone.utc).astimezone()
    stamp = local.strftime("%Y%m%dT%H%M%S")
    assert snapshot_folder_name("", frame, result) == f"{stamp} SiO2 508 AlN 587"
    assert snapshot_folder_name("2D4JAA157", frame, result) == f"{stamp} 2D4JAA157 SiO2 508 AlN 587"
    assert snapshot_folder_name('wafer / 2', frame, result) == f"{stamp} wafer _ 2 SiO2 508 AlN 587"
    first = write_snapshot(tmp_path, "", frame, cal, result)
    second = write_snapshot(tmp_path, "", frame, cal, result)
    assert first.name == f"{stamp} SiO2 508 AlN 587"
    assert second.name == first.name+" (2)"
    assert first.exists()


def test_one_csv_contains_aligned_fit_and_complete_replay_record(app_root, tmp_path):
    frame, cal, result = real_bilayer(app_root)
    saved = write_snapshot(tmp_path, "2D4JAA157", frame, cal, result)
    assert {p.name for p in saved.iterdir()} == {"spectrum.csv", "fit.png"}
    with (saved/"spectrum.csv").open(encoding="utf-8", newline="") as handle:
        rows = list(csv.reader(handle, delimiter=";"))
    assert rows[0][0] == "# ReflectometryLive metadata"
    header = rows[1]
    data = np.array([[float(x) if x else np.nan for x in row] for row in rows[2:]])
    np.testing.assert_array_equal(data[:, header.index("Wave")], frame.wavelength_nm)
    valid = data[:, header.index("ValidFit")].astype(bool)
    assert np.isnan(data[~valid, header.index("FittedReflectance")]).all()
    np.testing.assert_array_equal(data[valid, header.index("FittedReflectance")], result.prediction)
    np.testing.assert_array_equal(data[valid, header.index("Residual")], result.residual)
    restored_frame, restored_cal, metadata = load_replay(saved/"spectrum.csv")
    assert metadata["schema_version"] == 4
    assert metadata["measurement_name"] == "2D4JAA157"
    assert metadata["folder_name"] == saved.name
    assert restored_frame.timestamp_utc == frame.timestamp_utc
    assert restored_frame.sdk_timestamp == frame.sdk_timestamp
    np.testing.assert_array_equal(restored_cal.reference_std, cal.reference_std)
    refit = CachedFitter().process(restored_frame, restored_cal, FitSettings.from_dict(metadata["fit_settings"]))
    np.testing.assert_allclose(refit.layer_values(), result.layer_values(), atol=1e-6)
    np.testing.assert_allclose(refit.layer_std_nm, result.layer_std_nm, atol=1e-9)
    (saved/".incomplete").write_text("Interrupted save")
    with pytest.raises(ValueError, match="not saved completely"):
        load_replay(saved/"spectrum.csv")


def test_optional_wafer_field_previews_frozen_folder(qapp, app_root, tmp_path):
    from reflectometry_live.panels import SaveDialog
    frame, cal, result = real_bilayer(app_root)
    dialog = SaveDialog((frame, cal, result), tmp_path)
    try:
        assert dialog.values()[1] == ""
        assert dialog.folder_preview.text() == snapshot_folder_name("", frame, result)
        dialog.name.setText("2D4JAA157")
        assert dialog.folder_preview.text() == snapshot_folder_name("2D4JAA157", frame, result)
        assert dialog.values()[1] == "2D4JAA157"
    finally:
        dialog.deleteLater()
