param(
    [string]$BeeLlamaServer = "C:\tmp\beellama-v0.2.0\extract\llama-server.exe",
    [string]$Model = "E:\Claude\models\Qwen3.6-27B-Q5_K_S.gguf",
    [string]$DraftModel = "E:\Claude\models\Qwen3.6-27B-DFlash-Q4_K_M.gguf",
    [string]$Mmproj = "",
    [int]$Port = 8083,
    [int]$WaitSeconds = 240
)

$ErrorActionPreference = "Stop"

function Test-PortOpen {
    param([int]$LocalPort)
    return [bool](Get-NetTCPConnection -LocalPort $LocalPort -State Listen -ErrorAction SilentlyContinue)
}

if (Test-PortOpen -LocalPort $Port) {
    Write-Host "BeeLlama quality profile already listening on port $Port"
    exit 0
}

if (-not (Test-Path -LiteralPath $BeeLlamaServer)) {
    throw "BeeLlama server missing: $BeeLlamaServer"
}
if (-not (Test-Path -LiteralPath $Model)) {
    throw "Qwen Q5_K_S target GGUF missing: $Model. Download the Anbeeld Qwen3.6 27B Q5_K_S GGUF before starting the quality profile."
}
if (-not (Test-Path -LiteralPath $DraftModel)) {
    throw "Qwen DFlash drafter GGUF missing: $DraftModel"
}
if ($Mmproj -and -not (Test-Path -LiteralPath $Mmproj)) {
    throw "Qwen mmproj GGUF missing: $Mmproj"
}

$repoRoot = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot "..")).Path
$logDir = Join-Path $repoRoot "dist"
New-Item -ItemType Directory -Force -Path $logDir | Out-Null
$stderr = Join-Path $logDir "qwen-beellama-quality-8083.err.log"

$argsList = @(
    "-m", $Model,
    "--spec-draft-model", $DraftModel,
    "--spec-type", "dflash",
    "--spec-dflash-cross-ctx", "1024",
    "--host", "127.0.0.1",
    "--port", "$Port",
    "-np", "1",
    "--kv-unified",
    "-ngl", "all",
    "--spec-draft-ngl", "all",
    "-b", "2048",
    "-ub", "512",
    "--ctx-size", "102400",
    "--cache-type-k", "q5_0",
    "--cache-type-v", "q4_1",
    "--flash-attn", "on",
    "--cache-ram", "0",
    "--jinja",
    "--no-mmap",
    "--no-host",
    "--temp", "0.6",
    "--top-k", "20",
    "--top-p", "1.0",
    "--min-p", "0.0",
    "--spec-draft-n-max", "8",
    "--reasoning", "on",
    "--chat-template-kwargs", '{"preserve_thinking":true}'
)

if ($Mmproj) {
    $argsList += @("--mmproj", $Mmproj, "--no-mmproj-offload")
}

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

Write-Host "Started BeeLlama Qwen quality PID $($process.Id) on port $Port"

$deadline = (Get-Date).AddSeconds($WaitSeconds)
while ((Get-Date) -lt $deadline) {
    Start-Sleep -Seconds 2
    if ($process.HasExited) {
        $tail = ""
        if (Test-Path -LiteralPath $stderr) {
            $tail = (Get-Content -LiteralPath $stderr -Tail 40) -join "`n"
        }
        throw "BeeLlama quality profile exited during startup. $tail"
    }
    try {
        $health = Invoke-RestMethod -Uri "http://127.0.0.1:$Port/health" -TimeoutSec 3
        if ($health.status -eq "ok") {
            Write-Host "BeeLlama Qwen quality profile ready at http://127.0.0.1:$Port/v1"
            exit 0
        }
    } catch {
        # Still warming.
    }
}

throw "BeeLlama quality profile did not become healthy within $WaitSeconds seconds. See $stderr"
