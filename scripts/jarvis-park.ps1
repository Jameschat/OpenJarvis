# Jarvis GAME MODE — park everything, give the machine back to the operator.
#
# Stops the full Jarvis runtime and prevents resurrection:
#   1. disables the JarvisRuntimeWatchdog scheduled task (belt-and-braces —
#      the watchdog is also app-gated and skips when the desktop app is closed)
#   2. kills jarvis serve / LiteLLM / qwen port proxy / agentmemory sidecar
#   3. shuts down WSL (frees the ~22GB VRAM the Qwen lane holds + VmmemWSL RAM)
#
# Resume with scripts\jarvis-resume.ps1 (or the Jarvis Resume desktop shortcut).

$ErrorActionPreference = 'Continue'

Write-Host "JARVIS GAME MODE - parking..." -ForegroundColor Cyan

schtasks /Change /TN "JarvisRuntimeWatchdog" /DISABLE | Out-Null
schtasks /End /TN "JarvisRuntimeWatchdog" 2>$null | Out-Null
Write-Host "  watchdog disabled"

$targets = Get-CimInstance Win32_Process | Where-Object {
    ($_.Name -in 'python.exe', 'pythonw.exe' -and
        $_.CommandLine -match 'jarvis\.exe.? serve|openjarvis\.desktop|litellm|qwen-wsl-port-proxy') -or
    ($_.Name -eq 'iii.exe')
}
foreach ($p in $targets) {
    Write-Host ("  stopping {0} ({1})" -f $p.ProcessId, $p.Name)
    Stop-Process -Id $p.ProcessId -Force -Confirm:$false -ErrorAction SilentlyContinue
}

wsl --shutdown
Start-Sleep -Seconds 4

$vram = (nvidia-smi --query-gpu=memory.used --format=csv,noheader) 2>$null
Write-Host ""
Write-Host "PARKED. GPU memory in use: $vram" -ForegroundColor Green
Write-Host "Resume: scripts\jarvis-resume.ps1 or the 'Jarvis Resume' shortcut."
