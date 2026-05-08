# plan-commit.ps1 — enforce conventional-commit prefix + Plan-Step trailer.
# Usage: .\scripts\plan-commit.ps1 -Prefix "feat(agents)" -Step A3 -Message "..."
param(
    [Parameter(Mandatory=$true)] [string]$Prefix,
    [Parameter(Mandatory=$true)] [string]$Step,
    [Parameter(Mandatory=$true)] [string]$Message
)

if ($Prefix -notmatch '^(feat|fix|refactor|docs|chore|test|perf)(\([a-z0-9-]+\))?$') {
    Write-Error "'$Prefix' is not a valid conventional-commit type(scope)"
    exit 2
}
if ($Step -notmatch '^[A-Z][0-9]+$') {
    Write-Error "Plan-step '$Step' must look like A1, B3, etc."
    exit 2
}

$body = @"
$Prefix`: $Message

Plan-Step: $Step
"@
git commit -m $body
