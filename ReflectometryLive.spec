# Windowed x64 distribution. The vendor DLL is copied beside the EXE by Build.ps1.
from pathlib import Path
from PyInstaller.utils.hooks import collect_data_files, copy_metadata

root = Path(SPECPATH)
datas = [
    (str(root/"reflectometry_live"/"data"), "reflectometry_live/data"),
    (str(root/"assets"), "assets"),
    (str(root/"demo"), "demo"),
]
datas += collect_data_files("pyqtgraph")
for package in ("numpy", "scipy", "matplotlib", "PySide6", "PySide6_Addons",
                "PySide6_Essentials", "shiboken6", "pyqtgraph", "pillow", "contourpy",
                "fonttools", "cycler", "kiwisolver", "packaging", "python-dateutil",
                "pyparsing", "six"):
    datas += copy_metadata(package)

analysis = Analysis(
    [str(root/"launch_gui.pyw")],
    pathex=[str(root)],
    binaries=[],
    datas=datas,
    hiddenimports=["PySide6.QtSvg", "matplotlib.backends.backend_agg"],
    hookspath=[],
    hooksconfig={"matplotlib": {"backends": ["Agg"]}},
    runtime_hooks=[],
    excludes=["tkinter", "PyQt5", "PyQt6", "PySide2", "pytest", "_pytest"],
    noarchive=False,
)
archive = PYZ(analysis.pure)
exe = EXE(
    archive, analysis.scripts, [],
    exclude_binaries=True,
    name="ReflectometryLive",
    debug=False,
    strip=False,
    upx=False,
    console=False,
    disable_windowed_traceback=False,
    icon=str(root/"assets"/"reflectometry.ico"),
    version=str(root/"windows_version.txt"),
    contents_directory="_internal",
)
distribution = COLLECT(exe, analysis.binaries, analysis.datas,
                       strip=False, upx=False, name="ReflectometryLive")
