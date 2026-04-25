@echo off
REM Runs the claude-mem worker in the background.
REM Launched at Windows login via a hidden-window VBS shim.
cd /d %USERPROFILE%
npx --yes claude-mem start
