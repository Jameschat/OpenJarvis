param(
    [string]$RepoUrl = "https://github.com/affaan-m/ECC.git",
    [string]$CacheDir = "$env:USERPROFILE\.openjarvis\skill-cache\ecc",
    [string]$TargetRoot = "$env:USERPROFILE\.openjarvis\skills\ecc",
    [switch]$AllowHooks,
    [switch]$AllowScripts
)

$ErrorActionPreference = "Stop"

$Skills = @(
    "agentic-engineering",
    "autonomous-agent-harness",
    "verification-loop",
    "tdd-workflow",
    "iterative-retrieval",
    "browser-qa",
    "search-first",
    "plan-orchestrate",
    "security-review",
    "benchmark-optimization-loop"
)

if ($AllowHooks) {
    throw "ECC-lite intentionally does not install hooks. Review ECC hooks separately before enabling them in Jarvis."
}
if ($AllowScripts) {
    throw "ECC-lite intentionally skips scripts. Review scripts separately before importing them into Jarvis."
}

if (Test-Path $CacheDir) {
    git -C $CacheDir pull --ff-only | Out-Host
} else {
    New-Item -ItemType Directory -Force -Path (Split-Path $CacheDir) | Out-Null
    git clone $RepoUrl $CacheDir | Out-Host
}

New-Item -ItemType Directory -Force -Path $TargetRoot | Out-Null

$Commit = (git -C $CacheDir rev-parse HEAD).Trim()
$InstalledAt = [DateTime]::UtcNow.ToString("yyyy-MM-ddTHH:mm:ssZ")
$Installed = @()

foreach ($Skill in $Skills) {
    $Source = Join-Path $CacheDir "skills\$Skill"
    $SkillFile = Join-Path $Source "SKILL.md"
    if (!(Test-Path $SkillFile)) {
        throw "Missing ECC skill: $Skill"
    }

    $Target = Join-Path $TargetRoot $Skill
    if (Test-Path $Target) {
        Remove-Item -LiteralPath $Target -Recurse -Force
    }
    New-Item -ItemType Directory -Force -Path $Target | Out-Null

    Copy-Item -LiteralPath $SkillFile -Destination (Join-Path $Target "SKILL.md")
    foreach ($Subdir in @("references", "assets", "templates")) {
        $SubSource = Join-Path $Source $Subdir
        if (Test-Path $SubSource) {
            Copy-Item -LiteralPath $SubSource -Destination (Join-Path $Target $Subdir) -Recurse
        }
    }

    $Metadata = @"
source = "ecc:$Skill"
repo_url = "$RepoUrl"
commit = "$Commit"
installed_at = "$InstalledAt"
scripts_imported = false
hooks_imported = false
"@
    [System.IO.File]::WriteAllText(
        (Join-Path $Target ".source"),
        $Metadata,
        [System.Text.Encoding]::ASCII
    )
    $Installed += $Skill
}

Write-Host "Installed ECC-lite skills into $TargetRoot"
$Installed | ForEach-Object { Write-Host " - ecc/$_" }
