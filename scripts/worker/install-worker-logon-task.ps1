# ONE-TIME setup on the WORKER PC (run this ON the worker, not the main PC).
# Registers a scheduled task "JarvisWorkerStack" that runs at every logon and
# starts the worker stack (RotorQuant lane + LAN-bound LiteLLM on 4000).
#
# Usage (on the worker PC, in the OpenJarvis repo):
#   powershell -ExecutionPolicy Bypass -File scripts\worker\install-worker-logon-task.ps1
#
# Re-running is safe (the task is replaced). Remove with:
#   schtasks /Delete /TN "JarvisWorkerStack" /F

param(
    [string]$TaskName = "JarvisWorkerStack"
)

$ErrorActionPreference = "Stop"

$startScript = Join-Path $PSScriptRoot "start-worker-stack.ps1"
if (-not (Test-Path $startScript)) { throw "start-worker-stack.ps1 not found next to this installer." }

$action = "powershell.exe -ExecutionPolicy Bypass -WindowStyle Hidden -File `"$startScript`""
schtasks /Create /TN $TaskName /TR $action /SC ONLOGON /RL LIMITED /F
if ($LASTEXITCODE -ne 0) { throw "schtasks failed (exit $LASTEXITCODE)" }
Write-Host "Registered '$TaskName' to run at logon for the current user."

# LiteLLM must accept connections from the main PC over the LAN.
try {
    $rule = Get-NetFirewallRule -DisplayName "Jarvis Worker LiteLLM 4000" -ErrorAction SilentlyContinue
    if (-not $rule) {
        New-NetFirewallRule -DisplayName "Jarvis Worker LiteLLM 4000" -Direction Inbound `
            -Protocol TCP -LocalPort 4000 -Action Allow -Profile Private | Out-Null
        Write-Host "Added inbound firewall rule for TCP 4000 (Private profile)."
    } else {
        Write-Host "Firewall rule for TCP 4000 already present."
    }
} catch {
    Write-Warning "Could not add the firewall rule (needs an elevated shell). Run this once as admin:"
    Write-Warning "  New-NetFirewallRule -DisplayName 'Jarvis Worker LiteLLM 4000' -Direction Inbound -Protocol TCP -LocalPort 4000 -Action Allow -Profile Private"
}

Write-Host ""
Write-Host "Test it now without waiting for a logon:"
Write-Host "  schtasks /Run /TN `"$TaskName`""
Write-Host "Then from the main PC:  Test-NetConnection 192.168.1.191 -Port 4000"
