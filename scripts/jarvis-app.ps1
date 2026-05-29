# Launch the Jarvis desktop app (Studio in a native WebView2 window).
# Requires pywebview in the project venv:  uv pip install pywebview
# Assumes jarvis.bat has started the backend stack (7710 / 4000 / 8084).
$ErrorActionPreference = "Stop"
$repo = "E:\Claude\OpenJarvis"
$python = Join-Path $repo ".venv\Scripts\python.exe"
if (-not (Test-Path $python)) { $python = "python" }
& $python -m openjarvis.desktop @args
