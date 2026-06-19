param(
    # MTP 35B-A3B lane (Tier A worker upgrade). Adds self-speculative MTP decoding
    # to the 35B-A3B MoE — the worker currently runs RotorQuant IQ4_XS WITHOUT MTP
    # (~176 t/s @ 128K); MTP should push decode materially higher at the same
    # context, staying on the proven llama.cpp/WSL stack (no vLLM).
    #
    # PREREQUISITE on the target box: an MTP-capable llama.cpp build. The main PC
    # has /root/llama.cpp-turboq-mtp (supports --spec-type mtp + qwen35moe). The
    # worker's /root/llama-cpp-turboquant is a DIFFERENT (KV-branch) build that may
    # lack MTP — point -Server at an MTP build, or build turboq-mtp there first.
    [string]$WslDistro = "JarvisUbuntu",
    [string]$Server = "/root/llama.cpp-turboq-mtp/build/bin/llama-server",
    # APEX-MTP I-Compact: ~17.3GB, MTP draft head bundled, matches the current
    # RotorQuant footprint and leaves room for 128K KV. Use I-Quality (23.5GB) only
    # if the GPU has the headroom; I-Mini (14.3GB) for tighter cards.
    [string]$ModelRepo = "mudler/Qwen3.6-35B-A3B-APEX-MTP-GGUF",
    [string]$ModelFile = "Qwen3.6-35B-A3B-APEX-MTP-I-Compact.gguf",
    [int]$Port = 8085,
    [int]$ContextTokens = 128000,
    [int]$DraftMax = 3,
    [int]$Threads = 24,
    [int]$BatchSize = 4092,
    [int]$UbatchSize = 1024,
    # q4 KV keeps 128K affordable. Raise to q8_0 if VRAM allows (better quality).
    [string]$CacheTypeK = "q4_0",
    [string]$CacheTypeV = "q4_0",
    [int]$WaitSeconds = 600
)

$ErrorActionPreference = "Stop"

function Test-PortOpen { param([int]$P) return [bool](Get-NetTCPConnection -LocalPort $P -State Listen -ErrorAction SilentlyContinue) }

if (Test-PortOpen -LocalPort $Port) {
    Write-Host "A server is already listening on port $Port — stop it first to swap to the MTP lane."
    exit 0
}

$repoRoot = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot "..")).Path
$logDir = Join-Path $repoRoot "dist"
New-Item -ItemType Directory -Force -Path $logDir | Out-Null
$stdout = Join-Path $logDir "qwen-35b-a3b-mtp-$Port.log"
$stderr = Join-Path $logDir "qwen-35b-a3b-mtp-$Port.err.log"

# NOTE (hard-won on the main PC, 2026-06-19):
#  * --jinja (model's built-in template): a wrong chat template gives 0% MTP draft
#    acceptance + no stop token (runs to max_tokens). Built-in template fixes both.
#  * --ctx-checkpoints 0 / --checkpoint-every-n-tokens -1 / --no-context-shift:
#    the MTP speculative CUDA kernel hits "illegal memory access" (hard crash) when
#    a context checkpoint is created at long context (~8K+). Disable checkpoints.
$bashCommand = @"
set -euo pipefail
if [ ! -x "$Server" ]; then
  echo "MTP-capable llama-server missing: $Server" >&2
  echo "Build llama.cpp-turboq-mtp (supports --spec-type mtp + qwen35moe) on this box first." >&2
  exit 44
fi
exec "$Server" \
  -hf "$ModelRepo" \
  --hf-file "$ModelFile" \
  --host 0.0.0.0 \
  --port $Port \
  --ctx-size $ContextTokens \
  -ngl 99 \
  --flash-attn on \
  --threads $Threads \
  --threads-batch $Threads \
  --batch-size $BatchSize \
  --ubatch-size $UbatchSize \
  --cache-type-k $CacheTypeK \
  --cache-type-v $CacheTypeV \
  --spec-type mtp \
  --spec-draft-n-max $DraftMax \
  --ctx-checkpoints 0 \
  --checkpoint-every-n-tokens -1 \
  --no-context-shift \
  --parallel 1 \
  --jinja \
  --reasoning off \
  --no-mmap \
  --temp 0.6 --top-k 20 --top-p 0.95 --min-p 0.0 --presence-penalty 0.0 --repeat-penalty 1.0
"@

$argList = @("-d", $WslDistro, "--", "bash", "-lc", $bashCommand)
$process = Start-Process -FilePath "wsl.exe" -ArgumentList $argList -WindowStyle Hidden -RedirectStandardOutput $stdout -RedirectStandardError $stderr -PassThru
Write-Host "Started Qwen3.6-35B-A3B MTP lane PID $($process.Id) on port $Port (ctx $ContextTokens, $ModelFile)"

$deadline = (Get-Date).AddSeconds($WaitSeconds)
while ((Get-Date) -lt $deadline) {
    Start-Sleep -Seconds 3
    if ($process.HasExited) {
        $tail = ""
        if (Test-Path -LiteralPath $stderr) { $tail = (Get-Content -LiteralPath $stderr -Tail 80) -join "`n" }
        throw "MTP lane exited during startup. $tail"
    }
    try {
        $h = Invoke-RestMethod -Uri "http://127.0.0.1:$Port/health" -TimeoutSec 3
        if ($h.status -eq "ok") { Write-Host "Qwen3.6-35B-A3B MTP lane ready at http://127.0.0.1:$Port/v1"; exit 0 }
    } catch { }
}
throw "MTP lane did not become healthy within $WaitSeconds seconds. See $stderr"
