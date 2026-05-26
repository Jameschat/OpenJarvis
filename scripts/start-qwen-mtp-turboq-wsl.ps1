param(
    [string]$WslDistro = "JarvisUbuntu",
    [string]$TurboQServer = "~/llama.cpp-turboq-mtp/build/bin/llama-server",
    [string]$Model = "/mnt/e/Claude/models/Qwen3.6-27B-MTP-TBQ4.gguf",
    [int]$Port = 8084,
    [int]$ContextTokens = 65536,
    [int]$DraftMax = 3,
    [string]$CacheTypeK = "tbq4_0",
    [string]$CacheTypeV = "tbq4_0",
    [int]$WaitSeconds = 180
)

$ErrorActionPreference = "Stop"

Write-Host "EXPERIMENTAL: Qwen MTP/TurboQuant WSL lane. This does not replace BeeLlama."

function Test-PortOpen {
    param([int]$LocalPort)
    return [bool](Get-NetTCPConnection -LocalPort $LocalPort -State Listen -ErrorAction SilentlyContinue)
}

if (Test-PortOpen -LocalPort $Port) {
    Write-Host "TurboQuant/MTP server already listening on port $Port"
    exit 0
}

if (-not (Get-Command wsl.exe -ErrorAction SilentlyContinue)) {
    throw "wsl.exe not found. Install/configure WSL before using the experimental TurboQuant/MTP lane."
}

$distros = (& wsl.exe -l -q) | ForEach-Object { ($_ -replace "`0", "").Trim() } | Where-Object { $_ }
if ($distros -notcontains $WslDistro) {
    throw "WSL distro '$WslDistro' was not found. Available: $($distros -join ', ')"
}

$repoRoot = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot "..")).Path
$logDir = Join-Path $repoRoot "dist"
New-Item -ItemType Directory -Force -Path $logDir | Out-Null
$stdout = Join-Path $logDir "qwen-mtp-turboq-wsl-8084.log"
$stderr = Join-Path $logDir "qwen-mtp-turboq-wsl-8084.err.log"

$bashCommand = @"
set -euo pipefail
if [ ! -x $TurboQServer ]; then
  echo "TurboQuant llama-server missing or not executable: $TurboQServer" >&2
  exit 1
fi
if [ ! -f $Model ]; then
  echo "Qwen MTP/TBQ model missing: $Model" >&2
  exit 1
fi
exec $TurboQServer \
  -m $Model \
  --host 0.0.0.0 \
  --port $Port \
  -np 1 \
  --ctx-size $ContextTokens \
  -ngl 99 \
  --flash-attn on \
  --cache-type-k $CacheTypeK \
  --cache-type-v $CacheTypeV \
  --spec-type mtp \
  --spec-draft-n-max $DraftMax \
  --jinja \
  --no-mmap \
  --temp 0.6 \
  --top-k 20 \
  --top-p 1.0
"@

$argList = @("-d", $WslDistro, "--", "bash", "-lc", $bashCommand)
$process = Start-Process -FilePath "wsl.exe" -ArgumentList $argList -WindowStyle Hidden -RedirectStandardOutput $stdout -RedirectStandardError $stderr -PassThru
Write-Host "Started WSL TurboQuant/MTP PID $($process.Id) on port $Port"

$deadline = (Get-Date).AddSeconds($WaitSeconds)
while ((Get-Date) -lt $deadline) {
    Start-Sleep -Seconds 2
    if ($process.HasExited) {
        $tail = ""
        if (Test-Path -LiteralPath $stderr) {
            $tail = (Get-Content -LiteralPath $stderr -Tail 40) -join "`n"
        }
        throw "TurboQuant/MTP server exited during startup. $tail"
    }
    try {
        $health = Invoke-RestMethod -Uri "http://127.0.0.1:$Port/health" -TimeoutSec 3
        if ($health.status -eq "ok") {
            Write-Host "TurboQuant/MTP ready at http://127.0.0.1:$Port/v1"
            exit 0
        }
    } catch {
        # Still warming.
    }
}

throw "TurboQuant/MTP did not become healthy within $WaitSeconds seconds. See $stderr"
