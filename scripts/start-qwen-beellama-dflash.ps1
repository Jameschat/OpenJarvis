param(
    [Parameter(Mandatory=$true)]
    [string]$BeeLlamaServer,

    [Parameter(Mandatory=$true)]
    [string]$Model,

    [Parameter(Mandatory=$true)]
    [string]$DraftModel,

    [string]$Mmproj = "",
    [int]$Port = 8082,
    [int]$ContextTokens = 32768,
    [int]$CrossContextTokens = 512,
    [int]$DraftMax = 8,
    [string]$CacheTypeK = "q4_0",
    [string]$CacheTypeV = "q4_0"
)

$ErrorActionPreference = "Stop"

if (-not (Test-Path -LiteralPath $BeeLlamaServer)) {
    throw "beellama-server not found: $BeeLlamaServer"
}
if (-not (Test-Path -LiteralPath $Model)) {
    throw "Qwen target GGUF not found: $Model"
}
if (-not (Test-Path -LiteralPath $DraftModel)) {
    throw "Qwen DFlash drafter GGUF not found: $DraftModel"
}
if ($Mmproj -and -not (Test-Path -LiteralPath $Mmproj)) {
    throw "Qwen mmproj GGUF not found: $Mmproj"
}

$argsList = @(
    "-m", $Model,
    "--spec-draft-model", $DraftModel,
    "--spec-type", "dflash",
    "--spec-dflash-cross-ctx", "$CrossContextTokens",
    "--host", "127.0.0.1",
    "--port", "$Port",
    "-np", "1",
    "--kv-unified",
    "-ngl", "all",
    "--spec-draft-ngl", "all",
    "-b", "2048",
    "-ub", "512",
    "--ctx-size", "$ContextTokens",
    "--cache-type-k", $CacheTypeK,
    "--cache-type-v", $CacheTypeV,
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

if ($Mmproj) {
    $argsList += @("--mmproj", $Mmproj, "--no-mmproj-offload")
}
Write-Host "Starting Qwen BeeLlama DFlash fast lane on http://127.0.0.1:$Port/v1"
Write-Host "$BeeLlamaServer $($argsList -join ' ')"
& $BeeLlamaServer @argsList
