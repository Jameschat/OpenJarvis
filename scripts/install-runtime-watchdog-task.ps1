param(
    [string]$TaskName = "JarvisRuntimeWatchdog",
    [int]$IntervalMinutes = 5
)

$ErrorActionPreference = "Stop"

if ($IntervalMinutes -lt 1) {
    throw "IntervalMinutes must be at least 1."
}

$repoRoot = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot "..")).Path
# Run the watchdog under the venv's pythonw.exe (windowless host) rather than
# powershell.exe: Task Scheduler launching powershell.exe flashes a console
# every cycle even with -WindowStyle Hidden, which pops up on screen mid-game.
# pythonw has no console at all. The module is installed in the venv, so no
# PYTHONPATH or working dir is needed; child processes the watchdog spawns are
# kept windowless via CREATE_NO_WINDOW in runtime_watchdog.py. watch-jarvis-
# runtime.ps1 remains for manual/interactive runs.
$pythonw = Join-Path $repoRoot ".venv\Scripts\pythonw.exe"
if (-not (Test-Path -LiteralPath $pythonw)) {
    throw "venv pythonw.exe missing: $pythonw"
}

$reportPath = Join-Path $repoRoot "dist\runtime-watchdog-last.json"
New-Item -ItemType Directory -Force -Path (Split-Path -Parent $reportPath) | Out-Null

$action = "`"$pythonw`" -m openjarvis.tools.runtime_watchdog --json --report-path `"$reportPath`""

schtasks /Create /TN $TaskName /TR $action /SC MINUTE /MO $IntervalMinutes /RL LIMITED /F | Out-Host
if ($LASTEXITCODE -ne 0) {
    throw "Failed to create scheduled task $TaskName."
}

Write-Host "Installed $TaskName to run every $IntervalMinutes minute(s)."
Write-Host "Report: $reportPath"
