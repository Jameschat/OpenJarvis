# Starts the Jarvis WORKER PC stack, detached from the calling session.
# Intended to run on the second PC (the remote worker at 192.168.1.191):
#   1. Qwen RotorQuant 35B-A3B lane inside WSL (port 8085), launched with
#      setsid so it survives any parent process dying.
#   2. LiteLLM proxy on port 4000 bound to the LAN, exposing the lane as
#      qwen3.6-35b-a3b-rotorquant for the main PC's LiteLLM to route to.
# Idempotent: each piece is skipped if already healthy. Logs land in dist\.

param(
    [string]$RepoRoot = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot "..\..")).Path,
    [string]$WslDistro = "JarvisUbuntu",
    [string]$LiteLlmConfig = "configs/litellm.qwen-rotorquant.yaml",
    [int]$LanePort = 8085,
    [int]$ProxyPort = 4000,
    [int]$LaneWaitSeconds = 1800
)

$ErrorActionPreference = "Stop"
$logDir = Join-Path $RepoRoot "dist"
New-Item -ItemType Directory -Force -Path $logDir | Out-Null
Start-Transcript -Path (Join-Path $logDir "start-worker-stack.transcript.log") -Append | Out-Null
trap { Stop-Transcript | Out-Null; break }

function Test-Http {
    param([string]$Url, [int]$TimeoutSec = 3)
    try { $null = Invoke-WebRequest -Uri $Url -TimeoutSec $TimeoutSec -UseBasicParsing; return $true } catch { return $false }
}

# --- 1. RotorQuant lane (8085, WSL) ---
if (Test-Http "http://127.0.0.1:$LanePort/health") {
    Write-Host "[lane] RotorQuant already healthy on $LanePort"
} else {
    # The canonical lane script generates dist\qwen-rotorquant-8085 launch args.
    # We launch through bash setsid so the server survives parent death and
    # keeps the WSL VM alive (same hardening as the main PC's 8084 lane).
    $laneScript = Join-Path $RepoRoot "scripts\start-qwen-rotorquant-wsl.ps1"
    if (-not (Test-Path $laneScript)) { throw "[lane] missing $laneScript" }
    & powershell.exe -ExecutionPolicy Bypass -File $laneScript -WaitSeconds $LaneWaitSeconds
    if (-not (Test-Http "http://127.0.0.1:$LanePort/health" -TimeoutSec 5)) {
        Write-Warning "[lane] RotorQuant did not become healthy; LiteLLM will still start so the port answers with errors instead of timeouts."
    } else {
        Write-Host "[lane] RotorQuant healthy on $LanePort"
    }
}

# --- 2. LiteLLM proxy (4000, LAN-bound) ---
if (Test-Http "http://127.0.0.1:$ProxyPort/health/liveliness") {
    Write-Host "[litellm] already healthy on $ProxyPort"
} else {
    $litellm = Join-Path $RepoRoot ".venv\Scripts\litellm.exe"
    if (-not (Test-Path $litellm)) { $litellm = "litellm" }  # fall back to PATH
    $env:PYTHONIOENCODING = 'utf-8'
    Start-Process -FilePath $litellm `
        -ArgumentList "--config", $LiteLlmConfig, "--host", "0.0.0.0", "--port", "$ProxyPort" `
        -WorkingDirectory $RepoRoot -WindowStyle Hidden `
        -RedirectStandardOutput (Join-Path $logDir "litellm-worker-$ProxyPort.log") `
        -RedirectStandardError (Join-Path $logDir "litellm-worker-$ProxyPort.err.log")
    $deadline = (Get-Date).AddSeconds(90)
    while ((Get-Date) -lt $deadline -and -not (Test-Http "http://127.0.0.1:$ProxyPort/health/liveliness")) { Start-Sleep -Seconds 4 }
    if (Test-Http "http://127.0.0.1:$ProxyPort/health/liveliness") {
        Write-Host "[litellm] healthy on $ProxyPort (LAN-bound)"
    } else {
        throw "[litellm] failed to start - see dist\litellm-worker-$ProxyPort.err.log"
    }
}

Write-Host ""
Write-Host "Worker stack status:"
foreach ($svc in @(
    @{ name = "RotorQuant lane $LanePort"; url = "http://127.0.0.1:$LanePort/health" },
    @{ name = "LiteLLM proxy $ProxyPort"; url = "http://127.0.0.1:$ProxyPort/health/liveliness" }
)) {
    $state = if (Test-Http $svc.url) { "OK" } else { "DOWN" }
    Write-Host ("  {0,-24} {1}" -f $svc.name, $state)
}
Stop-Transcript | Out-Null
