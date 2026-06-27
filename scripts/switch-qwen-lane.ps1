param(
    [Parameter(Mandatory = $true)][ValidateSet("fast", "coder", "q35", "gptoss", "glm47", "gemma4")][string]$Target,
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

# Swap lock: start-studio-stack.ps1 checks for this and SKIPS the Windows-native
# BeeLlama fallback while it exists, so a mid-swap "8084 momentarily down" never
# triggers a 6GB BeeLlama spawn that starves the GPU and blocks the incoming lane.
# Removed in finally{} below; the stack also treats a lock older than 10 min as stale.
$swapLock = Join-Path $logDir ".lane-swap-in-progress"
Set-Content -Path $swapLock -Value "$Target $([DateTime]::Now.ToString('s'))" -Encoding ascii
function Clear-SwapLock { Remove-Item -Path $swapLock -Force -ErrorAction SilentlyContinue }

Note "switch requested -> $Target on port $Port"

# 1. Free VRAM: stop the local WSL llama-server (the active lane). Remote worker
#    is a different machine and is untouched. Use pkill -9: the MTP lane aborts
#    on a graceful shutdown ("free(): invalid pointer") which can strand its CUDA
#    allocation, so force-kill it to guarantee the VRAM is released.
& wsl.exe -d JarvisUbuntu -- bash -lc "pkill -9 -f llama-server" 2>$null
Start-Sleep -Seconds 4
$still = & wsl.exe -d JarvisUbuntu -- bash -lc "ps -eo args | grep llama-server | grep -v grep | wc -l" 2>$null
Note "local llama-servers after stop: $still"

# 1a. Kill the Windows-native BeeLlama fallback (C:\tmp\beellama-...\llama-server.exe).
#     It holds ~16GB of VRAM, SURVIVES wsl --shutdown, and is invisible to the WSL
#     nvidia-smi drain check below — so if it's resident the drain wait never sees
#     VRAM drop and the incoming lane OOMs. The swap lock prevents NEW BeeLlama
#     spawns; this clears any already-running one.
Get-Process llama-server -ErrorAction SilentlyContinue | ForEach-Object {
    Stop-Process -Id $_.Id -Force -ErrorAction SilentlyContinue
    Note "killed Windows-native llama-server/BeeLlama (pid $($_.Id))"
}

# 1b. Free the WINDOWS side of the port AND clear ALL stale WSL->Windows
#     port-proxies (qwen-wsl-port-proxy.py). These leak one-per-launch and, left
#     running, make the target start script see "Windows port occupied" and abort.
Get-CimInstance Win32_Process -Filter "Name='python.exe'" -ErrorAction SilentlyContinue |
    Where-Object { $_.CommandLine -match 'qwen-wsl-port-proxy' } | ForEach-Object {
        Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue
        Note "killed stale port-proxy (pid $($_.ProcessId))"
    }
Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue | ForEach-Object {
    Stop-Process -Id $_.OwningProcess -Force -ErrorAction SilentlyContinue
    Note "freed Windows port $Port (pid $($_.OwningProcess))"
}
Start-Sleep -Seconds 2

# 1c. CRITICAL on a 24GB card: both lanes (~21-22GB) nearly fill VRAM, and CUDA
#     teardown of the old lane takes several seconds. Starting the target before
#     the VRAM actually reclaims races into OOM / a slot-init hang. Wait for it.
for ($i = 0; $i -lt 25; $i++) {
    $used = (& wsl.exe -d JarvisUbuntu -- bash -lc "nvidia-smi --query-gpu=memory.used --format=csv,noheader,nounits" 2>$null) -join ''
    $used = ($used -replace '[^0-9]', '')
    if ($used -and [int]$used -lt 3500) { Note "VRAM drained to $used MiB after $($i*3)s"; break }
    Start-Sleep -Seconds 3
}

# 2. Start the target model on the local port — NON-BLOCKING.
#    Do NOT pipe the start script's output here: the detached llama-server inherits
#    that output handle, so the pipe never closes and this script blocks until the
#    caller's subprocess timeout kills it BEFORE the lock is cleared (orphaned lock).
#    Instead launch detached (output -> a log file) and poll health ourselves below.
$startLog = Join-Path $logDir "switch-target-start.log"
if ($Target -eq "q35") {
    # DEFAULT lane: Qwen3.6-35B-A3B, native 256K (KV only ~1.4GB — fits ~21GB).
    $script = Join-Path $RepoRoot "scripts\start-qwen3.6-35b-a3b-wsl.ps1"
    $startArgs = @("-NoProfile", "-ExecutionPolicy", "Bypass", "-File", $script, "-Port", $Port, "-ContextTokens", 262144, "-WaitSeconds", $WaitSeconds)
} elseif ($Target -eq "coder") {
    # 64K (not the standalone 96K): leaves ~3-4GB headroom so an in-place swap
    # (where the old lane's VRAM may not be 100% reclaimed) loads reliably.
    $script = Join-Path $RepoRoot "scripts\start-qwen3-coder-30b-a3b-wsl.ps1"
    $startArgs = @("-NoProfile", "-ExecutionPolicy", "Bypass", "-File", $script, "-Port", $Port, "-ContextTokens", 65536, "-WaitSeconds", $WaitSeconds)
} elseif ($Target -eq "gptoss") {
    # gpt-oss-20b: fast/frugal agentic lane (~15GB), 64K context with f16 KV.
    $script = Join-Path $RepoRoot "scripts\start-gptoss-wsl.ps1"
    $startArgs = @("-NoProfile", "-ExecutionPolicy", "Bypass", "-File", $script, "-Port", $Port, "-ContextTokens", 65536, "-WaitSeconds", $WaitSeconds)
} elseif ($Target -eq "glm47") {
    # GLM-4.7-Flash 30B-A3B (deepseek2): agentic/coding champ (~187 t/s, SWE-bench
    # 59). 32K ctx + f16 KV (~22GB) — deepseek2 KV is heavier, keep ctx conservative.
    $script = Join-Path $RepoRoot "scripts\start-glm47-wsl.ps1"
    $startArgs = @("-NoProfile", "-ExecutionPolicy", "Bypass", "-File", $script, "-Port", $Port, "-ContextTokens", 32768, "-WaitSeconds", $WaitSeconds)
} elseif ($Target -eq "gemma4") {
    # Gemma 4 26B-A4B (gemma4): stable big-context agentic (~122 t/s, native tools,
    # vision). 32K f16 KV (~20GB) — 64K hit ~23GB and CUDA-crashed under inference.
    $script = Join-Path $RepoRoot "scripts\start-gemma4-wsl.ps1"
    $startArgs = @("-NoProfile", "-ExecutionPolicy", "Bypass", "-File", $script, "-Port", $Port, "-ContextTokens", 32768, "-WaitSeconds", $WaitSeconds)
} else {
    $script = Join-Path $RepoRoot "scripts\start-qwen-mtp-froggeric-wsl.ps1"
    $startArgs = @("-NoProfile", "-ExecutionPolicy", "Bypass", "-File", $script, "-Port", $Port, "-WaitSeconds", $WaitSeconds)
}
Start-Process -FilePath "powershell.exe" -ArgumentList $startArgs -WindowStyle Hidden `
    -RedirectStandardOutput $startLog -RedirectStandardError "$startLog.err" | Out-Null
Note "target start launched ($Target); polling health on $Port"

# 3. Poll health; clear lock + exit as soon as the lane is up (don't wait on the
#    start script's process — it brings the WSL lane + Windows bridge up itself).
$tries = [Math]::Max(20, [int]($WaitSeconds / 3))
for ($i = 0; $i -lt $tries; $i++) {
    try {
        $h = Invoke-RestMethod -Uri "http://127.0.0.1:$Port/health" -TimeoutSec 3
        if ($h.status -eq "ok") { Note "lane healthy on $Port after $($i*3)s"; Clear-SwapLock; exit 0 }
    } catch {}
    Start-Sleep -Seconds 3
}
Note "WARNING: lane not healthy on $Port after switch ($WaitSeconds s)"
Clear-SwapLock
exit 1
