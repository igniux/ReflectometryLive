param([string]$ExePath, [int]$Runs = 3)
$ErrorActionPreference = 'Stop'
if (-not $ExePath) {
    $shortcutShell = New-Object -ComObject WScript.Shell
    $shortcut = $shortcutShell.CreateShortcut([System.IO.Path]::GetFullPath((Join-Path $PSScriptRoot '..\Reflectometry Live.lnk')))
    $ExePath = $shortcut.TargetPath
}
$ExePath = (Resolve-Path -LiteralPath $ExePath).Path
$env:QT_QPA_PLATFORM = 'offscreen'
$workingDir = Join-Path $PSScriptRoot 'reports\packaging\exe_checks\unrelated working directory'
New-Item -ItemType Directory -Path $workingDir -Force | Out-Null
$startupReports = Join-Path $PSScriptRoot 'reports\startup'
New-Item -ItemType Directory -Path $startupReports -Force | Out-Null
$records = @()
for ($runIndex = 1; $runIndex -le $Runs; $runIndex++) {
    $reportPath = Join-Path $startupReports ('startup_exe_after_' + $runIndex + '.json')
    $watch = [System.Diagnostics.Stopwatch]::StartNew()
    $process = Start-Process -FilePath $ExePath -ArgumentList @('--startup-check', ('"' + $reportPath + '"')) -WorkingDirectory $workingDir -WindowStyle Hidden -PassThru
    if (-not $process.WaitForExit(30000)) { $process.Kill(); throw 'Startup diagnostic timed out.' }
    $watch.Stop()
    if ($process.ExitCode -ne 0) { throw ('Startup diagnostic failed: ' + $reportPath) }
    $report = Get-Content -Raw -Encoding UTF8 -LiteralPath $reportPath | ConvertFrom-Json
    if (-not $report.success -or -not $report.frozen) { throw 'Invalid startup report.' }
    $records += [pscustomobject]@{ FirstPaintSeconds=$report.first_paint_seconds;
                                 RawSpectrumSeconds=$report.raw_spectrum_seconds;
                                 WholeProcessSeconds=$watch.Elapsed.TotalSeconds;
                                 ScientificImportsDeferred=(-not $report.scipy_loaded_before_raw_spectrum) }
}
$summary = [pscustomobject]@{ Metric='Actual EXE entry point to first painted window and raw simulated spectrum';
                             Executable=$ExePath; HardwareAcquisition=$false; Runs=$records;
                             MeanFirstPaintSeconds=($records.FirstPaintSeconds | Measure-Object -Average).Average;
                             MeanRawSpectrumSeconds=($records.RawSpectrumSeconds | Measure-Object -Average).Average }
$summary | ConvertTo-Json -Depth 5 | Set-Content -Encoding UTF8 -LiteralPath (Join-Path $startupReports 'startup_exe_after.json')
$summary | ConvertTo-Json -Depth 5
