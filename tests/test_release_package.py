import hashlib
import importlib.util
import json
from pathlib import Path
import zipfile

import pytest


def packager():
    path = Path(__file__).resolve().parents[1]/"tools/package_release.py"
    spec = importlib.util.spec_from_file_location("release_packager", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def release_tree(tmp_path):
    root = tmp_path/"ReflectometryLive"
    for name in ["ReflectometryLive.exe", "_internal/runtime.dll", "README.txt", "LICENSE",
                 "THIRD_PARTY.md", "sdk_manifest.json", "licenses/Qt-LGPL-3.0.txt",
                 "licenses/Qt-GPL-3.0.txt", "licenses/Python.txt", "licenses/PyInstaller.txt"]:
        path = root/name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"Synthetic packaging fixture\n")
    return root


def test_public_zip_contains_exe_and_runtime_but_no_local_captures_or_caches(tmp_path):
    root = release_tree(tmp_path)
    for name in [".cache/launch.log", "Measurements/private.csv", "local.lnk", "other.txt"]:
        path = root/name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("Excluded local fixture", encoding="utf-8")
    result = packager().package(root, tmp_path/"output", "1.3.5", "a"*40)
    archive = Path(result["archive"])
    with zipfile.ZipFile(archive) as bundle:
        names = set(bundle.namelist())
        assert "ReflectometryLive/ReflectometryLive.exe" in names
        assert "ReflectometryLive/_internal/runtime.dll" in names
        assert not any("private.csv" in n or "launch.log" in n or n.endswith(".lnk") for n in names)
        manifest = json.loads(bundle.read("ReflectometryLive/build-info.json"))
        assert manifest["source_commit"] == "a"*40
        assert not manifest["vendor_sdk_included"]
        for name, digest in manifest["files_sha256"].items():
            assert hashlib.sha256(bundle.read("ReflectometryLive/"+name)).hexdigest() == digest
    assert (archive.parent/"SHA256SUMS.txt").read_text().strip() == result["sha256"]+"  "+archive.name
    with pytest.raises(FileExistsError):
        packager().package(root, archive.parent, "1.3.5", "a"*40)


def test_public_zip_rejects_vendor_dll_and_incomplete_build(tmp_path):
    root = release_tree(tmp_path)
    vendor = root/"AVASPECX64.DLL"
    vendor.write_bytes(b"Dummy vendor fixture")
    with pytest.raises(ValueError, match="vendor SDK"):
        packager().package(root, tmp_path/"output", "1.3.5", "a"*40)
    vendor.unlink()
    (root/"ReflectometryLive.exe").unlink()
    with pytest.raises(ValueError, match="missing ReflectometryLive.exe"):
        packager().package(root, tmp_path/"output", "1.3.5", "a"*40)
