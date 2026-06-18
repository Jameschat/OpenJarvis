param(
    [string]$WslDistro = "JarvisUbuntu",
    [string]$Server = "/root/llama.cpp-turboq-mtp/build/bin/llama-server",
    [string]$Model = "/root/models/Qwen3-Coder-30B-A3B-Instruct-UD-Q4_K_XL.gguf",
    [string]$FallbackModel = "/mnt/e/Claude/models/Qwen3-Coder-30B-A3B-Instruct-UD-Q4_K_XL.gguf",
    [int]$Port = 8088,
    [int]$ContextTokens = 98304,
    [string]$CacheTypeK = "q4_0",
    [string]$CacheTypeV = "q4_0",
    [int]$Threads = 24,
    [int]$BatchSize = 4092,
    [int]$UbatchSize = 1024,
    [int]$WaitSeconds = 300
)

# Qwen3-Coder-30B-A3B-Instruct benchmark/alternate lane (sibling of the 27B
# froggeric launcher). Differences vs the 27B lane:
#   - MoE model, ~3B active: NO --spec-type mtp (it has no MTP draft head).
#   - Uses the model's BUILT-IN chat template via --jinja (no froggeric template).
#   - Qwen3-Coder recommended sampling (temp 0.7 / top-p 0.8 / top-k 20 / rep 1.05).
#   - Native 256K context; default here is 65536 (64K) to leave VRAM headroom on
#     the 24GB 4090 (q4 KV ~1.8GB @64K, ~3.6GB @128K; 256K won't fit with Q4
#     weights). Raise -ContextTokens up to ~131072 (128K) if you need it.
# Promotion to live routing is manual (operator): point configs/litellm.yaml at
# this lane, or run it as an opt-in via configs/litellm.qwen3-coder-30b-a3b.yaml.

$ErrorActionPreference = "Stop"

function Test-PortOpen {
    param([int]$LocalPort)
    return [bool](Get-NetTCPConnection -LocalPort $LocalPort -State Listen -ErrorAction SilentlyContinue)
}

function Test-LaneHealth {
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

function Convert-ToBashSingleQuoted {
    param([string]$Value)
    return "'" + $Value.Replace("'", "'\''") + "'"
}

function Test-WslFile {
    param([string]$Path)
    $quoted = Convert-ToBashSingleQuoted -Value $Path
    return ((Invoke-WslBashExit -Command "test -f $quoted") -eq 0)
}

function Test-WslPortOpen {
    param([int]$LocalPort)
    $probe = "ss -ltn 2>/dev/null | grep -Eq '(^|[[:space:]])[^[:space:]]*:$LocalPort[[:space:]]'"
    return ((Invoke-WslBashExit -Command $probe) -eq 0)
}

function Test-WslLaneHealth {
    param([int]$LocalPort)
    $probe = "curl -fsS --max-time 3 http://127.0.0.1:$LocalPort/health >/dev/null"
    return ((Invoke-WslBashExit -Command $probe) -eq 0)
}

function Get-WslHostIp {
    $ip = (& wsl.exe -d $WslDistro -- bash -lc "hostname -I | awk '{print `$1}'" 2>$null)
    return ($ip | Select-Object -First 1).Trim()
}

function Start-WindowsLaneBridge {
    param([int]$LocalPort)
    $wslIp = Get-WslHostIp
    if (-not $wslIp) {
        throw "Lane is healthy inside WSL, but the WSL IP could not be resolved for the Windows bridge."
    }
    $proxyScript = Join-Path $PSScriptRoot "qwen-wsl-port-proxy.py"
    if (-not (Test-Path -LiteralPath $proxyScript)) {
        throw "WSL bridge script missing: $proxyScript"
    }
    $repoRoot = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot "..")).Path
    $python = Join-Path $repoRoot ".venv\Scripts\python.exe"
    if (-not (Test-Path -LiteralPath $python)) { $python = "python.exe" }
    $logDir = Join-Path $repoRoot "dist"
    New-Item -ItemType Directory -Force -Path $logDir | Out-Null
    $proxyStdout = Join-Path $logDir "qwen3-coder-30b-a3b-proxy-$LocalPort.log"
    $proxyStderr = Join-Path $logDir "qwen3-coder-30b-a3b-proxy-$LocalPort.err.log"
    $proxyArgs = @(
        $proxyScript,
        "--listen-host", "127.0.0.1",
        "--listen-port", [string]$LocalPort,
        "--target-host", $wslIp,
        "--target-port", [string]$LocalPort
    )
    $process = Start-Process -FilePath $python -ArgumentList $proxyArgs -WindowStyle Hidden -RedirectStandardOutput $proxyStdout -RedirectStandardError $proxyStderr -PassThru
    Write-Host "Started lane Windows bridge PID $($process.Id) on 127.0.0.1:$LocalPort -> ${wslIp}:$LocalPort"
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

if (Test-WslLaneHealth -LocalPort $Port) {
    if (Test-LaneHealth -LocalPort $Port) {
        Write-Host "Qwen3-Coder-30B-A3B already healthy on port $Port"
        exit 0
    }
    Start-WindowsLaneBridge -LocalPort $Port
    Start-Sleep -Seconds 2
    if (Test-LaneHealth -LocalPort $Port) {
        Write-Host "Qwen3-Coder-30B-A3B ready through Windows bridge at http://127.0.0.1:$Port/v1"
        exit 0
    }
    throw "Lane is healthy inside WSL, but the Windows bridge did not become healthy on port $Port."
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
    if (Test-LaneHealth -LocalPort $Port) {
        Write-Host "Qwen3-Coder-30B-A3B already healthy on port $Port"
        exit 0
    }
    throw "Windows port $Port is occupied but lane health is not responding. Close the stale process and retry."
}

$repoRoot = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot "..")).Path
$logDir = Join-Path $repoRoot "dist"
New-Item -ItemType Directory -Force -Path $logDir | Out-Null
$stdout = Join-Path $logDir "qwen3-coder-30b-a3b-$Port.log"
$stderr = Join-Path $logDir "qwen3-coder-30b-a3b-$Port.err.log"
$launchScript = Join-Path $logDir "qwen3-coder-30b-a3b-$Port.sh"

$modelToUse = $Model
if (-not (Test-WslFile -Path $modelToUse)) {
    if ($FallbackModel -and (Test-WslFile -Path $FallbackModel)) {
        Write-Warning "ext4 model missing at $Model; falling back to $FallbackModel"
        $modelToUse = $FallbackModel
    } else {
        throw "Qwen3-Coder-30B-A3B model not found in WSL at $Model or fallback $FallbackModel"
    }
}

$bashCommand = @"
set -euo pipefail
exec $Server \
  -m $modelToUse \
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
  --jinja \
  --reasoning off \
  --no-mmap \
  --temp 0.7 \
  --top-k 20 \
  --top-p 0.8 \
  --min-p 0.0 \
  --presence-penalty 0.0 \
  --repeat-penalty 1.05
"@

$bashCommand -replace "`r`n", "`n" | Set-Content -LiteralPath $launchScript -Encoding ASCII
$drive = $launchScript.Substring(0, 1).ToLowerInvariant()
$rest = $launchScript.Substring(2).Replace("\", "/")
$wslLaunchScript = "/mnt/$drive$rest"

$argList = @("-d", $WslDistro, "--", "bash", $wslLaunchScript)
$process = Start-Process -FilePath "wsl.exe" -ArgumentList $argList -WindowStyle Hidden -RedirectStandardOutput $stdout -RedirectStandardError $stderr -PassThru
Write-Host "Started Qwen3-Coder-30B-A3B PID $($process.Id) on port $Port (ctx $ContextTokens)"

$deadline = (Get-Date).AddSeconds($WaitSeconds)
while ((Get-Date) -lt $deadline) {
    Start-Sleep -Seconds 2
    if ($process.HasExited) {
        $tail = ""
        if (Test-Path -LiteralPath $stderr) {
            $tail = (Get-Content -LiteralPath $stderr -Tail 80) -join "`n"
        }
        throw "Qwen3-Coder-30B-A3B server exited during startup. $tail"
    }
    if (Test-LaneHealth -LocalPort $Port) {
        Write-Host "Qwen3-Coder-30B-A3B ready at http://127.0.0.1:$Port/v1"
        exit 0
    }
    if ((Test-WslLaneHealth -LocalPort $Port) -and -not (Test-PortOpen -LocalPort $Port)) {
        Start-WindowsLaneBridge -LocalPort $Port
        Start-Sleep -Seconds 2
        if (Test-LaneHealth -LocalPort $Port) {
            Write-Host "Qwen3-Coder-30B-A3B ready through Windows bridge at http://127.0.0.1:$Port/v1"
            exit 0
        }
    }
}

throw "Qwen3-Coder-30B-A3B did not become healthy within $WaitSeconds seconds. See $stderr"
