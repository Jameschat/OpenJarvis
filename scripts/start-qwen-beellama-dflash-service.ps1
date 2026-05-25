param(
    [string]$BeeLlamaServer = "C:\tmp\beellama-v0.2.0\extract\llama-server.exe",
    [string]$Model = "E:\Claude\models\Qwen3.6-27B-Q4_K_M.gguf",
    [string]$DraftModel = "E:\Claude\models\Qwen3.6-27B-DFlash-Q4_K_M.gguf",
    [int]$Port = 8082,
    [int]$ContextTokens = 4096,
    [int]$DraftMax = 8,
    [int]$WaitSeconds = 180
)

$ErrorActionPreference = "Stop"

function Test-PortOpen {
    param([int]$LocalPort)
    return [bool](Get-NetTCPConnection -LocalPort $LocalPort -State Listen -ErrorAction SilentlyContinue)
}

if (Test-PortOpen -LocalPort $Port) {
    Write-Host "BeeLlama already listening on port $Port"
    exit 0
}

if (-not (Test-Path -LiteralPath $BeeLlamaServer)) {
    throw "BeeLlama server missing: $BeeLlamaServer"
}
if (-not (Test-Path -LiteralPath $Model)) {
    throw "Qwen target GGUF missing: $Model"
}
if (-not (Test-Path -LiteralPath $DraftModel)) {
    throw "Qwen DFlash drafter GGUF missing: $DraftModel"
}

$repoRoot = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot "..")).Path
$logDir = Join-Path $repoRoot "dist"
New-Item -ItemType Directory -Force -Path $logDir | Out-Null
$stdout = Join-Path $logDir "qwen-beellama-8082.log"
$stderr = Join-Path $logDir "qwen-beellama-8082.err.log"

$argsList = @(
    "-m", $Model,
    "--spec-draft-model", $DraftModel,
    "--spec-type", "dflash",
    "--spec-dflash-cross-ctx", "512",
    "--host", "127.0.0.1",
    "--port", "$Port",
    "-np", "1",
    "--kv-unified",
    "-ngl", "all",
    "--spec-draft-ngl", "all",
    "-b", "2048",
    "-ub", "512",
    "--ctx-size", "$ContextTokens",
    "--cache-type-k", "q4_0",
    "--cache-type-v", "q4_0",
    "--flash-attn", "on",
    "--cache-ram", "0",
    "--jinja",
    "--no-mmap",
    "--mlock",
    "--no-host",
    "--temp", "0.6",
    "--top-k", "20",
    "--top-p", "1.0",
    "--min-p", "0.0",
    "--spec-draft-n-max", "$DraftMax"
)

function ConvertTo-QuotedArgument {
    param([string]$Value)
    if ($Value -notmatch '[\s"]') {
        return $Value
    }
    return '"' + ($Value -replace '\\', '\\' -replace '"', '\"') + '"'
}

$psi = [System.Diagnostics.ProcessStartInfo]::new()
$psi.FileName = $BeeLlamaServer
$psi.Arguments = ($argsList | ForEach-Object { ConvertTo-QuotedArgument $_ }) -join " "
$psi.WorkingDirectory = Split-Path -Parent $BeeLlamaServer
$psi.UseShellExecute = $false
$psi.CreateNoWindow = $true
$psi.RedirectStandardOutput = $false
$psi.RedirectStandardError = $false
$process = [System.Diagnostics.Process]::Start($psi)

Write-Host "Started BeeLlama DFlash PID $($process.Id) on port $Port"

$deadline = (Get-Date).AddSeconds($WaitSeconds)
while ((Get-Date) -lt $deadline) {
    Start-Sleep -Seconds 2
    if ($process.HasExited) {
        $tail = ""
        if (Test-Path -LiteralPath $stderr) {
            $tail = (Get-Content -LiteralPath $stderr -Tail 40) -join "`n"
        }
        throw "BeeLlama exited during startup. $tail"
    }
    try {
        $health = Invoke-RestMethod -Uri "http://127.0.0.1:$Port/health" -TimeoutSec 3
        if ($health.status -eq "ok") {
            Write-Host "BeeLlama DFlash ready at http://127.0.0.1:$Port/v1"
            exit 0
        }
    } catch {
        # Still warming.
    }
}

throw "BeeLlama did not become healthy within $WaitSeconds seconds. See $stderr"
