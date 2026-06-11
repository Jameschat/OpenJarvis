param(
    [string]$TaskName = "JarvisRuntimeWatchdog",
    [int]$IntervalMinutes = 5
)

$ErrorActionPreference = "Stop"

if ($IntervalMinutes -lt 1) {
    throw "IntervalMinutes must be at least 1."
}

$repoRoot = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot "..")).Path
$watchScript = Join-Path $repoRoot "scripts\watch-jarvis-runtime.ps1"
if (-not (Test-Path -LiteralPath $watchScript)) {
    throw "Watchdog script missing: $watchScript"
}

$reportPath = Join-Path $repoRoot "dist\runtime-watchdog-last.json"
New-Item -ItemType Directory -Force -Path (Split-Path -Parent $reportPath) | Out-Null

$action = "powershell.exe -ExecutionPolicy Bypass -WindowStyle Hidden -File `"$watchScript`" -Json -ReportPath `"$reportPath`""

schtasks /Create /TN $TaskName /TR $action /SC MINUTE /MO $IntervalMinutes /RL LIMITED /F | Out-Host
if ($LASTEXITCODE -ne 0) {
    throw "Failed to create scheduled task $TaskName."
}

Write-Host "Installed $TaskName to run every $IntervalMinutes minute(s)."
Write-Host "Report: $reportPath"
