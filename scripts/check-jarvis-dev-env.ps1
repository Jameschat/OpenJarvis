param(
  [string]$Python = "E:\Claude\OpenJarvis\.venv\Scripts\python.exe"
)

$ErrorActionPreference = "Stop"
$env:UV_CACHE_DIR = "C:\tmp\uv-cache-openjarvis"

if (!(Test-Path -LiteralPath $Python)) {
  Write-Error "Python venv not found: $Python"
}

Write-Host "Checking pip..."
& $Python -m pip --version

Write-Host "Checking package integrity..."
& $Python -m pip check

Write-Host "Checking pytest..."
& $Python -m pytest --version

Write-Host "Jarvis dev environment checks passed."
