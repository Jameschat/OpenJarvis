param(
    [Parameter(Mandatory=$true)]
    [string]$LlamaServer,

    [Parameter(Mandatory=$true)]
    [string]$Model,

    [string]$DraftModel = "",
    [string]$SpeculativeMode = "none",
    [int]$Port = 8081,
    [int]$ContextTokens = 8192,
    [int]$GpuLayers = 99,
    [int]$DraftMax = 16
)

$ErrorActionPreference = "Stop"

if (-not (Test-Path -LiteralPath $LlamaServer)) {
    throw "llama-server not found: $LlamaServer"
}
if (-not (Test-Path -LiteralPath $Model)) {
    throw "Qwen model GGUF not found: $Model"
}
if ($DraftModel -and -not (Test-Path -LiteralPath $DraftModel)) {
    throw "Draft model GGUF not found: $DraftModel"
}

$argsList = @(
    "--model", $Model,
    "--host", "127.0.0.1",
    "--port", "$Port",
    "-c", "$ContextTokens",
    "-ngl", "$GpuLayers",
    "-fa", "on",
    "--cache-prompt",
    "--no-mmproj"
)

if ($DraftModel) {
    $argsList += @("--model-draft", $DraftModel, "-ngld", "$GpuLayers", "--spec-draft-n-max", "$DraftMax")
} elseif ($SpeculativeMode -ne "none") {
    $argsList += @("--spec-type", $SpeculativeMode, "--spec-draft-n-max", "$([Math]::Max($DraftMax, 16))")
}

Write-Host "Starting Qwen llama.cpp fast lane on http://127.0.0.1:$Port/v1"
Write-Host "$LlamaServer $($argsList -join ' ')"
& $LlamaServer @argsList
