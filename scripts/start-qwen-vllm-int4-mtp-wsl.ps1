param(
    [string]$WslDistro = "JarvisUbuntu",
    [string]$ModelRef = "",
    [int]$Port = 8086,
    [int]$ContextTokens = 65536,
    [int]$MaxNumSeqs = 1,
    [int]$MaxNumBatchedTokens = 4128,
    [int]$SpeculativeTokens = 3,
    [int]$WaitSeconds = 180
)

$ErrorActionPreference = "Stop"

Write-Host "EXPERIMENTAL: Qwen vLLM INT4/MTP WSL jump lane. This does not replace the live 8084 lane."

function Test-PortOpen {
    param([int]$LocalPort)
    return [bool](Get-NetTCPConnection -LocalPort $LocalPort -State Listen -ErrorAction SilentlyContinue)
}

if ($Port -eq 8084) {
    throw "Refusing to start vLLM on 8084. 8084 is reserved for the stable WSL/MTP Froggeric lane."
}

if (Test-PortOpen -LocalPort $Port) {
    Write-Host "vLLM jump lane already listening on port $Port"
    exit 0
}

if (-not (Get-Command wsl.exe -ErrorAction SilentlyContinue)) {
    throw "wsl.exe not found. Install/configure WSL before using the experimental vLLM jump lane."
}

$distros = (& wsl.exe -l -q) | ForEach-Object { ($_ -replace "`0", "").Trim() } | Where-Object { $_ }
if ($distros -notcontains $WslDistro) {
    throw "WSL distro '$WslDistro' was not found. Available: $($distros -join ', ')"
}

if (-not $ModelRef) {
    $ModelRef = [string]$env:JARVIS_VLLM_QWEN27_MODEL
}
if (-not $ModelRef) {
    throw "Set -ModelRef or JARVIS_VLLM_QWEN27_MODEL to a Qwen3.6 27B INT4 model before launching vLLM."
}

$null = & wsl.exe -d $WslDistro -- python3 -c "import importlib.util; raise SystemExit(0 if importlib.util.find_spec('vllm') else 1)"
if ($LASTEXITCODE -ne 0) {
    throw "vLLM is not installed in WSL distro '$WslDistro'. Install the vLLM stack before launching this jump lane."
}

$repoRoot = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot "..")).Path
$logDir = Join-Path $repoRoot "dist"
New-Item -ItemType Directory -Force -Path $logDir | Out-Null
$stdout = Join-Path $logDir "qwen-vllm-int4-mtp-wsl-$Port.log"
$stderr = Join-Path $logDir "qwen-vllm-int4-mtp-wsl-$Port.err.log"
$launcher = Join-Path $logDir "qwen-vllm-int4-mtp-wsl-$Port.sh"
$speculativeConfig = "{`"method`":`"mtp`",`"num_speculative_tokens`":$SpeculativeTokens}"

$bashCommand = @"
set -euo pipefail
exec python3 -m vllm.entrypoints.openai.api_server \
  --host 0.0.0.0 \
  --port $Port \
  --model "$ModelRef" \
  --served-model-name qwen3.6-27b-vllm \
  --max-model-len $ContextTokens \
  --max-num-seqs $MaxNumSeqs \
  --max-num-batched-tokens $MaxNumBatchedTokens \
  --enable-prefix-caching \
  --enable-chunked-prefill \
  --reasoning-parser qwen3 \
  --tool-call-parser qwen3_coder \
  --speculative-config '$speculativeConfig'
"@

Set-Content -LiteralPath $launcher -Value $bashCommand -Encoding UTF8

$process = Start-Process -FilePath "wsl.exe" -ArgumentList @("-d", $WslDistro, "--", "bash", $launcher) -WindowStyle Hidden -RedirectStandardOutput $stdout -RedirectStandardError $stderr -PassThru
Write-Host "Started WSL vLLM INT4/MTP PID $($process.Id) on port $Port"

$deadline = (Get-Date).AddSeconds($WaitSeconds)
while ((Get-Date) -lt $deadline) {
    Start-Sleep -Seconds 2
    if ($process.HasExited) {
        $tail = ""
        if (Test-Path -LiteralPath $stderr) {
            $tail = (Get-Content -LiteralPath $stderr -Tail 60) -join "`n"
        }
        throw "vLLM INT4/MTP server exited during startup. $tail"
    }
    try {
        $models = Invoke-RestMethod -Uri "http://127.0.0.1:$Port/v1/models" -Headers @{ Authorization = "Bearer sk-noop" } -TimeoutSec 3
        if ($models.data) {
            Write-Host "vLLM INT4/MTP ready at http://127.0.0.1:$Port/v1"
            exit 0
        }
    } catch {
        # Still warming.
    }
}

throw "vLLM INT4/MTP did not become healthy within $WaitSeconds seconds. See $stderr"
