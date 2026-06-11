param(
    [switch]$Json,
    [switch]$DryRun,
    [string]$ReportPath = ""
)

$ErrorActionPreference = "Stop"

$repoRoot = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot "..")).Path
$python = Join-Path $repoRoot ".venv\Scripts\python.exe"
if (-not (Test-Path -LiteralPath $python)) {
    throw "OpenJarvis venv python not found: $python"
}

$env:PYTHONPATH = Join-Path $repoRoot "src"
$env:UV_CACHE_DIR = "C:\tmp\uv-cache-openjarvis"

$argsList = @("-m", "openjarvis.tools.runtime_watchdog")
if ($Json) {
    $argsList += "--json"
}
if ($DryRun) {
    $argsList += "--dry-run"
}
if ($ReportPath) {
    $argsList += @("--report-path", $ReportPath)
}

Push-Location $repoRoot
try {
    & $python @argsList
    exit $LASTEXITCODE
} finally {
    Pop-Location
}
