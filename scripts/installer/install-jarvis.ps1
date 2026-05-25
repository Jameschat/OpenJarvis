param(
    [string]$PayloadZip = "",
    [string]$InstallRoot = "E:\Claude",
    [switch]$SkipModelPull
)

$ErrorActionPreference = "Stop"

function Write-Step {
    param([string]$Message)
    Write-Host ""
    Write-Host "== $Message =="
}

function Require-Command {
    param(
        [string]$Name,
        [string]$InstallHint
    )
    if (-not (Get-Command $Name -ErrorAction SilentlyContinue)) {
        throw "$Name is required. $InstallHint"
    }
}

function Copy-DirectoryContents {
    param(
        [string]$Source,
        [string]$Destination
    )
    if (-not (Test-Path -LiteralPath $Source)) {
        return
    }
    New-Item -ItemType Directory -Force -Path $Destination | Out-Null
    Get-ChildItem -LiteralPath $Source -Force | ForEach-Object {
        Copy-Item -LiteralPath $_.FullName -Destination $Destination -Recurse -Force
    }
}

if (-not $PayloadZip) {
    $PayloadZip = Join-Path $PSScriptRoot "JarvisPayload.zip"
}
if (-not (Test-Path -LiteralPath $PayloadZip)) {
    throw "Payload zip not found: $PayloadZip"
}

$installRootFull = [System.IO.Path]::GetFullPath($InstallRoot)
$openJarvisRoot = Join-Path $installRootFull "OpenJarvis"
$brainRoot = Join-Path $installRootFull "Obsidian\Claude\Brain"
$stage = Join-Path $env:TEMP ("jarvis-install-" + [guid]::NewGuid().ToString("N"))

Write-Step "Preparing install folders"
New-Item -ItemType Directory -Force -Path $installRootFull | Out-Null
New-Item -ItemType Directory -Force -Path $stage | Out-Null
Expand-Archive -LiteralPath $PayloadZip -DestinationPath $stage -Force

Write-Step "Checking prerequisites"
Require-Command -Name "git" -InstallHint "Install Git for Windows, then rerun Jarvis Setup."
Require-Command -Name "ollama" -InstallHint "Install Ollama from https://ollama.com/download/windows, then rerun Jarvis Setup."

if (-not (Get-Command "uv" -ErrorAction SilentlyContinue)) {
    $uvPath = Join-Path $env:LOCALAPPDATA "Programs\Python\Python312\Scripts\uv.exe"
    if (Test-Path -LiteralPath $uvPath) {
        $env:PATH = (Split-Path $uvPath) + ";" + $env:PATH
    } else {
        throw "uv is required. Install it first with: powershell -ExecutionPolicy ByPass -c `"irm https://astral.sh/uv/install.ps1 | iex`""
    }
}

Write-Step "Restoring OpenJarvis"
Copy-DirectoryContents -Source (Join-Path $stage "OpenJarvis") -Destination $openJarvisRoot

Write-Step "Restoring Brain vault"
Copy-DirectoryContents -Source (Join-Path $stage "Brain") -Destination $brainRoot

Write-Step "Restoring .openjarvis state"
$stateSource = Join-Path $stage "OpenJarvisState"
$stateDest = Join-Path $env:USERPROFILE ".openjarvis"
Copy-DirectoryContents -Source $stateSource -Destination $stateDest

Write-Step "Restoring secrets if present"
$secretsSource = Join-Path $stage "Secrets"
if (Test-Path -LiteralPath (Join-Path $secretsSource "jarvis.bat")) {
    Copy-Item -LiteralPath (Join-Path $secretsSource "jarvis.bat") -Destination (Join-Path $openJarvisRoot "jarvis.bat") -Force
}
if (-not (Test-Path -LiteralPath (Join-Path $openJarvisRoot "jarvis.bat"))) {
    $template = @"
@echo off
echo Jarvis secrets were not included in this installer.
echo Copy your live jarvis.bat from the old PC to:
echo   $openJarvisRoot\jarvis.bat
pause
"@
    $template | Set-Content -LiteralPath (Join-Path $openJarvisRoot "jarvis.bat") -Encoding ASCII
}

Write-Step "Installing Python environment"
Push-Location $openJarvisRoot
try {
    & uv sync --extra dev --extra server --extra inference-litellm
} finally {
    Pop-Location
}

if (-not $SkipModelPull) {
    Write-Step "Pulling Ollama models"
    $modelList = Join-Path $stage "ollama-models.txt"
    $models = @("qwen3.6:27b")
    if (Test-Path -LiteralPath $modelList) {
        $models = Get-Content -LiteralPath $modelList | Where-Object { $_ -and $_.Trim() }
    }
    foreach ($model in $models) {
        Write-Host "Pulling $model"
        & ollama pull $model
    }
}

Write-Step "Creating desktop shortcut"
$shortcutPath = Join-Path ([Environment]::GetFolderPath("Desktop")) "Jarvis.lnk"
$shell = New-Object -ComObject WScript.Shell
$shortcut = $shell.CreateShortcut($shortcutPath)
$shortcut.TargetPath = Join-Path $openJarvisRoot "jarvis.bat"
$shortcut.WorkingDirectory = $openJarvisRoot
$shortcut.IconLocation = "$env:SystemRoot\System32\shell32.dll,13"
$shortcut.Save()

Write-Step "Writing install summary"
@"
Jarvis install complete.

OpenJarvis: $openJarvisRoot
Brain vault: $brainRoot
State: $stateDest
Shortcut: $shortcutPath

Next:
1. Review $openJarvisRoot\jarvis.bat and add secrets if this installer excluded them.
2. Run the desktop Jarvis shortcut.
3. Open http://localhost:7710 after Jarvis starts.
"@ | Set-Content -LiteralPath (Join-Path $openJarvisRoot "INSTALL-SUMMARY.txt") -Encoding UTF8

Remove-Item -LiteralPath $stage -Recurse -Force
Write-Host ""
Write-Host "Jarvis install complete. Start it from the desktop shortcut."
