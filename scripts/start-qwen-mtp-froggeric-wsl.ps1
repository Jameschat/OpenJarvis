param(
    [string]$WslDistro = "JarvisUbuntu",
    [string]$Server = "/root/llama.cpp-turboq-mtp/build/bin/llama-server",
    [string]$Model = "/mnt/e/Claude/models/Qwen3.6-27B-Q4_K_M-mtp.gguf",
    [string]$ChatTemplate = "/mnt/e/Claude/OpenJarvis/configs/qwen/froggeric-chat-template.jinja",
    [int]$Port = 8084,
    [int]$ContextTokens = 16384,
    [int]$DraftMax = 3,
    [string]$CacheTypeK = "q4_0",
    [string]$CacheTypeV = "q4_0",
    [int]$Threads = 24,
    [int]$BatchSize = 4092,
    [int]$UbatchSize = 1024,
    [int]$WaitSeconds = 300
)

$ErrorActionPreference = "Stop"

function Test-PortOpen {
    param([int]$LocalPort)
    return [bool](Get-NetTCPConnection -LocalPort $LocalPort -State Listen -ErrorAction SilentlyContinue)
}

function Test-QwenHealth {
    param([int]$LocalPort)
    try {
        $health = Invoke-RestMethod -Uri "http://127.0.0.1:$LocalPort/health" -TimeoutSec 3
        return ($health.status -eq "ok")
    } catch {
        return $false
    }
}

function Invoke-WslBashExit {
    param([string]$Command)
    & wsl.exe -d $WslDistro -- bash -lc $Command | Out-Null
    return $LASTEXITCODE
}

function Test-WslPortOpen {
    param([int]$LocalPort)
    $probe = "ss -ltn 2>/dev/null | grep -Eq '(^|[[:space:]])[^[:space:]]*:$LocalPort[[:space:]]'"
    return ((Invoke-WslBashExit -Command $probe) -eq 0)
}

function Test-WslQwenHealth {
    param([int]$LocalPort)
    $probe = "curl -fsS --max-time 3 http://127.0.0.1:$LocalPort/health >/dev/null"
    return ((Invoke-WslBashExit -Command $probe) -eq 0)
}

function Get-WslHostIp {
    $ip = (& wsl.exe -d $WslDistro -- bash -lc "hostname -I | awk '{print `$1}'" 2>$null)
    return ($ip | Select-Object -First 1).Trim()
}

function Start-WindowsQwenBridge {
    param([int]$LocalPort)
    $wslIp = Get-WslHostIp
    if (-not $wslIp) {
        throw "Qwen is healthy inside WSL, but Jarvis could not resolve the WSL IP for Windows bridge startup."
    }

    $proxyScript = Join-Path $PSScriptRoot "qwen-wsl-port-proxy.py"
    if (-not (Test-Path -LiteralPath $proxyScript)) {
        throw "Qwen WSL bridge script missing: $proxyScript"
    }

    $repoRoot = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot "..")).Path
    $python = Join-Path $repoRoot ".venv\Scripts\python.exe"
    if (-not (Test-Path -LiteralPath $python)) {
        $python = "python.exe"
    }

    $logDir = Join-Path $repoRoot "dist"
    New-Item -ItemType Directory -Force -Path $logDir | Out-Null
    $proxyStdout = Join-Path $logDir "qwen-wsl-port-proxy-8084.log"
    $proxyStderr = Join-Path $logDir "qwen-wsl-port-proxy-8084.err.log"
    $args = @(
        $proxyScript,
        "--listen-host", "127.0.0.1",
        "--listen-port", [string]$LocalPort,
        "--target-host", $wslIp,
        "--target-port", [string]$LocalPort
    )
    $process = Start-Process -FilePath $python -ArgumentList $args -WindowStyle Hidden -RedirectStandardOutput $proxyStdout -RedirectStandardError $proxyStderr -PassThru
    Write-Host "Started Qwen Windows bridge PID $($process.Id) on 127.0.0.1:$LocalPort -> ${wslIp}:$LocalPort"
}

function Stop-WslPort {
    param([int]$LocalPort)
    $cleanup = @'
if command -v fuser >/dev/null 2>&1; then
  fuser -k __PORT__/tcp >/dev/null 2>&1 || true
else
  pids=$(ss -ltnp 2>/dev/null | awk '/:__PORT__ / {print $NF}' | sed -n 's/.*pid=\([0-9]*\).*/\1/p' | sort -u)
  for pid in $pids; do kill -TERM "$pid" 2>/dev/null || true; done
  sleep 1
  for pid in $pids; do kill -KILL "$pid" 2>/dev/null || true; done
fi
'@.Replace("__PORT__", [string]$LocalPort)
    [void](Invoke-WslBashExit -Command $cleanup)
}

if (Test-WslQwenHealth -LocalPort $Port) {
    if (Test-QwenHealth -LocalPort $Port) {
        Write-Host "Qwen MTP Froggeric server already healthy on port $Port"
        exit 0
    }
    Start-WindowsQwenBridge -LocalPort $Port
    Start-Sleep -Seconds 2
    if (Test-QwenHealth -LocalPort $Port) {
        Write-Host "Qwen MTP Froggeric ready through Windows bridge at http://127.0.0.1:$Port/v1"
        exit 0
    }
    throw "Qwen is healthy inside WSL, but Windows bridge did not become healthy on port $Port."
}

if (Test-WslPortOpen -LocalPort $Port) {
    Write-Host "Clearing stale WSL listener on port $Port"
    Stop-WslPort -LocalPort $Port
    Start-Sleep -Seconds 2
    if (Test-WslPortOpen -LocalPort $Port) {
        throw "WSL port $Port is still occupied after cleanup."
    }
}

if (Test-PortOpen -LocalPort $Port) {
    if (Test-QwenHealth -LocalPort $Port) {
        Write-Host "Qwen MTP Froggeric server already healthy on port $Port"
        exit 0
    }
    throw "Windows port $Port is occupied but Qwen health is not responding. Close the stale process and retry."
}

$repoRoot = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot "..")).Path
$logDir = Join-Path $repoRoot "dist"
New-Item -ItemType Directory -Force -Path $logDir | Out-Null
$stdout = Join-Path $logDir "qwen-mtp-froggeric-8084.log"
$stderr = Join-Path $logDir "qwen-mtp-froggeric-8084.err.log"
$launchScript = Join-Path $logDir "qwen-mtp-froggeric-8084.sh"

$bashCommand = @"
set -euo pipefail
exec $Server \
  -m $Model \
  --host 0.0.0.0 \
  --port $Port \
  -np 1 \
  --threads $Threads \
  --threads-batch $Threads \
  --batch-size $BatchSize \
  --ubatch-size $UbatchSize \
  --ctx-size $ContextTokens \
  -ngl 99 \
  --flash-attn on \
  --cache-type-k $CacheTypeK \
  --cache-type-v $CacheTypeV \
  --spec-type mtp \
  --spec-draft-n-max $DraftMax \
  --jinja \
  --chat-template-file $ChatTemplate \
  --reasoning off \
  --no-cache-prompt \
  --cache-ram 0 \
  --no-warmup \
  --no-mmap \
  --temp 0.6 \
  --top-k 20 \
  --top-p 0.95 \
  --min-p 0.0 \
  --presence-penalty 0.0 \
  --repeat-penalty 1.0
"@

$bashCommand -replace "`r`n", "`n" | Set-Content -LiteralPath $launchScript -Encoding ASCII
$drive = $launchScript.Substring(0, 1).ToLowerInvariant()
$rest = $launchScript.Substring(2).Replace("\", "/")
$wslLaunchScript = "/mnt/$drive$rest"
if (-not $wslLaunchScript) {
    throw "Could not translate Qwen launch script path for WSL: $launchScript"
}

$argList = @("-d", $WslDistro, "--", "bash", $wslLaunchScript)
$process = Start-Process -FilePath "wsl.exe" -ArgumentList $argList -WindowStyle Hidden -RedirectStandardOutput $stdout -RedirectStandardError $stderr -PassThru
Write-Host "Started Qwen MTP Froggeric PID $($process.Id) on port $Port"

$deadline = (Get-Date).AddSeconds($WaitSeconds)
while ((Get-Date) -lt $deadline) {
    Start-Sleep -Seconds 2
    if ($process.HasExited) {
        $tail = ""
        if (Test-Path -LiteralPath $stderr) {
            $tail = (Get-Content -LiteralPath $stderr -Tail 80) -join "`n"
        }
        throw "Qwen MTP Froggeric server exited during startup. $tail"
    }
    if (Test-QwenHealth -LocalPort $Port) {
        Write-Host "Qwen MTP Froggeric ready at http://127.0.0.1:$Port/v1"
        exit 0
    }
    if ((Test-WslQwenHealth -LocalPort $Port) -and -not (Test-PortOpen -LocalPort $Port)) {
        Start-WindowsQwenBridge -LocalPort $Port
        Start-Sleep -Seconds 2
        if (Test-QwenHealth -LocalPort $Port) {
            Write-Host "Qwen MTP Froggeric ready through Windows bridge at http://127.0.0.1:$Port/v1"
            exit 0
        }
    }
}

throw "Qwen MTP Froggeric did not become healthy within $WaitSeconds seconds. See $stderr"
