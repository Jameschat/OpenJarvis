param(
    [string]$OutputDir = "E:\Claude\OpenJarvis\dist\installer",
    [string]$OpenJarvisRoot = "E:\Claude\OpenJarvis",
    [string]$BrainRoot = "E:\Claude\Obsidian\Claude\Brain",
    [switch]$IncludeSecrets
)

$ErrorActionPreference = "Stop"

function New-CleanDirectory {
    param([string]$Path)
    if (Test-Path -LiteralPath $Path) {
        Remove-Item -LiteralPath $Path -Recurse -Force
    }
    New-Item -ItemType Directory -Force -Path $Path | Out-Null
}

function Copy-TreeFiltered {
    param(
        [string]$Source,
        [string]$Destination,
        [string[]]$ExcludeNames = @()
    )
    if (-not (Test-Path -LiteralPath $Source)) {
        throw "Source path not found: $Source"
    }
    New-Item -ItemType Directory -Force -Path $Destination | Out-Null
    $sourcePath = (Resolve-Path -LiteralPath $Source).Path
    Get-ChildItem -LiteralPath $sourcePath -Force | ForEach-Object {
        if ($ExcludeNames -contains $_.Name) {
            return
        }
        $target = Join-Path $Destination $_.Name
        if ($_.PSIsContainer) {
            Copy-TreeFiltered -Source $_.FullName -Destination $target -ExcludeNames $ExcludeNames
        } else {
            Copy-Item -LiteralPath $_.FullName -Destination $target -Force
        }
    }
}

$stamp = Get-Date -Format "yyyyMMdd-HHmmss"
$stage = Join-Path $OutputDir "payload-stage"
$payloadZip = Join-Path $OutputDir "JarvisPayload-$stamp.zip"
$latestZip = Join-Path $OutputDir "JarvisPayload.zip"

New-Item -ItemType Directory -Force -Path $OutputDir | Out-Null
New-CleanDirectory -Path $stage

$repoDest = Join-Path $stage "OpenJarvis"
$brainDest = Join-Path $stage "Brain"
$docsDest = Join-Path $stage "ClaudeDocs"
$stateDest = Join-Path $stage "OpenJarvisState"
$secretsDest = Join-Path $stage "Secrets"
$userAssetsDest = Join-Path $stage "UserAssets"

$repoExcludes = @(
    ".git",
    ".venv",
    ".pytest_cache",
    ".codegraph",
    ".code-review-graph",
    "__pycache__",
    "node_modules",
    "target",
    "build",
    "dist"
)
if (-not $IncludeSecrets) {
    $repoExcludes += @("jarvis.bat", "set_spotify_creds.bat")
}
Copy-TreeFiltered -Source $OpenJarvisRoot -Destination $repoDest -ExcludeNames $repoExcludes

if (Test-Path -LiteralPath $BrainRoot) {
    Copy-TreeFiltered -Source $BrainRoot -Destination $brainDest -ExcludeNames @(".obsidian", ".trash", "__pycache__")
}

$claudeDocsRoot = "E:\Claude\docs"
if (Test-Path -LiteralPath $claudeDocsRoot) {
    Copy-TreeFiltered -Source $claudeDocsRoot -Destination $docsDest -ExcludeNames @(".git", "__pycache__", "node_modules")
}

$openJarvisState = Join-Path $env:USERPROFILE ".openjarvis"
if (Test-Path -LiteralPath $openJarvisState) {
    Copy-TreeFiltered -Source $openJarvisState -Destination $stateDest -ExcludeNames @(
        "logs",
        "runs",
        "cache",
        "__pycache__"
    )
}

New-Item -ItemType Directory -Force -Path $userAssetsDest | Out-Null
$codexSuperpowers = Join-Path $env:USERPROFILE ".codex\plugins\cache\local\superpowers"
if (Test-Path -LiteralPath $codexSuperpowers) {
    Copy-TreeFiltered -Source $codexSuperpowers -Destination (Join-Path $userAssetsDest "codex-superpowers") -ExcludeNames @(".git", "node_modules", "__pycache__")
}
$claudeSuperpowers = Join-Path $env:USERPROFILE ".claude\plugins\cache\claude-plugins-official\superpowers"
if (Test-Path -LiteralPath $claudeSuperpowers) {
    Copy-TreeFiltered -Source $claudeSuperpowers -Destination (Join-Path $userAssetsDest "claude-superpowers") -ExcludeNames @(".git", "node_modules", "__pycache__")
}
$agentMemoryHome = Join-Path $env:USERPROFILE ".agentmemory"
if (Test-Path -LiteralPath $agentMemoryHome) {
    Copy-TreeFiltered -Source $agentMemoryHome -Destination (Join-Path $userAssetsDest "agentmemory-home") -ExcludeNames @("iii.pid", "logs", "cache", "__pycache__")
}
$iiiExe = Join-Path $env:USERPROFILE ".local\bin\iii.exe"
if (Test-Path -LiteralPath $iiiExe) {
    $localBinDest = Join-Path $userAssetsDest "local-bin"
    New-Item -ItemType Directory -Force -Path $localBinDest | Out-Null
    Copy-Item -LiteralPath $iiiExe -Destination $localBinDest -Force
}

New-Item -ItemType Directory -Force -Path $secretsDest | Out-Null
if ($IncludeSecrets) {
    $secretFiles = @(
        (Join-Path $OpenJarvisRoot "jarvis.bat"),
        (Join-Path $OpenJarvisRoot "set_spotify_creds.bat")
    )
    foreach ($file in $secretFiles) {
        if (Test-Path -LiteralPath $file) {
            Copy-Item -LiteralPath $file -Destination $secretsDest -Force
        }
    }
} else {
    @"
# Jarvis secrets were intentionally not exported.
# On the new PC, copy your live secrets into:
#   E:\Claude\OpenJarvis\jarvis.bat
# or rerun this exporter with -IncludeSecrets if you accept the risk.
"@ | Set-Content -LiteralPath (Join-Path $secretsDest "SECRETS_NOT_EXPORTED.txt") -Encoding UTF8
}

$modelListPath = Join-Path $stage "ollama-models.txt"
try {
    $models = (& ollama list 2>$null) -split "`r?`n" |
        Select-Object -Skip 1 |
        ForEach-Object { ($_ -split "\s+")[0] } |
        Where-Object { $_ -and $_ -ne "NAME" }
    $models | Set-Content -LiteralPath $modelListPath -Encoding UTF8
} catch {
    "qwen3.6:27b" | Set-Content -LiteralPath $modelListPath -Encoding UTF8
}

@{
    created_at = (Get-Date).ToString("o")
    source_machine = $env:COMPUTERNAME
    openjarvis_root = $OpenJarvisRoot
    brain_root = $BrainRoot
    claude_docs_root = $claudeDocsRoot
    includes_secrets = [bool]$IncludeSecrets
} | ConvertTo-Json -Depth 4 | Set-Content -LiteralPath (Join-Path $stage "manifest.json") -Encoding UTF8

if (Test-Path -LiteralPath $payloadZip) {
    Remove-Item -LiteralPath $payloadZip -Force
}
Compress-Archive -Path (Join-Path $stage "*") -DestinationPath $payloadZip -Force
Copy-Item -LiteralPath $payloadZip -Destination $latestZip -Force

Write-Host "Created payload: $payloadZip"
Write-Host "Updated latest payload: $latestZip"
if (-not $IncludeSecrets) {
    Write-Host "Secrets were not included. Use -IncludeSecrets only if the installer file will be kept private."
}
