# Migrate secret env vars out of jarvis.bat into a git-ignored, user-only env file.
#
# - Secrets move to %USERPROFILE%\.openjarvis\jarvis.env (KEY=VALUE lines, ASCII, no BOM)
# - jarvis.bat gets a loader block in place of the removed `set` lines
# - A timestamped backup of jarvis.bat goes to %USERPROFILE%\.openjarvis\backups\
# - Verification compares value LENGTHS via a fresh cmd.exe loader pass; values are
#   never printed, logged, or echoed by this script.
#
# Usage: powershell -ExecutionPolicy Bypass -File scripts\migrate-jarvis-secrets.ps1 [-WhatIfOnly]

param(
    [switch]$WhatIfOnly
)

$ErrorActionPreference = 'Stop'

$RepoRoot = Split-Path -Parent $PSScriptRoot
$Launcher = Join-Path $RepoRoot 'jarvis.bat'
$EnvDir = Join-Path $env:USERPROFILE '.openjarvis'
$EnvFile = Join-Path $EnvDir 'jarvis.env'
$BackupDir = Join-Path $EnvDir 'backups'

# Keys treated as secrets. Everything else stays in jarvis.bat as plain config.
$SecretKeys = @(
    'OPENJARVIS_TUNNEL_TOKEN',
    'OPENJARVIS_UNIFI_KEY',
    'OPENJARVIS_VAULT_TOKEN',
    'OPENJARVIS_PUBLIC_PIN'
)

if (-not (Test-Path $Launcher)) { throw "jarvis.bat not found at $Launcher" }

$lines = [System.IO.File]::ReadAllLines($Launcher)

# Collect secret set-lines. Abort on cmd-special characters whose semantics would
# change between a literal `set` line and a for /f load (we'd rather not migrate
# than silently mangle a token).
$riskyPattern = '[%!^&<>|]'
$found = @{}
$firstSecretIndex = -1
for ($i = 0; $i -lt $lines.Count; $i++) {
    if ($lines[$i] -match '^\s*set\s+"?([A-Za-z_][A-Za-z0-9_]*)=(.*?)"?\s*$') {
        $key = $Matches[1]; $value = $Matches[2]
        if ($SecretKeys -contains $key) {
            if ($value -match $riskyPattern) {
                throw "Value of $key contains cmd-special characters; migrate that key by hand."
            }
            if (-not $found.ContainsKey($key)) {
                $found[$key] = $value
                if ($firstSecretIndex -lt 0) { $firstSecretIndex = $i }
            }
        }
    }
}

if ($found.Count -eq 0) {
    Write-Host 'No secret set-lines found in jarvis.bat - nothing to migrate.'
    exit 0
}

Write-Host ("Found {0} secret key(s) to migrate: {1}" -f $found.Count, ($found.Keys -join ', '))
if ($WhatIfOnly) { Write-Host 'WhatIfOnly: stopping before any changes.'; exit 0 }

# --- Backup launcher (backup also contains secrets -> same protected dir) ---
New-Item -ItemType Directory -Force $BackupDir | Out-Null
$stamp = Get-Date -Format 'yyyyMMdd-HHmmss'
$backupPath = Join-Path $BackupDir "jarvis.bat.$stamp"
Copy-Item $Launcher $backupPath

# --- Write env file (ASCII, no BOM: cmd's for /f mangles a BOM'd first line) ---
$envLines = @('# Jarvis secrets - loaded by jarvis.bat and start-studio-stack.ps1. Never commit.')
foreach ($key in $SecretKeys) {
    if ($found.ContainsKey($key)) { $envLines += "$key=$($found[$key])" }
}
[System.IO.File]::WriteAllLines($EnvFile, $envLines, [System.Text.Encoding]::ASCII)

# Restrict ACL to the current user (+ SYSTEM/admins inherit removal).
icacls $EnvFile /inheritance:r /grant:r "$($env:USERNAME):(R,W)" | Out-Null

# --- Rewrite jarvis.bat: loader block replaces first secret line, others removed ---
$loader = @(
    'REM --- Secrets live in %USERPROFILE%\.openjarvis\jarvis.env (git-ignored, user-only ACL) ---',
    'REM --- Migrated by scripts\migrate-jarvis-secrets.ps1; backup in %USERPROFILE%\.openjarvis\backups ---',
    'if exist "%USERPROFILE%\.openjarvis\jarvis.env" (',
    '  for /f "usebackq eol=# tokens=1,* delims==" %%a in ("%USERPROFILE%\.openjarvis\jarvis.env") do set "%%a=%%b"',
    ')'
)
$out = New-Object System.Collections.Generic.List[string]
for ($i = 0; $i -lt $lines.Count; $i++) {
    $isSecretLine = $false
    if ($lines[$i] -match '^\s*set\s+"?([A-Za-z_][A-Za-z0-9_]*)=') {
        if ($found.ContainsKey($Matches[1])) { $isSecretLine = $true }
    }
    if ($i -eq $firstSecretIndex) {
        $loader | ForEach-Object { $out.Add($_) }
    } elseif (-not $isSecretLine) {
        $out.Add($lines[$i])
    }
}
[System.IO.File]::WriteAllLines($Launcher, $out, [System.Text.Encoding]::ASCII)

# --- Verify: fresh cmd loads only the env file; compare value lengths ---
# (temp batch file so the echoes run AFTER the for-loop completes, not inside it)
$probeFile = Join-Path $EnvDir "verify-jarvis-env.$stamp.cmd"
$probeLines = @(
    '@echo off',
    ('for /f "usebackq eol=# tokens=1,* delims==" %%a in ("' + $EnvFile + '") do set "%%a=%%b"')
)
foreach ($key in $found.Keys) { $probeLines += "echo $key=%$key%" }
[System.IO.File]::WriteAllLines($probeFile, $probeLines, [System.Text.Encoding]::ASCII)
$result = cmd /c "`"$probeFile`"" 2>&1
Remove-Item $probeFile -Force
$ok = $true
foreach ($key in $found.Keys) {
    $line = $result | Where-Object { $_ -like "$key=*" } | Select-Object -First 1
    $loadedLen = if ($line) { $line.Length - $key.Length - 1 } else { -1 }
    $wantLen = $found[$key].Length
    if ($loadedLen -ne $wantLen) {
        Write-Warning "$key length mismatch (loaded $loadedLen vs expected $wantLen)"
        $ok = $false
    } else {
        Write-Host "$key OK (length $wantLen)"
    }
}
# Scrub captured values from this session.
$result = $null; $found = $null; $checks = $null

if (-not $ok) {
    Write-Warning "Verification FAILED - restoring jarvis.bat from $backupPath"
    Copy-Item $backupPath $Launcher -Force
    exit 1
}

Write-Host ''
Write-Host "Done. Secrets now in $EnvFile (user-only ACL)."
Write-Host "jarvis.bat backup: $backupPath"
Write-Host 'Next stack restart picks the loader up automatically.'
