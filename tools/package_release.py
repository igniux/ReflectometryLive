"""Create an SDK-free portable ZIP and SHA-256 manifest using only the stdlib."""
import argparse
import hashlib
import json
from pathlib import Path
import re
import zipfile

ROOT = Path(__file__).resolve().parents[1]
ALLOWED = {"ReflectometryLive.exe", "_internal", "README.txt", "LICENSE", "THIRD_PARTY.md", "sdk_manifest.json", "licenses"}
PRIVATE = {".cache", "Measurements", "measurements", "__pycache__", ".git", ".venv"}


def sha256_file(path):
    with path.open("rb") as handle:
        return hashlib.file_digest(handle, "sha256").hexdigest()


def package(release_dir, output_dir, version, source_commit):
    release_dir, output_dir = Path(release_dir).resolve(), Path(output_dir).resolve()
    if not re.fullmatch(r"\d+\.\d+\.\d+(?:[-.][A-Za-z0-9]+)*", version):
        raise ValueError("Invalid release version.")
    if not re.fullmatch(r"[0-9a-f]{40}", source_commit):
        raise ValueError("Provide the tested source commit's full SHA-1.")
    required = ["ReflectometryLive.exe", "_internal", "README.txt", "LICENSE", "THIRD_PARTY.md",
                "sdk_manifest.json", "licenses/Qt-LGPL-3.0.txt", "licenses/Qt-GPL-3.0.txt",
                "licenses/Python.txt", "licenses/PyInstaller.txt"]
    for name in required:
        if not (release_dir/name).exists():
            raise ValueError(f"Incomplete release: missing {name}")
    if any(p.name.casefold() == "avaspecx64.dll" for p in release_dir.rglob("*")):
        raise ValueError("A public release must not contain the vendor SDK DLL.")
    files = []
    for path in sorted(release_dir.rglob("*")):
        relative = path.relative_to(release_dir)
        if not path.is_file() or relative.parts[0] not in ALLOWED:
            continue
        if PRIVATE.intersection(relative.parts) or path.suffix.lower() == ".lnk":
            continue
        if path.is_symlink():
            raise ValueError(f"Symlink in release: {relative}")
        files.append((path, relative.as_posix()))
    hashes = {name: sha256_file(path) for path, name in files}
    manifest = {"application": "Reflectometry Live", "version": version, "source_commit": source_commit,
                "platform": "Windows x64", "vendor_sdk_included": False,
                "files_sha256": hashes}
    output_dir.mkdir(parents=True, exist_ok=True)
    archive = output_dir/f"ReflectometryLive-v{version}-windows-x64.zip"
    checksum = output_dir/"SHA256SUMS.txt"
    if archive.exists() or checksum.exists():
        raise FileExistsError("Release output already exists; choose a new output directory.")
    temporary = archive.with_suffix(".zip.partial")
    try:
        with zipfile.ZipFile(temporary, "x", zipfile.ZIP_DEFLATED, compresslevel=6) as bundle:
            for path, name in files:
                bundle.write(path, "ReflectometryLive/"+name)
            bundle.writestr("ReflectometryLive/build-info.json", json.dumps(manifest, indent=2)+"\n")
        with zipfile.ZipFile(temporary) as bundle:
            if bundle.testzip() is not None:
                raise ValueError("Archive integrity check failed.")
            names = bundle.namelist()
            if any(Path(name).name.casefold() == "avaspecx64.dll" or PRIVATE.intersection(Path(name).parts) for name in names):
                raise ValueError("Private/vendor content in release archive.")
        temporary.replace(archive)
    except Exception:
        temporary.unlink(missing_ok=True)
        raise
    digest = sha256_file(archive)
    checksum.write_text(f"{digest}  {archive.name}\n", encoding="utf-8")
    return {"archive": str(archive), "sha256": digest, "bytes": archive.stat().st_size,
            "files": len(files)+1, "source_commit": source_commit}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--release-dir", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--source-commit", required=True)
    parser.add_argument("--version", default=re.search(r'__version__ = "([^"]+)"', (ROOT/"reflectometry_live/__init__.py").read_text())[1])
    args = parser.parse_args()
    print(json.dumps(package(args.release_dir, args.output_dir, args.version, args.source_commit), indent=2))


if __name__ == "__main__":
    main()
