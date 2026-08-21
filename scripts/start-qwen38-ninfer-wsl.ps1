param(
    [string]$WslDistro = "JarvisUbuntu",
    [string]$Server = "/root/ninfer-4090/build/apps/ninfer-serve",
    [string]$Model = "/root/models/qwen3_8_27b.ninfer",
    [int]$Port = 8084,
    [int]$ContextTokens = 131072,
    [string]$KvDtype = "rk4v4",
    [int]$DraftTokens = 3,
    [int]$WaitSeconds = 300
)

# Qwen3.8-27B on the NInfer engine (github.com/sergiuszm/ninfer-4090, rtx4090-port),
# built natively in WSL at /root/ninfer-4090 - NOT llama.cpp and NOT Docker.
# Benchmarked 2026-08-21 on the 4090:
#   - 131K ctx + rk4v4 (Hadamard 4-bit KV): ~90 tok/s decode, MTP3. VERIFIED with a
#     110,892-token prefill + exact needle retrieval at 78% depth (104s), 2.38 GiB slack.
#   - CAVEAT on the llama.cpp comparison: our 45.7 tok/s llama.cpp figure was
#     measured WITHOUT speculation. llama.cpp supports --spec-type draft-mtp, which
#     upstream reports at ~118 tok/s shallow, and a 3090 study measures 63.7 tok/s
#     sustained. A fair llama.cpp config is likely COMPARABLE to this lane, not 3x
#     slower - do not cite the 45.7 number as an engine win.
#   - The real llama.cpp defect measured here: with the stock chat template a
#     40-tool payload produces a 16384-token runaway and ZERO tool calls, whereas
#     ninfer returns a clean tool call in ~120-190 tokens. Community configs pass a
#     CUSTOM --chat-template-file, which may fix that; untested here.
#   - Context/KV tradeoff measured: rk4v4-e8 holds full speed to 96K then collapses
#     (114K -> 44 tok/s). rk2v4-e8 (2-bit keys) sustains 196K @ 112 tok/s (verified
#     138K-token prefill + exact needle retrieval). 262K loads but CRASHES on a deep
#     prefill (only 1.16 GiB slack) - do not use.
#   - --no-thinking is required: the weights think by default and the agent loop
#     reads `content`, not reasoning traces.
#   - --model-id qwen3.6-27b-local keeps the existing LiteLLM alias working, so this
#     lane is a drop-in on port 8084 like every llama.cpp lane.
#
# WSL DRIVER GOTCHA: this distro carries a stale libnvidia-ptxjitcompiler
# (580.173.02) in /lib/x86_64-linux-gnu that shadows the one matching the live WSL
# driver (610.88). The mismatched pair segfaults inside __cuda_CallJitEntryPoint on
# the first cudaGetDeviceCount(). LD_LIBRARY_PATH below pins the matched driver dir.

$ErrorActionPreference = "Stop"

function Test-LaneHealth {
    param([int]$LocalPort)
    try {
        $r = Invoke-RestMethod -Uri "http://127.0.0.1:$LocalPort/v1/models" -TimeoutSec 3
        return ($null -ne $r)
    } catch { return $false }
}

function Invoke-WslBashExit {
    param([string]$Command)
    & wsl.exe -d $WslDistro -- bash -lc $Command | Out-Null
    return $LASTEXITCODE
}

function Test-WslLaneHealth {
    param([int]$LocalPort)
    return ((Invoke-WslBashExit -Command "curl -fsS --max-time 3 http://127.0.0.1:$LocalPort/v1/models >/dev/null") -eq 0)
}

if (Test-LaneHealth -LocalPort $Port) {
    Write-Host "Qwen3.8/NInfer already healthy on port $Port"
    exit 0
}

$repoRoot = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot "..")).Path
$logDir = Join-Path $repoRoot "dist"
New-Item -ItemType Directory -Force -Path $logDir | Out-Null
$stdout = Join-Path $logDir "qwen38-ninfer-$Port.log"
$stderr = Join-Path $logDir "qwen38-ninfer-$Port.err.log"
$launchScript = Join-Path $logDir "qwen38-ninfer-$Port.sh"

$bashCommand = @"
set -euo pipefail
export LD_LIBRARY_PATH=/usr/lib/wsl/drivers/nv_dispi.inf_amd64_0373d825005116d0:/usr/lib/wsl/lib:`${LD_LIBRARY_PATH:-}
exec $Server $Model \
  --host 0.0.0.0 \
  --port $Port \
  --model-id qwen3.6-27b-local \
  --max-context $ContextTokens \
  --kv-capacity $ContextTokens \
  --max-concurrency 1 \
  --max-pending-requests 16 \
  --pending-timeout-ms 600000 \
  --prefill-chunk 1024 \
  --kv-dtype $KvDtype \
  --spec mtp \
  --draft-tokens $DraftTokens \
  --lm-head-draft \
  --default-max-tokens 16384 \
  --no-thinking
"@

# WriteAllText (not Set-Content): exact LF bytes, no trailing CR on the last flag.
[System.IO.File]::WriteAllText($launchScript, ($bashCommand -replace "`r`n", "`n"))
$drive = $launchScript.Substring(0, 1).ToLowerInvariant()
$rest = $launchScript.Substring(2).Replace("\", "/")
$wslLaunchScript = "/mnt/$drive$rest"

$argList = @("-d", $WslDistro, "--", "bash", $wslLaunchScript)
$process = Start-Process -FilePath "wsl.exe" -ArgumentList $argList -WindowStyle Hidden -RedirectStandardOutput $stdout -RedirectStandardError $stderr -PassThru
Write-Host "Started Qwen3.8/NInfer PID $($process.Id) on port $Port (ctx $ContextTokens, kv $KvDtype)"

$deadline = (Get-Date).AddSeconds($WaitSeconds)
while ((Get-Date) -lt $deadline) {
    Start-Sleep -Seconds 3
    if ($process.HasExited) {
        $tail = ""
        if (Test-Path -LiteralPath $stderr) { $tail = (Get-Content -LiteralPath $stderr -Tail 25) -join "`n" }
        throw "NInfer server exited during startup. $tail"
    }
    if (Test-LaneHealth -LocalPort $Port) {
        Write-Host "Qwen3.8/NInfer ready at http://127.0.0.1:$Port/v1"
        exit 0
    }
}

throw "Qwen3.8/NInfer did not become healthy within $WaitSeconds seconds. See $stderr"
