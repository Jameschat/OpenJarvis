param(
    [Parameter(Mandatory = $true)][ValidateSet("fast", "coder")][string]$Target,
    [string]$RepoRoot = "E:\Claude\OpenJarvis",
    [int]$Port = 8084,
    [int]$WaitSeconds = 300
)

# Swap the single local GPU lane on $Port (default 8084). 24GB can't hold two
# 27-35B lanes, so selecting a different local profile in the app means: stop the
# active local WSL llama-server (frees VRAM), then start the target model on the
# SAME port so existing LiteLLM routing (qwen3.6-27b-local -> :8084) transparently
# follows. Triggered by the backend on a qwen-profile change (fast <-> coder).
#   fast  = Qwen3.6-27B MTP (froggeric)      coder = Qwen3-Coder-30B-A3B (96K)
# Remote/quality profiles don't call this (they route off-box / to another port).

$ErrorActionPreference = "Continue"
$logDir = Join-Path $RepoRoot "dist"
New-Item -ItemType Directory -Force -Path $logDir | Out-Null
$log = Join-Path $logDir "switch-qwen-lane.log"
function Note($m) { "$([DateTime]::Now.ToString('s')) [$Target] $m" | Tee-Object -FilePath $log -Append | Out-Null }

Note "switch requested -> $Target on port $Port"

# 1. Free VRAM: stop the local WSL llama-server (the active lane). Remote worker
#    is a different machine and is untouched.
& wsl.exe -d JarvisUbuntu -- bash -lc "pkill -f llama-server" 2>$null
Start-Sleep -Seconds 4
$still = & wsl.exe -d JarvisUbuntu -- bash -lc "ps -eo args | grep llama-server | grep -v grep | wc -l" 2>$null
Note "local llama-servers after stop: $still"

# 2. Start the target model on the local port.
if ($Target -eq "coder") {
    $script = Join-Path $RepoRoot "scripts\start-qwen3-coder-30b-a3b-wsl.ps1"
    & powershell.exe -ExecutionPolicy Bypass -File $script -Port $Port -ContextTokens 98304 -WaitSeconds $WaitSeconds 2>&1 | ForEach-Object { Note $_ }
} else {
    $script = Join-Path $RepoRoot "scripts\start-qwen-mtp-froggeric-wsl.ps1"
    & powershell.exe -ExecutionPolicy Bypass -File $script -Port $Port -WaitSeconds $WaitSeconds 2>&1 | ForEach-Object { Note $_ }
}

# 3. Verify health.
try {
    $h = Invoke-RestMethod -Uri "http://127.0.0.1:$Port/health" -TimeoutSec 5
    if ($h.status -eq "ok") { Note "lane healthy on $Port"; exit 0 }
} catch {}
Note "WARNING: lane not healthy on $Port after switch"
exit 1
