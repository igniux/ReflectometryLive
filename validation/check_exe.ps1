param([string]$ExePath, [switch]$Relocate)
$ErrorActionPreference = 'Stop'
$appDir = [System.IO.Path]::GetFullPath((Join-Path $PSScriptRoot '..'))
if (-not $ExePath) {
    $shortcutShell = New-Object -ComObject WScript.Shell
    $shortcut = $shortcutShell.CreateShortcut((Join-Path $appDir 'Reflectometry Live.lnk'))
    $ExePath = $shortcut.TargetPath
}
$ExePath = (Resolve-Path -LiteralPath $ExePath).Path
$releaseDir = Split-Path -Parent $ExePath
$originalRelease = $releaseDir
$checkRoot = Join-Path $PSScriptRoot 'reports\packaging\exe_checks'
$workingDir = Join-Path $checkRoot 'unrelated working directory'
New-Item -ItemType Directory -Path $workingDir -Force | Out-Null
$env:QT_QPA_PLATFORM = 'offscreen'
$env:PYQTGRAPH_QT_LIB = 'PySide6'
$env:PYTHONPATH = ''
try {
    if ($Relocate) {
        $temporaryRoot = Join-Path $appDir ('.cache\relocation-' + (Get-Date -Format 'yyyyMMdd-HHmmss-fff'))
        $temporaryRelease = [System.IO.Path]::GetFullPath((Join-Path $temporaryRoot 'ReflectometryLive'))
        # Only move the explicitly supplied generated release within this project.
        $allowedPrefix = $appDir.TrimEnd('\') + '\'
        if (-not $originalRelease.StartsWith($allowedPrefix, [System.StringComparison]::OrdinalIgnoreCase) -or
            -not $temporaryRelease.StartsWith($allowedPrefix, [System.StringComparison]::OrdinalIgnoreCase)) {
            throw 'Relocation targets must remain inside this application workspace.'
        }
        if (Test-Path -LiteralPath $temporaryRelease) { throw 'Temporary relocation destination already exists.' }
        New-Item -ItemType Directory -Path $temporaryRoot | Out-Null
        Move-Item -LiteralPath $originalRelease -Destination $temporaryRelease
        $releaseDir = $temporaryRelease
        $ExePath = Join-Path $releaseDir 'ReflectometryLive.exe'
    }
    $reportName = if ($Relocate) { 'relocated.json' } else { 'packaged.json' }
    $reportPath = Join-Path $checkRoot $reportName
    $process = Start-Process -FilePath $ExePath -ArgumentList @('--self-test', ('"' + $reportPath + '"')) -WorkingDirectory $workingDir -WindowStyle Hidden -PassThru
    if (-not $process.WaitForExit(60000)) {
        $process.Kill()
        throw 'The executable self-test timed out. Inspect the application-local .cache\launch.log.'
    }
    if ($process.ExitCode -ne 0 -or -not (Test-Path -LiteralPath $reportPath)) {
        throw ('Executable self-test failed with exit code ' + $process.ExitCode + '. Inspect ' + $reportPath)
    }
    $report = Get-Content -Raw -Encoding UTF8 -LiteralPath $reportPath | ConvertFrom-Json
    if (-not $report.success -or -not $report.frozen -or -not $report.workers_closed) { throw ('Invalid executable validation report: ' + $reportPath) }
    $expectedSdk = Join-Path $releaseDir 'avaspecx64.dll'
    if ($report.sdk_path -ne $expectedSdk) { throw 'Executable loaded an SDK outside its release folder.' }
    Write-Output ('Executable validation passed: ' + $reportPath)
    Write-Output ('Distinct replay frames in 3 seconds: ' + $report.distinct_replay_frames_in_3_seconds)
} finally {
    if ($Relocate -and $releaseDir -ne $originalRelease -and (Test-Path -LiteralPath $releaseDir)) {
        if (Test-Path -LiteralPath $originalRelease) { throw 'Original release path is unexpectedly occupied; the tested release remains at the temporary path.' }
        # Both absolute paths were verified to stay inside the application above.
        Move-Item -LiteralPath $releaseDir -Destination $originalRelease
    }
}
