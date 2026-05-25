# Jarvis Windows Installer

This folder builds a normal Windows installer for the operator's local Jarvis setup.

## Build

From `E:\Claude\OpenJarvis`:

```powershell
.\scripts\installer\build-installer.ps1
```

This creates:

- `dist\installer\JarvisPayload.zip`
- `dist\installer\JarvisSetup.exe` if Inno Setup 7 is installed

By default, secrets are not exported. That means `jarvis.bat` is replaced on the new PC with a placeholder that tells the operator to copy the live secret-bearing launcher manually.

To include secrets in the installer:

```powershell
.\scripts\installer\build-installer.ps1 -IncludeSecrets
```

Only use `-IncludeSecrets` for a private installer that will not be uploaded, emailed, or left on shared storage.

## New PC Requirements

Install these before running `JarvisSetup.exe`:

- Git for Windows
- Python 3.12
- uv
- Ollama
- NVIDIA driver, if this PC has an NVIDIA GPU

The installer restores:

- `E:\Claude\OpenJarvis`
- `E:\Claude\Obsidian\Claude\Brain`
- `%USERPROFILE%\.openjarvis`
- desktop and Start menu shortcuts
- Ollama models listed by the source machine, unless skipped manually in the PowerShell installer

## Run After Install

Start Jarvis from the desktop shortcut, then open:

```text
http://localhost:7710
```

Use `INSTALL-SUMMARY.txt` inside the installed `OpenJarvis` folder for final checks.
