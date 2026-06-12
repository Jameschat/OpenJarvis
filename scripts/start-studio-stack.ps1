# Starts the full Jarvis Studio backend stack, detached from the calling session.
# Idempotent: each lane is skipped if already healthy.
#   1. Qwen fast lane (WSL MTP/Froggeric llama-server, port 8084 - primary)
#      Falls back to Windows-native BeeLlama plain decode (8082) if WSL fails.
#   2. LiteLLM proxy (port 4000, direct from .venv - avoids the uv-run sync hang)
#   3. Jarvis backend (jarvis serve, port 7710, env loaded from jarvis.bat)
# Safe to run repeatedly; logs land in dist\.
# NEVER co-load two 27B lanes on the 24GB 4090: VRAM overcommit garbles output.

param(
    [string]$RepoRoot = "E:\Claude\OpenJarvis",
    [string]$WslDistro = "JarvisUbuntu",
    [int]$QwenWaitSeconds = 1200
)

$ErrorActionPreference = "Stop"

# At boot/logon this task can fire before the E: drive (and WSL service) are
# ready. Wait for the repo root instead of dying with an unwritable transcript.
$bootDeadline = (Get-Date).AddSeconds(180)
while (-not (Test-Path $RepoRoot) -and (Get-Date) -lt $bootDeadline) { Start-Sleep -Seconds 5 }
if (-not (Test-Path $RepoRoot)) { exit 2 }

$logDir = Join-Path $RepoRoot "dist"
New-Item -ItemType Directory -Force -Path $logDir | Out-Null
Start-Transcript -Path (Join-Path $logDir "start-studio-stack.transcript.log") -Append | Out-Null
trap { Stop-Transcript | Out-Null; break }

# Warm the WSL service before the lane launch relies on it.
try { wsl.exe -d $WslDistro -- true 2>$null | Out-Null } catch { Start-Sleep -Seconds 10 }

function Test-Http {
    param([string]$Url, [int]$TimeoutSec = 3)
    try {
        $null = Invoke-WebRequest -Uri $Url -TimeoutSec $TimeoutSec -UseBasicParsing
        return $true
    } catch { return $false }
}

# --- Load env vars from jarvis.bat (secrets stay out of logs) ---
Select-String -Path (Join-Path $RepoRoot "jarvis.bat") -Pattern '^\s*set\s+([A-Z_]+)=(.+)$' | ForEach-Object {
    $m = $_.Line -replace '^\s*set\s+', ''
    $i = $m.IndexOf('=')
    if ($i -gt 0) { Set-Item -Path "env:$($m.Substring(0,$i))" -Value $m.Substring($i + 1) }
}

# --- Load secrets from %USERPROFILE%\.openjarvis\jarvis.env (migrated out of jarvis.bat) ---
$jarvisEnvFile = Join-Path $env:USERPROFILE '.openjarvis\jarvis.env'
if (Test-Path $jarvisEnvFile) {
    Get-Content $jarvisEnvFile | Where-Object { $_ -and ($_ -notmatch '^\s*#') } | ForEach-Object {
        $i = $_.IndexOf('=')
        if ($i -gt 0) { Set-Item -Path "env:$($_.Substring(0,$i))" -Value $_.Substring($i + 1) }
    }
}
$env:PYTHONIOENCODING = 'utf-8'

# --- 1. Qwen fast lane (WSL MTP/Froggeric, 8084 primary) ---
$qwenHealthy = $false
if (Test-Http "http://127.0.0.1:8084/health") {
    Write-Host "[qwen] WSL MTP lane already healthy on 8084"
    $qwenHealthy = $true
} else {
    $qwenScript = Join-Path $RepoRoot "scripts\start-qwen-mtp-froggeric-wsl.ps1"
    if (Test-Path $qwenScript) {
        & powershell.exe -ExecutionPolicy Bypass -File $qwenScript -WslDistro $WslDistro -WaitSeconds $QwenWaitSeconds
        $qwenHealthy = Test-Http "http://127.0.0.1:8084/health"
    } else {
        Write-Warning "[qwen] WSL launcher missing: $qwenScript"
    }

    if ($qwenHealthy) {
        Write-Host "[qwen] WSL MTP lane healthy on 8084"
    } else {
        Write-Warning "[qwen] WSL lane failed; falling back to native BeeLlama (8082, plain decode - short prompts only)"
        $beellamaScript = Join-Path $RepoRoot "scripts\start-qwen-beellama-dflash-service.ps1"
        & powershell.exe -ExecutionPolicy Bypass -File $beellamaScript -ContextTokens 16384 -WaitSeconds 420
        if (Test-Http "http://127.0.0.1:8082/health" -TimeoutSec 5) {
            Write-Host "[qwen] BeeLlama fallback healthy on 8082 (LiteLLM falls through to it when 8084 is down)"
        } else {
            Write-Warning "[qwen] no local Qwen lane is up; chat will fall back to Ollama"
        }
    }
}

# --- 2. LiteLLM proxy (4000) ---
if (Test-Http "http://127.0.0.1:4000/health/liveliness") {
    Write-Host "[litellm] already healthy on 4000"
} else {
    $litellm = Join-Path $RepoRoot ".venv\Scripts\litellm.exe"
    Start-Process -FilePath $litellm `
        -ArgumentList "--config", "configs/litellm.yaml", "--port", "4000" `
        -WorkingDirectory $RepoRoot -WindowStyle Hidden `
        -RedirectStandardOutput (Join-Path $logDir "litellm-4000.log") `
        -RedirectStandardError (Join-Path $logDir "litellm-4000.err.log")
    $deadline = (Get-Date).AddSeconds(90)
    while ((Get-Date) -lt $deadline -and -not (Test-Http "http://127.0.0.1:4000/health/liveliness")) { Start-Sleep -Seconds 4 }
    if (Test-Http "http://127.0.0.1:4000/health/liveliness") {
        Write-Host "[litellm] healthy on 4000"
    } else {
        throw "[litellm] failed to start - see dist\litellm-4000.err.log"
    }
}

# --- 3. Jarvis backend (7710) ---
if (Test-Http "http://127.0.0.1:7710/studio/ping") {
    Write-Host "[backend] already healthy on 7710"
} else {
    $jarvis = Join-Path $RepoRoot ".venv\Scripts\jarvis.exe"
    Start-Process -FilePath $jarvis `
        -ArgumentList "serve", "--port", "7710", "--model", "qwen3.6-27b-local", "--agent", "simple" `
        -WorkingDirectory $RepoRoot -WindowStyle Hidden `
        -RedirectStandardOutput (Join-Path $logDir "jarvis-serve-7710.log") `
        -RedirectStandardError (Join-Path $logDir "jarvis-serve-7710.err.log")
    $deadline = (Get-Date).AddSeconds(150)
    while ((Get-Date) -lt $deadline -and -not (Test-Http "http://127.0.0.1:7710/studio/ping")) { Start-Sleep -Seconds 4 }
    if (Test-Http "http://127.0.0.1:7710/studio/ping") {
        Write-Host "[backend] healthy on 7710"
    } else {
        throw "[backend] failed to start - see dist\jarvis-serve-7710.err.log"
    }
}

Write-Host ""
Write-Host "Studio stack status:"
foreach ($svc in @(
    @{ name = "Jarvis backend 7710"; url = "http://127.0.0.1:7710/studio/ping" },
    @{ name = "LiteLLM proxy 4000"; url = "http://127.0.0.1:4000/health/liveliness" },
    @{ name = "Qwen fast lane 8084"; url = "http://127.0.0.1:8084/health" },
    @{ name = "Qwen fallback 8082"; url = "http://127.0.0.1:8082/health" },
    @{ name = "Ollama 11434"; url = "http://127.0.0.1:11434/api/version" }
)) {
    $state = if (Test-Http $svc.url) { "OK" } else { "DOWN" }
    Write-Host ("  {0,-22} {1}" -f $svc.name, $state)
}
Stop-Transcript | Out-Null
