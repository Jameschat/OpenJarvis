param(
    [switch]$Json,
    [string]$QwenStatusPath = ""
)

$ErrorActionPreference = "Stop"

$repoRoot = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot "..")).Path
$env:UV_CACHE_DIR = Join-Path $repoRoot ".uv-cache"

$argsList = @("run", "--no-sync", "python", "-m", "openjarvis.tools.runtime_health")
if ($Json) {
    $argsList += "--json"
}
if ($QwenStatusPath) {
    $argsList += @("--qwen-status-path", $QwenStatusPath)
}

Push-Location $repoRoot
try {
    & uv @argsList
    exit $LASTEXITCODE
} finally {
    Pop-Location
}
