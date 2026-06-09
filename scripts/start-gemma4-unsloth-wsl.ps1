param(
    [string]$WslDistro = "JarvisUbuntu",
    [string]$Server = "/root/llama-cpp-turboquant/build/bin/llama-server",
    [string]$Model = "/mnt/e/Claude/models/gemma-4-26B-A4B-it-qat-UD-Q4_K_XL.gguf",
    [int]$Port = 8087,
    [int]$ContextTokens = 60000,
    [int]$Threads = 24,
    [int]$BatchSize = 4092,
    [int]$UBatchSize = 1024,
    [string]$CacheTypeK = "q8_0",
    [string]$CacheTypeV = "turbo4",
    [int]$WaitSeconds = 300
)

$ErrorActionPreference = "Stop"

function Test-PortOpen {
    param([int]$LocalPort)
    return [bool](Get-NetTCPConnection -LocalPort $LocalPort -State Listen -ErrorAction SilentlyContinue)
}

if (Test-PortOpen -LocalPort $Port) {
    Write-Host "Gemma 4 benchmark server already listening on port $Port"
    exit 0
}

$repoRoot = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot "..")).Path
$logDir = Join-Path $repoRoot "dist"
New-Item -ItemType Directory -Force -Path $logDir | Out-Null
$stdout = Join-Path $logDir "gemma4-unsloth-8087.log"
$stderr = Join-Path $logDir "gemma4-unsloth-8087.err.log"

$bashCommand = @"
set -euo pipefail
if [ ! -x "$Server" ]; then
  echo "EXPERIMENTAL Gemma 4 TurboQuant server missing: $Server" >&2
  echo "Build TheTom/llama-cpp-turboquant under /root/llama-cpp-turboquant before running this lane." >&2
  exit 44
fi
if [ ! -f "$Model" ]; then
  echo "Gemma 4 GGUF missing: $Model" >&2
  echo "Download unsloth/gemma-4-26B-A4B-it-qat-GGUF gemma-4-26B-A4B-it-qat-UD-Q4_K_XL.gguf into E:\\Claude\\models first." >&2
  exit 45
fi
exec "$Server" \
  -m "$Model" \
  --host 0.0.0.0 \
  --port $Port \
  --ctx-size $ContextTokens \
  -ngl 99 \
  --flash-attn on \
  --threads $Threads \
  --batch-size $BatchSize \
  --ubatch-size $UBatchSize \
  --cache-type-k $CacheTypeK \
  --cache-type-v $CacheTypeV \
  --parallel 1 \
  --no-context-shift \
  --ctx-checkpoints 0 \
  --checkpoint-every-n-tokens -1 \
  --cache-ram 0 \
  --no-cache-prompt \
  --jinja \
  --reasoning off \
  --reasoning-budget 0 \
  --temp 0.6 \
  --top-p 0.95 \
  --top-k 20 \
  --min-p 0.0 \
  --presence-penalty 0.0 \
  --repeat-penalty 1.0
"@

$argList = @("-d", $WslDistro, "--", "bash", "-lc", $bashCommand)
$process = Start-Process -FilePath "wsl.exe" -ArgumentList $argList -WindowStyle Hidden -RedirectStandardOutput $stdout -RedirectStandardError $stderr -PassThru
Write-Host "Started EXPERIMENTAL Gemma 4 benchmark PID $($process.Id) on port $Port"

$deadline = (Get-Date).AddSeconds($WaitSeconds)
while ((Get-Date) -lt $deadline) {
    Start-Sleep -Seconds 2
    if ($process.HasExited) {
        $tail = ""
        if (Test-Path -LiteralPath $stderr) {
            $tail = (Get-Content -LiteralPath $stderr -Tail 80) -join "`n"
        }
        throw "Gemma 4 benchmark server exited during startup. $tail"
    }
    try {
        $health = Invoke-RestMethod -Uri "http://127.0.0.1:$Port/health" -TimeoutSec 3
        if ($health.status -eq "ok") {
            Write-Host "Gemma 4 benchmark ready at http://127.0.0.1:$Port/v1"
            exit 0
        }
    } catch {
        # Still warming.
    }
}

throw "Gemma 4 benchmark did not become healthy within $WaitSeconds seconds. See $stderr"
