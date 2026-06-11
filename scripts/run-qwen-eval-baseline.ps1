param(
  [string]$Python = "E:\Claude\OpenJarvis\.venv\Scripts\python.exe",
  [string]$Model = "qwen3.6-27b-local",
  [string]$Cases = "E:\Claude\OpenJarvis\evals\qwen\cases.json",
  [string]$OutDir = "E:\Claude\OpenJarvis\dist",
  [double]$MinPassRate = 0.0
)

$ErrorActionPreference = "Stop"
$env:PYTHONPATH = "E:\Claude\OpenJarvis\src"
$env:UV_CACHE_DIR = "C:\tmp\uv-cache-openjarvis"

if (!(Test-Path -LiteralPath $Python)) {
  Write-Error "Python venv not found: $Python"
}
if (!(Test-Path -LiteralPath $Cases)) {
  Write-Error "Qwen eval cases not found: $Cases"
}
New-Item -ItemType Directory -Force -Path $OutDir | Out-Null

$stamp = Get-Date -Format "yyyyMMdd-HHmmss"
$out = Join-Path $OutDir "qwen-eval-baseline-$stamp.md"
$latest = Join-Path $OutDir "qwen-eval-baseline-latest.md"

& $Python -m openjarvis.tools.qwen_eval `
  --cases $Cases `
  --model $Model `
  --label "qwen-baseline-$Model" `
  --min-pass-rate $MinPassRate `
  --out $out

Copy-Item -LiteralPath $out -Destination $latest -Force
Write-Host "Qwen eval baseline written: $out"
Write-Host "Latest baseline updated: $latest"
