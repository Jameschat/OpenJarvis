# Build Jarvis.exe — the pywebview desktop shell — with PyInstaller.
#
# The shell is THIN: it only needs the desktop module, pywebview, and the light
# health-probe modules (runtime_health / qwen_runtime_status). It does NOT bundle
# the backend (torch/faiss/etc.) — the backend runs as a separate process started
# by jarvis.bat (or by --start-backend, which the supervisor launches).
#
# Prereqs (one-time):  uv pip install pywebview pyinstaller
# Output:  dist/Jarvis/Jarvis.exe  (one-dir; change to --onefile for a single exe)

$ErrorActionPreference = "Stop"
$repo = "E:\Claude\OpenJarvis"
$python = Join-Path $repo ".venv\Scripts\python.exe"
if (-not (Test-Path $python)) { $python = "python" }

# Optional app icon: drop a jarvis.ico at jarvis_web\jarvis.ico to brand the exe.
$iconArgs = @()
$icon = Join-Path $repo "jarvis_web\jarvis.ico"
if (Test-Path $icon) { $iconArgs = @("--icon", $icon) }

& $python -m PyInstaller `
  --name Jarvis `
  --noconsole `
  --clean `
  --noconfirm `
  --collect-submodules webview `
  --hidden-import openjarvis.tools.runtime_health `
  --hidden-import openjarvis.tools.qwen_runtime_status `
  --paths (Join-Path $repo "src") `
  @iconArgs `
  (Join-Path $repo "src\openjarvis\desktop\__main__.py")

Write-Host ""
Write-Host "Built: $repo\dist\Jarvis\Jarvis.exe"
Write-Host "Run it with the backend already up (jarvis.bat), or it will offer to start it."
