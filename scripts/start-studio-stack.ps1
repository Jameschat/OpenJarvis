# Starts the full Jarvis Studio backend stack, detached from the calling session.
# Idempotent: each lane is skipped if already healthy.
#   1. Qwen fast lane (WSL MTP/Froggeric llama-server, port 8084 - primary)
#      Falls back to Windows-native BeeLlama plain decode (8082) if WSL fails.
#   2. LiteLLM proxy (port 4000, direct from .venv - avoids the uv-run sync hang)
#   3. Jarvis backend (jarvis serve, port 7710, env loaded from jarvis.bat)
# Safe to run repeatedly; logs land in dist\.
# NEVER co-load two 27B lanes on the 24GB 4090: VRAM overcommit garbles output.

param(
    [string]$RepoRoot = "E:\Claude\OpenJarvis",
    [string]$WslDistro = "JarvisUbuntu",
    [int]$QwenWaitSeconds = 1200
)

$ErrorActionPreference = "Stop"

# At boot/logon this task can fire before the E: drive (and WSL service) are
# ready. Wait for the repo root instead of dying with an unwritable transcript.
$bootDeadline = (Get-Date).AddSeconds(180)
while (-not (Test-Path $RepoRoot) -and (Get-Date) -lt $bootDeadline) { Start-Sleep -Seconds 5 }
if (-not (Test-Path $RepoRoot)) { exit 2 }

$logDir = Join-Path $RepoRoot "dist"
New-Item -ItemType Directory -Force -Path $logDir | Out-Null
Start-Transcript -Path (Join-Path $logDir "start-studio-stack.transcript.log") -Append | Out-Null
trap { Stop-Transcript | Out-Null; break }

# Warm the WSL service before the lane launch relies on it.
try { wsl.exe -d $WslDistro -- true 2>$null | Out-Null } catch { Start-Sleep -Seconds 10 }

function Test-Http {
    param([string]$Url, [int]$TimeoutSec = 3)
    try {
        $null = Invoke-WebRequest -Uri $Url -TimeoutSec $TimeoutSec -UseBasicParsing
        return $true
    } catch { return $false }
}

function Stop-NativeQwenFallback {
    # Do not keep the Windows-native fallback co-loaded after the WSL lane is
    # healthy. Two 27B lanes on one 24GB card causes VRAM pressure and slow UI.
    $listeners = Get-NetTCPConnection -LocalPort 8082 -State Listen -ErrorAction SilentlyContinue
    foreach ($listener in $listeners) {
        $proc = Get-Process -Id $listener.OwningProcess -ErrorAction SilentlyContinue
        if ($proc -and $proc.Path -and $proc.Path -match 'beellama|llama-server') {
            Stop-Process -Id $proc.Id -Force -ErrorAction SilentlyContinue
            Write-Host "[qwen] stopped native fallback on 8082 because WSL fast lane is healthy"
        }
    }
}

# --- Load env vars from jarvis.bat (secrets stay out of logs) ---
Select-String -Path (Join-Path $RepoRoot "jarvis.bat") -Pattern '^\s*set\s+([A-Z_]+)=(.+)$' | ForEach-Object {
    $m = $_.Line -replace '^\s*set\s+', ''
    $i = $m.IndexOf('=')
    if ($i -gt 0) { Set-Item -Path "env:$($m.Substring(0,$i))" -Value $m.Substring($i + 1) }
}

# --- Load secrets from %USERPROFILE%\.openjarvis\jarvis.env (migrated out of jarvis.bat) ---
$jarvisEnvFile = Join-Path $env:USERPROFILE '.openjarvis\jarvis.env'
if (Test-Path $jarvisEnvFile) {
    Get-Content $jarvisEnvFile | Where-Object { $_ -and ($_ -notmatch '^\s*#') } | ForEach-Object {
        $i = $_.IndexOf('=')
        if ($i -gt 0) { Set-Item -Path "env:$($_.Substring(0,$i))" -Value $_.Substring($i + 1) }
    }
}
$env:PYTHONIOENCODING = 'utf-8'

# --- 1. Qwen fast lane (WSL MTP/Froggeric, 8084 primary) ---
# BEST-EFFORT: a down or cold-loading lane must NEVER abort this script. The
# whole try/catch exists because $ErrorActionPreference=Stop (above) turns a
# native sub-script's stderr (e.g. its own curl health-probe firing before the
# port is listening) into a terminating error - which previously aborted the
# run before LiteLLM/backend started, so the app-gated watchdog could not
# self-heal a dead lane (ROADMAP Phase 9 #7, 2026-06-14). On any qwen failure
# we log and fall through: the lane launches via setsid nohup (survives this
# script) and a cold no-mmap ext4 load takes minutes, so the backend comes up
# now and the lane converges on the next watchdog cycle. Local Qwen is optional
# here - LiteLLM/chat fall back to BeeLlama (8082) then Ollama.
$qwenHealthy = $false
try {
    if (Test-Http "http://127.0.0.1:8084/health") {
        Write-Host "[qwen] WSL MTP lane already healthy on 8084"
        $qwenHealthy = $true
        Stop-NativeQwenFallback
    } else {
        $qwenScript = Join-Path $RepoRoot "scripts\start-qwen-mtp-froggeric-wsl.ps1"
        if (Test-Path $qwenScript) {
            try {
                & powershell.exe -ExecutionPolicy Bypass -File $qwenScript -WslDistro $WslDistro -WaitSeconds $QwenWaitSeconds
            } catch {
                Write-Warning "[qwen] MTP launcher reported an error ($($_.Exception.Message)); lane may still be loading async"
            }
            $qwenHealthy = Test-Http "http://127.0.0.1:8084/health"
        } else {
            Write-Warning "[qwen] WSL launcher missing: $qwenScript"
        }

        if ($qwenHealthy) {
            Write-Host "[qwen] WSL MTP lane healthy on 8084"
            Stop-NativeQwenFallback
        } else {
            # Guard the BeeLlama (~6GB Windows-native) fallback. It has repeatedly
            # STARVED the GPU and blocked the incoming lane during swaps / while the
            # card was committed (a game, or a 21GB lane mid-load). Two skips:
            #   1. Swap in progress: switch-qwen-lane.ps1 writes dist/.lane-swap-in-progress
            #      (treated stale after 10 min). 8084 being momentarily down mid-swap is
            #      expected - do NOT spawn a competing 6GB lane.
            #   2. VRAM already committed: if <8GB free, something big is resident
            #      (game / lane loading); adding 6GB would OOM. Skip and let it converge.
            $skipReason = $null
            # OPT-IN gate (2026-06-19): BeeLlama is OFF by default. It was the
            # ghost-VRAM culprit (a Windows-native ~16GB llama-server that survives
            # wsl --shutdown and blocked the 35B/MTP lanes from loading). We route
            # through the local Qwen lane only; enable this fallback explicitly with
            # OPENJARVIS_ENABLE_BEELLAMA=1 if you ever actually want it.
            $beellamaOptIn = ($env:OPENJARVIS_ENABLE_BEELLAMA -in @('1','true','on','True','ON'))
            if (-not $beellamaOptIn) { $skipReason = "BeeLlama disabled (set OPENJARVIS_ENABLE_BEELLAMA=1 to enable)" }
            $swapLock = Join-Path $RepoRoot "dist\.lane-swap-in-progress"
            if (-not $skipReason -and (Test-Path $swapLock)) {
                $age = (Get-Date) - (Get-Item $swapLock).LastWriteTime
                if ($age.TotalMinutes -lt 10) { $skipReason = "a lane swap is in progress" }
                else { Remove-Item $swapLock -Force -ErrorAction SilentlyContinue }
            }
            if (-not $skipReason) {
                $freeMiB = 0
                try { $freeMiB = [int](((& nvidia-smi --query-gpu=memory.free --format=csv,noheader,nounits 2>$null) -join '') -replace '[^0-9]','') } catch {}
                if ($freeMiB -gt 0 -and $freeMiB -lt 8000) { $skipReason = "only ${freeMiB}MiB VRAM free (card committed)" }
            }
            if ($skipReason) {
                Write-Warning "[qwen] skipping BeeLlama fallback: $skipReason; chat falls through to Ollama/remote, lane converges next watchdog cycle"
            } else {
                Write-Warning "[qwen] WSL lane not healthy yet; trying native BeeLlama fallback (8082, plain decode - short prompts only)"
                $beellamaScript = Join-Path $RepoRoot "scripts\start-qwen-beellama-dflash-service.ps1"
                if (Test-Path $beellamaScript) {
                    try {
                        & powershell.exe -ExecutionPolicy Bypass -File $beellamaScript -ContextTokens 16384 -WaitSeconds 420
                    } catch {
                        Write-Warning "[qwen] BeeLlama fallback launcher errored ($($_.Exception.Message))"
                    }
                }
                if (Test-Http "http://127.0.0.1:8082/health" -TimeoutSec 5) {
                    Write-Host "[qwen] BeeLlama fallback healthy on 8082 (LiteLLM falls through to it when 8084 is down)"
                } else {
                    Write-Warning "[qwen] no local Qwen lane up yet; continuing to LiteLLM/backend (chat falls back to Ollama, lane converges next watchdog cycle)"
                }
            }
        }
    }
} catch {
    Write-Warning "[qwen] lane bring-up errored ($($_.Exception.Message)); continuing so the stack still recovers"
}

# --- 2. LiteLLM proxy (4000) ---
if (Test-Http "http://127.0.0.1:4000/health/liveliness") {
    Write-Host "[litellm] already healthy on 4000"
} else {
    $litellm = Join-Path $RepoRoot ".venv\Scripts\litellm.exe"
    Start-Process -FilePath $litellm `
        -ArgumentList "--config", "configs/litellm.yaml", "--port", "4000" `
        -WorkingDirectory $RepoRoot -WindowStyle Hidden `
        -RedirectStandardOutput (Join-Path $logDir "litellm-4000.log") `
        -RedirectStandardError (Join-Path $logDir "litellm-4000.err.log")
    $deadline = (Get-Date).AddSeconds(90)
    while ((Get-Date) -lt $deadline -and -not (Test-Http "http://127.0.0.1:4000/health/liveliness")) { Start-Sleep -Seconds 4 }
    if (Test-Http "http://127.0.0.1:4000/health/liveliness") {
        Write-Host "[litellm] healthy on 4000"
    } else {
        throw "[litellm] failed to start - see dist\litellm-4000.err.log"
    }
}

# --- 3. Jarvis backend (7710) ---
if (Test-Http "http://127.0.0.1:7710/studio/ping") {
    Write-Host "[backend] already healthy on 7710"
} else {
    $jarvis = Join-Path $RepoRoot ".venv\Scripts\jarvis.exe"
    # orchestrator (not "simple"): the tool-using agent. "simple" has
    # accepts_tools=False, so it never loads the file_edit/todo_write/etc tools
    # and the chat never runs the agentic loop. orchestrator honours
    # config.agent.tools (+ workspace_dir scoping) so build/code/diff/plan work.
    Start-Process -FilePath $jarvis `
        -ArgumentList "serve", "--port", "7710", "--model", "qwen3.6-27b-local", "--agent", "orchestrator" `
        -WorkingDirectory $RepoRoot -WindowStyle Hidden `
        -RedirectStandardOutput (Join-Path $logDir "jarvis-serve-7710.log") `
        -RedirectStandardError (Join-Path $logDir "jarvis-serve-7710.err.log")
    $deadline = (Get-Date).AddSeconds(150)
    while ((Get-Date) -lt $deadline -and -not (Test-Http "http://127.0.0.1:7710/studio/ping")) { Start-Sleep -Seconds 4 }
    if (Test-Http "http://127.0.0.1:7710/studio/ping") {
        Write-Host "[backend] healthy on 7710"
    } else {
        throw "[backend] failed to start - see dist\jarvis-serve-7710.err.log"
    }
}

# --- 4. agentmemory sidecar (7730) ---
# Episodic memory. Was only started by jarvis.bat, so every Game Mode
# park/resume cycle left it dead (Memory page showed it offline, 2026-06-13).
# OPENAI_API_KEY is stripped for this child only: agentmemory's provider
# detection picks openai first, whose chat is unsupported - with it cleared,
# the ANTHROPIC_* block in ~/.agentmemory/.env routes reflect to local Qwen.
if (Test-Http "http://127.0.0.1:7730/agentmemory/livez") {
    Write-Host "[agentmemory] already healthy on 7730"
} else {
    $iii = Join-Path $env:USERPROFILE ".local\bin\iii.exe"
    # iii-config.yaml is the PROVEN config (absolute data paths, shared with
    # the .claude MCP hooks). iii-agentmemory.yaml has relative ./data paths
    # (cwd-dependent) and never served livez when launched here (2026-06-13).
    $iiiConfig = Join-Path $env:USERPROFILE ".openjarvis\iii-config.yaml"
    if ((Test-Path $iii) -and (Test-Path $iiiConfig)) {
        $psi = New-Object System.Diagnostics.ProcessStartInfo
        $psi.FileName = $iii
        $psi.Arguments = "--config `"$iiiConfig`""
        $psi.UseShellExecute = $false
        $psi.CreateNoWindow = $true
        $psi.EnvironmentVariables.Remove('OPENAI_API_KEY') | Out-Null
        [System.Diagnostics.Process]::Start($psi) | Out-Null
        $deadline = (Get-Date).AddSeconds(45)
        while ((Get-Date) -lt $deadline -and -not (Test-Http "http://127.0.0.1:7730/agentmemory/livez")) { Start-Sleep -Seconds 3 }
        if (Test-Http "http://127.0.0.1:7730/agentmemory/livez") {
            Write-Host "[agentmemory] healthy on 7730"
        } else {
            Write-Host "[agentmemory] did not come up within 45s (non-fatal - episodic recall degrades gracefully)"
        }
    } else {
        Write-Host "[agentmemory] iii.exe or config missing - skipping (episodic memory disabled)"
    }
}

Write-Host ""
Write-Host "Studio stack status:"
foreach ($svc in @(
    @{ name = "Jarvis backend 7710"; url = "http://127.0.0.1:7710/studio/ping" },
    @{ name = "LiteLLM proxy 4000"; url = "http://127.0.0.1:4000/health/liveliness" },
    @{ name = "Qwen fast lane 8084"; url = "http://127.0.0.1:8084/health" },
    @{ name = "Qwen fallback 8082"; url = "http://127.0.0.1:8082/health" },
    @{ name = "Ollama 11434"; url = "http://127.0.0.1:11434/api/version" }
)) {
    $state = if (Test-Http $svc.url) { "OK" } else { "DOWN" }
    Write-Host ("  {0,-22} {1}" -f $svc.name, $state)
}
Stop-Transcript | Out-Null
