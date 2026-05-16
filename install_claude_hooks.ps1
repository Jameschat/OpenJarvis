# Install / remove Jarvis mission-control hooks in Claude Code's settings.
#
# Usage:
#   powershell -NoProfile -ExecutionPolicy Bypass -File install_claude_hooks.ps1
#   powershell -NoProfile -ExecutionPolicy Bypass -File install_claude_hooks.ps1 -Uninstall
#
# Patches ~/.claude/settings.json to register SessionStart / UserPromptSubmit /
# PreToolUse / PostToolUse / SubagentStop / Stop / SessionEnd hooks that POST
# each event to http://127.0.0.1:7710/claude_event via claude_hook_post.ps1.
#
# Coexists with other hooks (e.g. agent-flow) - only touches entries whose
# command contains "claude_hook_post.ps1". Safe to re-run.

param(
    [switch]$Uninstall
)

$ErrorActionPreference = 'Stop'

$SettingsPath = Join-Path $env:USERPROFILE '.claude\settings.json'
$HookScript   = Join-Path $PSScriptRoot 'claude_hook_post.ps1'
$HookCommand  = 'powershell -NoProfile -ExecutionPolicy Bypass -File "' + $HookScript + '"'
$HookEvents   = @('SessionStart','UserPromptSubmit','PreToolUse','PostToolUse','SubagentStop','Stop','SessionEnd')

if (-not (Test-Path $HookScript)) {
    Write-Host ('ERROR: ' + $HookScript + ' not found next to this installer.') -ForegroundColor Red
    exit 1
}

# Ensure the .claude folder exists
$SettingsDir = Split-Path $SettingsPath
if (-not (Test-Path $SettingsDir)) {
    New-Item -ItemType Directory -Force -Path $SettingsDir | Out-Null
}

# Load existing settings as a hashtable (start fresh if missing/corrupt)
$settings = @{}
if (Test-Path $SettingsPath) {
    try {
        $raw = Get-Content $SettingsPath -Raw -Encoding UTF8
        if ($raw -and $raw.Trim().Length -gt 0) {
            $parsed = $raw | ConvertFrom-Json
            $settings = @{}
            foreach ($prop in $parsed.PSObject.Properties) {
                $settings[$prop.Name] = $prop.Value
            }
        }
    } catch {
        Write-Host ('WARNING: ' + $SettingsPath + ' unreadable, starting fresh.') -ForegroundColor Yellow
        $settings = @{}
    }
}

# Normalize hooks section into a hashtable
if (-not $settings.ContainsKey('hooks') -or $null -eq $settings['hooks']) {
    $settings['hooks'] = @{}
}
if ($settings['hooks'] -isnot [hashtable]) {
    $hh = @{}
    foreach ($prop in $settings['hooks'].PSObject.Properties) {
        $hh[$prop.Name] = $prop.Value
    }
    $settings['hooks'] = $hh
}

# Strip existing Jarvis entries from an event's matcher list
function Remove-JarvisEntries {
    param($eventArr)
    if (-not $eventArr) { return @() }
    $kept = New-Object System.Collections.ArrayList
    foreach ($matcher in $eventArr) {
        if (-not $matcher.hooks) {
            [void]$kept.Add($matcher)
            continue
        }
        $keptHooks = New-Object System.Collections.ArrayList
        foreach ($h in $matcher.hooks) {
            $cmd = [string]$h.command
            if ($cmd -and ($cmd -match 'claude_hook_post\.ps1')) {
                continue
            }
            [void]$keptHooks.Add($h)
        }
        if ($keptHooks.Count -gt 0) {
            $matcher.hooks = $keptHooks.ToArray()
            [void]$kept.Add($matcher)
        }
    }
    return $kept.ToArray()
}

foreach ($ev in $HookEvents) {
    $existing = @()
    if ($settings['hooks'].ContainsKey($ev)) {
        $existing = Remove-JarvisEntries $settings['hooks'][$ev]
    }
    if (-not $Uninstall) {
        $newMatcher = [pscustomobject]@{
            matcher = '*'
            hooks   = @(
                [pscustomobject]@{
                    type    = 'command'
                    command = $HookCommand
                    timeout = 3
                }
            )
        }
        $existing = @($existing) + $newMatcher
    }
    if ($existing -and $existing.Count -gt 0) {
        $settings['hooks'][$ev] = $existing
    } elseif ($settings['hooks'].ContainsKey($ev)) {
        $settings['hooks'].Remove($ev)
    }
}

# Write back as UTF-8 (no BOM preferred, but PS 5.1 UTF-8 has BOM; Claude handles either)
$json = $settings | ConvertTo-Json -Depth 12
[System.IO.File]::WriteAllText($SettingsPath, $json, (New-Object System.Text.UTF8Encoding($false)))

if ($Uninstall) {
    Write-Host ('Removed Jarvis hooks from ' + $SettingsPath) -ForegroundColor Green
} else {
    Write-Host ('Installed Jarvis hooks in ' + $SettingsPath) -ForegroundColor Green
    Write-Host ('   Events:  ' + ($HookEvents -join ', '))
    Write-Host ('   Wrapper: ' + $HookScript)
    Write-Host ''
    Write-Host 'Next: close and reopen any Claude Code session - it will appear as a card in mission control.' -ForegroundColor Cyan
}
