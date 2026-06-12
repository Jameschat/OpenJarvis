# Jarvis RESUME — bring the runtime back after game mode.
#
#   1. re-enables the JarvisRuntimeWatchdog scheduled task (app-gated:
#      it only self-heals while the desktop app is open)
#   2. triggers the canonical JarvisStudioStack starter (idempotent;
#      Qwen lane loads from WSL ext4 in seconds)

$ErrorActionPreference = 'Continue'

Write-Host "JARVIS RESUME - starting..." -ForegroundColor Cyan

schtasks /Change /TN "JarvisRuntimeWatchdog" /ENABLE | Out-Null
Write-Host "  watchdog enabled (app-gated)"

schtasks /Run /TN "JarvisStudioStack" | Out-Null
Write-Host "  JarvisStudioStack triggered"

# Wait briefly and report
$deadline = (Get-Date).AddSeconds(90)
$up = $false
while ((Get-Date) -lt $deadline) {
    try {
        $r = Invoke-WebRequest -Uri 'http://127.0.0.1:7710/studio/ping' -TimeoutSec 3 -UseBasicParsing
        if ($r.StatusCode -eq 200) { $up = $true; break }
    } catch { }
    Start-Sleep -Seconds 5
}
Write-Host ""
if ($up) {
    Write-Host "RESUMED. Backend answering on 7710; Qwen lane warming if cold." -ForegroundColor Green
} else {
    Write-Host "Stack triggered but 7710 not answering yet - give it a minute, or run 'jarvis doctor --quick'." -ForegroundColor Yellow
}
