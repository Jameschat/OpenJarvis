param(
    [string]$Distro = "Ubuntu-24.04"
)

$ErrorActionPreference = "Stop"

function Test-IsAdministrator {
    $identity = [Security.Principal.WindowsIdentity]::GetCurrent()
    $principal = [Security.Principal.WindowsPrincipal]::new($identity)
    return $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
}

if (-not (Test-IsAdministrator)) {
    throw "Run this script from an elevated Administrator PowerShell. WSL feature enablement requires elevation and may require reboot."
}

Write-Host "Enabling Windows optional features for WSL2..."
Enable-WindowsOptionalFeature -Online -FeatureName Microsoft-Windows-Subsystem-Linux -All -NoRestart | Out-Null
Enable-WindowsOptionalFeature -Online -FeatureName VirtualMachinePlatform -All -NoRestart | Out-Null

Write-Host "Installing/updating WSL..."
wsl.exe --update
wsl.exe --set-default-version 2

$distros = (& wsl.exe -l -q 2>$null) | ForEach-Object { ($_ -replace "`0", "").Trim() } | Where-Object { $_ }
if ($distros -notcontains $Distro) {
    Write-Host "Installing WSL distro: $Distro"
    wsl.exe --install -d $Distro
} else {
    Write-Host "WSL distro already installed: $Distro"
}

Write-Host ""
Write-Host "If Windows asks for a reboot, reboot before running scripts\start-qwen-mtp-turboq-wsl.ps1."
