param(
    [string]$WslDistro = "JarvisUbuntu",
    [string]$Server = "/root/llama.cpp-turboq-mtp/build/bin/llama-server",
    [string]$Model = "/root/models/GLM-4.7-Flash-UD-Q4_K_XL.gguf",
    [string]$FallbackModel = "/mnt/e/Claude/models/GLM-4.7-Flash-UD-Q4_K_XL.gguf",
    [int]$Port = 8084,
    [int]$ContextTokens = 32768,
    [string]$CacheTypeK = "f16",
    [string]$CacheTypeV = "f16",
    [int]$Threads = 24,
    [int]$BatchSize = 2048,
    [int]$UbatchSize = 512,
    [int]$WaitSeconds = 300
)

# GLM-4.7-Flash (zai-org, 30B-A3B MoE — deepseek2 arch in llama.cpp) — the
# "agentic/coding champion" local lane on :8084. Benchmarked 2026-06-20 on the
# 4090: ~187 tok/s decode (FASTEST of our local options), clean structured
# tool-calling, 4/4 reasoning (with budget), ~20GB at 16K ctx. GLM "ARC"
# (Agentic/Reasoning/Coding) family — SWE-bench Verified 59.2, tau2-bench 79.5.
#   - MoE, ~3B active. Thinking model (<think> by default — its concise CoT is the
#     point; needs token budget to finish before answering, like Qwen reason-on).
#   - deepseek2 KV is heavier than Qwen's hybrid attention, so we run 32K ctx with
#     f16 KV here (~22GB) for a safe single-card fit; 131K native if VRAM allows.
#   - Built-in chat template (--jinja).

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

function Test-LaneCoherent {
    param([int]$LocalPort)
    # A lane can degrade at runtime into emitting a long repeated-character run
    # ('333…') for ANY prompt while /health still returns 200. Probe a tiny prompt
    # and reject that signature. GLM-4.7 is a reasoning model: a trivial answer may
    # land in reasoning_content with content empty -> treat coherent reasoning as
    # healthy. A transient error (busy/loading) is NOT incoherence -> return true.
    try {
        $body = '{"model":"glm","messages":[{"role":"user","content":"Say hello in five words."}],"max_tokens":64,"temperature":0}'
        $resp = Invoke-RestMethod -Uri "http://127.0.0.1:$LocalPort/v1/chat/completions" -Method Post -Body $body -ContentType "application/json" -TimeoutSec 20
        $msg = $resp.choices[0].message
        $text = [string]$msg.content
        if ([string]::IsNullOrWhiteSpace($text)) { $text = [string]$msg.reasoning_content }
        if ([string]::IsNullOrWhiteSpace($text)) { return $false }
        if ($text -match '(.)\1{11,}') { return $false }
        return $true
    } catch {
        return $true
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
    $proxyStdout = Join-Path $logDir "glm47-proxy-$LocalPort.log"
    $proxyStderr = Join-Path $logDir "glm47-proxy-$LocalPort.err.log"
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

# Reject adopting a degenerate-but-healthy lane (HTTP 200 + '333…' garbage): kill
# it here so the fresh-start path below reloads it.
if ((Test-WslLaneHealth -LocalPort $Port) -and (Test-LaneHealth -LocalPort $Port) -and (-not (Test-LaneCoherent -LocalPort $Port))) {
    Write-Host "Lane on $Port responds but emits degenerate output - reloading instead of adopting."
    [void](Invoke-WslBashExit -Command "pkill -9 -f llama-server")
    Start-Sleep -Seconds 3
}

if (Test-WslLaneHealth -LocalPort $Port) {
    if (Test-LaneHealth -LocalPort $Port) {
        Write-Host "GLM-4.7-Flash already healthy on port $Port"
        exit 0
    }
    Start-WindowsLaneBridge -LocalPort $Port
    Start-Sleep -Seconds 2
    if (Test-LaneHealth -LocalPort $Port) {
        Write-Host "GLM-4.7-Flash ready through Windows bridge at http://127.0.0.1:$Port/v1"
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
        Write-Host "GLM-4.7-Flash already healthy on port $Port"
        exit 0
    }
    throw "Windows port $Port is occupied but lane health is not responding. Close the stale process and retry."
}

$repoRoot = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot "..")).Path
$logDir = Join-Path $repoRoot "dist"
New-Item -ItemType Directory -Force -Path $logDir | Out-Null
$stdout = Join-Path $logDir "glm47-$Port.log"
$stderr = Join-Path $logDir "glm47-$Port.err.log"
$launchScript = Join-Path $logDir "glm47-$Port.sh"

$modelToUse = $Model
if (-not (Test-WslFile -Path $modelToUse)) {
    if ($FallbackModel -and (Test-WslFile -Path $FallbackModel)) {
        Write-Warning "ext4 model missing at $Model; falling back to $FallbackModel"
        $modelToUse = $FallbackModel
    } else {
        throw "GLM-4.7-Flash model not found in WSL at $Model or fallback $FallbackModel"
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
  --no-context-shift \
  --jinja \
  --no-mmap
"@

# WriteAllText (not Set-Content): exact LF bytes, no trailing CRLF that would put a
# CR on the final bare flag (--no-mmap) and make llama.cpp reject it.
[System.IO.File]::WriteAllText($launchScript, ($bashCommand -replace "`r`n", "`n"))
$drive = $launchScript.Substring(0, 1).ToLowerInvariant()
$rest = $launchScript.Substring(2).Replace("\", "/")
$wslLaunchScript = "/mnt/$drive$rest"

$argList = @("-d", $WslDistro, "--", "bash", $wslLaunchScript)
$process = Start-Process -FilePath "wsl.exe" -ArgumentList $argList -WindowStyle Hidden -RedirectStandardOutput $stdout -RedirectStandardError $stderr -PassThru
Write-Host "Started GLM-4.7-Flash PID $($process.Id) on port $Port (ctx $ContextTokens)"

$deadline = (Get-Date).AddSeconds($WaitSeconds)
while ((Get-Date) -lt $deadline) {
    Start-Sleep -Seconds 2
    if ($process.HasExited) {
        $tail = ""
        if (Test-Path -LiteralPath $stderr) {
            $tail = (Get-Content -LiteralPath $stderr -Tail 80) -join "`n"
        }
        throw "GLM-4.7-Flash server exited during startup. $tail"
    }
    if (Test-LaneHealth -LocalPort $Port) {
        Write-Host "GLM-4.7-Flash ready at http://127.0.0.1:$Port/v1"
        exit 0
    }
    if ((Test-WslLaneHealth -LocalPort $Port) -and -not (Test-PortOpen -LocalPort $Port)) {
        Start-WindowsLaneBridge -LocalPort $Port
        Start-Sleep -Seconds 2
        if (Test-LaneHealth -LocalPort $Port) {
            Write-Host "GLM-4.7-Flash ready through Windows bridge at http://127.0.0.1:$Port/v1"
            exit 0
        }
    }
}

throw "GLM-4.7-Flash did not become healthy within $WaitSeconds seconds. See $stderr"
