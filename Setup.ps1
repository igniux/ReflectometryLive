param(
    [string]$PythonExe,
    [string]$SdkSource
)
$ErrorActionPreference = 'Stop'
$appDir = $PSScriptRoot
$localSdk = Join-Path $appDir 'avaspecx64.dll'
if ($SdkSource) {
    if (-not (Test-Path -LiteralPath $SdkSource -PathType Leaf)) { throw 'SDK source unavailable. Supply -SdkSource with the path to avaspecx64.dll.' }
    $resolvedSdkSource = (Resolve-Path -LiteralPath $SdkSource).Path
    if ([System.IO.Path]::GetFullPath($resolvedSdkSource) -ne [System.IO.Path]::GetFullPath($localSdk)) {
        Copy-Item -LiteralPath $SdkSource -Destination $localSdk
        if ((Get-FileHash -LiteralPath $SdkSource -Algorithm SHA256).Hash -ne (Get-FileHash -LiteralPath $localSdk -Algorithm SHA256).Hash) {
            throw 'SDK copy verification failed.'
        }
    }
}
if (-not (Test-Path -LiteralPath $localSdk)) {
    Write-Host 'No local Avantes SDK: replay and fitting work without it. For USB acquisition, place avaspecx64.dll beside run.py or supply -SdkSource.'
}
if (-not $PythonExe) {
    $candidatePaths = @(
        (Join-Path $env:LOCALAPPDATA 'Python\pythoncore-3.14-64\python.exe'),
        (Join-Path $appDir '..\.reflectometry-venv\Scripts\python.exe')
    )
    foreach ($candidatePath in $candidatePaths) { if (Test-Path -LiteralPath $candidatePath) { $PythonExe = $candidatePath; break } }
    if (-not $PythonExe) {
        $pythonCommand = Get-Command python.exe -ErrorAction SilentlyContinue
        if ($pythonCommand) { $PythonExe = $pythonCommand.Source }
    }
}
if (-not $PythonExe) { throw 'Install 64-bit Python 3.14, or supply -PythonExe with its executable path.' }
& $PythonExe -c "import sys,struct; assert struct.calcsize('P')==8, '64-bit Python is required'; assert sys.version_info >= (3, 12), 'Python 3.12 or newer is required'"
if ($LASTEXITCODE -ne 0) { throw 'Python compatibility check failed.' }
$venvDir = Join-Path $appDir '.venv'
& $PythonExe -m venv $venvDir
if ($LASTEXITCODE -ne 0) { throw 'Unable to create the local Python environment.' }
$venvPython = Join-Path $venvDir 'Scripts\python.exe'
& $venvPython -m pip install --disable-pip-version-check -r (Join-Path $appDir 'requirements.txt')
if ($LASTEXITCODE -ne 0) { throw 'Dependency installation failed.' }
$shortcutShell = New-Object -ComObject WScript.Shell
$shortcut = $shortcutShell.CreateShortcut((Join-Path $appDir 'Launch.lnk'))
$shortcut.TargetPath = Join-Path $venvDir 'Scripts\pythonw.exe'
$shortcut.Arguments = '"' + (Join-Path $appDir 'launch_gui.pyw') + '"'
$shortcut.WorkingDirectory = $appDir
$shortcut.Description = 'Reflectometry Live'
$shortcut.IconLocation = (Join-Path $appDir 'assets\reflectometry.ico') + ',0'
$shortcut.WindowStyle = 1
$shortcut.Save()
Write-Host 'Setup complete. Double-click Launch.lnk. USB acquisition uses only the application-local SDK when installed.'
