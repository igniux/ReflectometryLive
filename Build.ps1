param([string]$PythonExe, [string]$OutputDirectory, [switch]$IncludeSdk)
$ErrorActionPreference = 'Stop'
$appDir = $PSScriptRoot
$sdkSource = Join-Path $appDir 'avaspecx64.dll'
if ($IncludeSdk -and -not (Test-Path -LiteralPath $sdkSource -PathType Leaf)) {
    throw 'To include the SDK, first install your authorized avaspecx64.dll beside run.py.'
}
if (-not $PythonExe) {
    foreach ($candidatePath in @((Join-Path $appDir '.venv\Scripts\python.exe'),
                                 (Join-Path $appDir '..\.reflectometry-venv\Scripts\python.exe'))) {
        if (Test-Path -LiteralPath $candidatePath) { $PythonExe = (Resolve-Path -LiteralPath $candidatePath).Path; break }
    }
}
if (-not $PythonExe) { throw 'Run Setup.bat first, or supply -PythonExe with a 64-bit interpreter.' }
& $PythonExe -c "import struct, PyInstaller; assert struct.calcsize('P') == 8, '64-bit Python is required'"
if ($LASTEXITCODE -ne 0) { throw 'Install requirements-build.txt into the selected Python environment first.' }
if (-not $OutputDirectory) {
    $OutputDirectory = Join-Path $appDir 'dist'
    if (Test-Path -LiteralPath (Join-Path $OutputDirectory 'ReflectometryLive')) {
        # Existing portable releases may contain measurements: never delete them.
        $OutputDirectory = Join-Path $OutputDirectory ('release-' + (Get-Date -Format 'yyyyMMdd-HHmmss-fff'))
    }
}
$OutputDirectory = [System.IO.Path]::GetFullPath($OutputDirectory)
$releaseDir = Join-Path $OutputDirectory 'ReflectometryLive'
if (Test-Path -LiteralPath $releaseDir) { throw 'The target release already exists. Choose a new -OutputDirectory to preserve its measurements.' }
$env:PYINSTALLER_CONFIG_DIR = Join-Path $appDir '.cache\pyinstaller'
$env:MPLCONFIGDIR = Join-Path $appDir '.cache\matplotlib'
$env:PYQTGRAPH_QT_LIB = 'PySide6'
& $PythonExe -m PyInstaller --distpath $OutputDirectory --workpath (Join-Path $appDir '.cache\build') (Join-Path $appDir 'ReflectometryLive.spec')
if ($LASTEXITCODE -ne 0) { throw 'Executable build failed.' }
$sdkCopy = Join-Path $releaseDir 'avaspecx64.dll'
if ($IncludeSdk) {
    Copy-Item -LiteralPath $sdkSource -Destination $sdkCopy
    if ((Get-FileHash -LiteralPath $sdkSource -Algorithm SHA256).Hash -ne (Get-FileHash -LiteralPath $sdkCopy -Algorithm SHA256).Hash) {
        throw 'Bundled SDK hash does not match the application-local SDK.'
    }
} else {
    Write-Output 'Vendor DLL omitted. Replay works; USB users must install their authorized SDK beside the EXE.'
}
Copy-Item -LiteralPath (Join-Path $appDir 'sdk_manifest.json') -Destination $releaseDir
Copy-Item -LiteralPath (Join-Path $appDir 'Portable_README.txt') -Destination (Join-Path $releaseDir 'README.txt')
Copy-Item -LiteralPath (Join-Path $appDir 'LICENSE') -Destination $releaseDir
Copy-Item -LiteralPath (Join-Path $appDir 'THIRD_PARTY.md') -Destination $releaseDir
$shortcutShell = New-Object -ComObject WScript.Shell
$shortcut = $shortcutShell.CreateShortcut((Join-Path $appDir 'Reflectometry Live.lnk'))
$shortcut.TargetPath = Join-Path $releaseDir 'ReflectometryLive.exe'
$shortcut.WorkingDirectory = $releaseDir
$shortcut.IconLocation = (Join-Path $releaseDir '_internal\assets\reflectometry.ico') + ',0'
$shortcut.Description = 'Reflectometry Live - standalone Windows application'
$shortcut.WindowStyle = 1
$shortcut.Save()
Write-Output ('Built executable: ' + (Join-Path $releaseDir 'ReflectometryLive.exe'))
Write-Output 'Copy the whole release folder when moving the program to another PC.'
