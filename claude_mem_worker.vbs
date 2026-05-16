' Launches claude_mem_worker.bat with no visible console window.
' Runs on Windows login via a Startup-folder shortcut.
Set WshShell = CreateObject("WScript.Shell")
WshShell.Run chr(34) & "E:\Claude\OpenJarvis\claude_mem_worker.bat" & chr(34), 0, False
Set WshShell = Nothing
