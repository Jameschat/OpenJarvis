param(
    [string]$WslDistro = "JarvisUbuntu",
    [string]$Server = "/root/llama-cpp-turboquant/build/bin/llama-server",
    [string]$ModelRef = "majentik/Qwen3.6-35B-A3B-RotorQuant-GGUF-IQ4_XS",
    [string]$TurboQuantRepo = "/root/llama-cpp-turboquant",
    [string]$TurboQuantBranch = "feature/turboquant-kv-cache",
    [int]$Port = 8085,
    [int]$ContextTokens = 128000,
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
    Write-Host "Qwen RotorQuant server already listening on port $Port"
    exit 0
}

$repoRoot = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot "..")).Path
$logDir = Join-Path $repoRoot "dist"
New-Item -ItemType Directory -Force -Path $logDir | Out-Null
$stdout = Join-Path $logDir "qwen-rotorquant-8085.log"
$stderr = Join-Path $logDir "qwen-rotorquant-8085.err.log"

$bashCommand = @"
set -euo pipefail
if [ ! -x "$Server" ]; then
  echo "EXPERIMENTAL RotorQuant server missing: $Server" >&2
  echo "Build expected from TheTom/llama-cpp-turboquant branch $TurboQuantBranch under $TurboQuantRepo" >&2
  exit 44
fi
exec "$Server" \
  -hf "$ModelRef" \
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
  --jinja \
  --temp 0.6 \
  --top-p 0.95 \
  --top-k 20 \
  --min-p 0.0 \
  --presence-penalty 0.0 \
  --repeat-penalty 1.0
"@

$argList = @("-d", $WslDistro, "--", "bash", "-lc", $bashCommand)
$process = Start-Process -FilePath "wsl.exe" -ArgumentList $argList -WindowStyle Hidden -RedirectStandardOutput $stdout -RedirectStandardError $stderr -PassThru
Write-Host "Started EXPERIMENTAL Qwen RotorQuant PID $($process.Id) on port $Port"

$deadline = (Get-Date).AddSeconds($WaitSeconds)
while ((Get-Date) -lt $deadline) {
    Start-Sleep -Seconds 2
    if ($process.HasExited) {
        $tail = ""
        if (Test-Path -LiteralPath $stderr) {
            $tail = (Get-Content -LiteralPath $stderr -Tail 80) -join "`n"
        }
        throw "Qwen RotorQuant server exited during startup. $tail"
    }
    try {
        $health = Invoke-RestMethod -Uri "http://127.0.0.1:$Port/health" -TimeoutSec 3
        if ($health.status -eq "ok") {
            Write-Host "Qwen RotorQuant ready at http://127.0.0.1:$Port/v1"
            exit 0
        }
    } catch {
        # Still warming.
    }
}

throw "Qwen RotorQuant did not become healthy within $WaitSeconds seconds. See $stderr"
