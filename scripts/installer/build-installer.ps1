param(
    [switch]$IncludeSecrets,
    [switch]$SkipPayloadExport
)

$ErrorActionPreference = "Stop"
$installerDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$repoRoot = (Resolve-Path -LiteralPath (Join-Path $installerDir "..\..")).Path
$dist = Join-Path $repoRoot "dist\installer"
$iss = Join-Path $installerDir "JarvisSetup.iss"

if (-not $SkipPayloadExport) {
    $exportArgs = @{
        OutputDir = $dist
        OpenJarvisRoot = $repoRoot
        BrainRoot = "E:\Claude\Obsidian\Claude\Brain"
    }
    if ($IncludeSecrets) {
        & (Join-Path $installerDir "export-jarvis-payload.ps1") @exportArgs -IncludeSecrets
    } else {
        & (Join-Path $installerDir "export-jarvis-payload.ps1") @exportArgs
    }
}

$iscc = Get-Command iscc -ErrorAction SilentlyContinue
if (-not $iscc) {
    $candidates = @(
        "C:\Program Files\Inno Setup 7\ISCC.exe",
        "C:\Program Files (x86)\Inno Setup 7\ISCC.exe",
        "C:\Program Files (x86)\Inno Setup 6\ISCC.exe",
        "C:\Program Files\Inno Setup 6\ISCC.exe"
    )
    foreach ($candidate in $candidates) {
        if (Test-Path -LiteralPath $candidate) {
            $iscc = @{ Source = $candidate }
            break
        }
    }
}

if (-not $iscc) {
    Write-Host "Payload is ready at: $dist\JarvisPayload.zip"
    Write-Host "Inno Setup compiler was not found, so JarvisSetup.exe was not built."
    Write-Host "Install Inno Setup 7, then rerun this script."
    exit 2
}

& $iscc.Source $iss
Write-Host "Installer created at: $dist\JarvisSetup.exe"
