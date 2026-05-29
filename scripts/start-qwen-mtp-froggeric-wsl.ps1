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
    [int]$WaitSeconds = 300
)

$ErrorActionPreference = "Stop"

function Test-PortOpen {
    param([int]$LocalPort)
    return [bool](Get-NetTCPConnection -LocalPort $LocalPort -State Listen -ErrorAction SilentlyContinue)
}

if (Test-PortOpen -LocalPort $Port) {
    Write-Host "Qwen MTP Froggeric server already listening on port $Port"
    exit 0
}

$repoRoot = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot "..")).Path
$logDir = Join-Path $repoRoot "dist"
New-Item -ItemType Directory -Force -Path $logDir | Out-Null
$stdout = Join-Path $logDir "qwen-mtp-froggeric-8084.log"
$stderr = Join-Path $logDir "qwen-mtp-froggeric-8084.err.log"

$bashCommand = @"
set -euo pipefail
exec $Server \
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
  --chat-template-file $ChatTemplate \
  --reasoning off \
  --no-cache-prompt \
  --cache-ram 0 \
  --no-mmap \
  --temp 0.6 \
  --top-k 20 \
  --top-p 1.0
"@

$argList = @("-d", $WslDistro, "--", "bash", "-lc", $bashCommand)
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
    try {
        $health = Invoke-RestMethod -Uri "http://127.0.0.1:$Port/health" -TimeoutSec 3
        if ($health.status -eq "ok") {
            Write-Host "Qwen MTP Froggeric ready at http://127.0.0.1:$Port/v1"
            exit 0
        }
    } catch {
        # Still warming.
    }
}

throw "Qwen MTP Froggeric did not become healthy within $WaitSeconds seconds. See $stderr"
