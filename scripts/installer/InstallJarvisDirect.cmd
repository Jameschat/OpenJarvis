@echo off
setlocal
set "SCRIPT_DIR=%~dp0"
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%SCRIPT_DIR%install-jarvis.ps1" -PayloadZip "%SCRIPT_DIR%JarvisPayload.zip" -InstallRoot "E:\Claude"
if errorlevel 1 (
  echo.
  echo Jarvis install failed. Leave this window open and send Codex the error above.
  pause
  exit /b 1
)
echo.
echo Jarvis install finished. You can start Jarvis from the desktop shortcut.
pause
