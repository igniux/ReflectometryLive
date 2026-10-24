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
# The Qt GUI hook collects optional PDF/virtual-keyboard plugins and their QML
# dependencies. This Widgets application uses neither; do not ship those
# unrelated modules (notably the separately licensed virtual keyboard).
unused_qt = {"Qt6Pdf.dll", "Qt6Qml.dll", "Qt6QmlMeta.dll", "Qt6QmlModels.dll",
             "Qt6QmlWorkerScript.dll", "Qt6Quick.dll", "Qt6VirtualKeyboard.dll",
             "QtPdf.pyd", "QtQml.pyd", "QtQuick.pyd", "QtVirtualKeyboard.pyd",
             "qpdf.dll", "qtvirtualkeyboardplugin.dll"}
analysis.binaries = [entry for entry in analysis.binaries
                     if Path(entry[0]).name not in unused_qt
                     and "/qml/" not in entry[0].replace("\\", "/").lower()]
analysis.datas = [entry for entry in analysis.datas
                  if "/qml/" not in entry[0].replace("\\", "/").lower()
                  and "qtvirtualkeyboard" not in entry[0].lower()]
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
