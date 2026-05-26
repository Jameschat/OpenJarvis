param(
    [string]$WslDistro = "JarvisUbuntu",
    [string]$Server = "/root/llama.cpp-turboq-mtp/build/bin/llama-server",
    [string]$Model = "/mnt/e/Claude/models/Qwen3.6-27B-Q4_K_M-mtp.gguf",
    [int]$Port = 8084,
    [int]$ContextTokens = 4096,
    [int[]]$DraftMaxValues = @(3, 4, 5, 6, 8),
    [string[]]$CacheProfiles = @("tbq4_0:tbq4_0", "q4_0:q4_0", "q5_0:q4_1"),
    [int]$MaxTokens = 256,
    [int]$TimeoutSec = 120,
    [string]$OutputPath = ""
)

$ErrorActionPreference = "Stop"

$Prompt = "Write a practical implementation plan for a local AI Studio that can create projects, read memory, search a code graph, run safe tools, verify work, and escalate to cloud agents only when needed. Use 8 concise bullets."
$JsonPrompt = "Return only valid JSON with keys status, model, next_action. Keep values short."

function Stop-WslServer {
    & wsl.exe -d $WslDistro -- bash -lc "pkill -f llama-server || true" | Out-Null
    Start-Sleep -Seconds 2
}

function Start-WslServer {
    param(
        [int]$DraftMax,
        [string]$CacheTypeK,
        [string]$CacheTypeV
    )

    $repoRoot = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot "..")).Path
    $logDir = Join-Path $repoRoot "dist"
    New-Item -ItemType Directory -Force -Path $logDir | Out-Null
    $stdout = Join-Path $logDir "qwen-mtp-tune-$DraftMax-$CacheTypeK-$CacheTypeV.log"
    $stderr = Join-Path $logDir "qwen-mtp-tune-$DraftMax-$CacheTypeK-$CacheTypeV.err.log"

    $bashCommand = @"
set -euo pipefail
exec $Server \
  -m $Model \
  --host 0.0.0.0 \
  --port $Port \
  -np 1 \
  --ctx-size $ContextTokens \
  -ngl 99 \
  --flash-attn on \
  --cache-type-k $CacheTypeK \
  --cache-type-v $CacheTypeV \
  --spec-type mtp \
  --spec-draft-n-max $DraftMax \
  --jinja \
  --reasoning off \
  --no-cache-prompt \
  --cache-ram 0 \
  --no-mmap \
  --temp 0.6 \
  --top-k 20 \
  --top-p 1.0
"@

    $process = Start-Process -FilePath "wsl.exe" -ArgumentList @("-d", $WslDistro, "--", "bash", "-lc", $bashCommand) -WindowStyle Hidden -RedirectStandardOutput $stdout -RedirectStandardError $stderr -PassThru
    $deadline = (Get-Date).AddSeconds(240)
    while ((Get-Date) -lt $deadline) {
        Start-Sleep -Seconds 2
        if ($process.HasExited) {
            $tail = ""
            if (Test-Path -LiteralPath $stderr) {
                $tail = (Get-Content -LiteralPath $stderr -Tail 50) -join "`n"
            }
            throw "Server exited during startup for draft=$DraftMax cache=$CacheTypeK/$CacheTypeV. $tail"
        }
        try {
            $health = Invoke-RestMethod -Uri "http://127.0.0.1:$Port/health" -TimeoutSec 3
            if ($health.status -eq "ok") {
                return $process
            }
        } catch {
            # Still warming.
        }
    }
    throw "Server did not become healthy for draft=$DraftMax cache=$CacheTypeK/$CacheTypeV"
}

function Invoke-ChatMeasure {
    param(
        [string]$Name,
        [string]$PromptText,
        [int]$Tokens
    )
    $body = @{
        model = "qwen3.6-27b-local"
        messages = @(@{ role = "user"; content = $PromptText })
        max_tokens = $Tokens
        temperature = 0.2
        stream = $false
        chat_template_kwargs = @{ enable_thinking = $false }
    } | ConvertTo-Json -Depth 8

    $started = Get-Date
    try {
        $response = Invoke-RestMethod -Method Post -Uri "http://127.0.0.1:$Port/v1/chat/completions" -Headers @{ Authorization = "Bearer sk-noop" } -ContentType "application/json" -Body $body -TimeoutSec $TimeoutSec
        $elapsed = ((Get-Date) - $started).TotalSeconds
        $completionTokens = [int]$response.usage.completion_tokens
        $content = [string]$response.choices[0].message.content
        if (-not $content -and $response.choices[0].message.reasoning_content) {
            $content = [string]$response.choices[0].message.reasoning_content
        }
        return [pscustomobject]@{
            name = $Name
            ok = $true
            seconds = [Math]::Round($elapsed, 2)
            tokens = $completionTokens
            tokens_per_second = if ($elapsed -gt 0) { [Math]::Round($completionTokens / $elapsed, 2) } else { 0 }
            preview = (($content -replace "\s+", " ").Trim()).Substring(0, [Math]::Min(120, (($content -replace "\s+", " ").Trim()).Length))
            error = ""
        }
    } catch {
        return [pscustomobject]@{
            name = $Name
            ok = $false
            seconds = [Math]::Round(((Get-Date) - $started).TotalSeconds, 2)
            tokens = 0
            tokens_per_second = 0
            preview = ""
            error = $_.Exception.Message
        }
    }
}

$results = @()
foreach ($cache in $CacheProfiles) {
    $parts = $cache.Split(":")
    $cacheK = $parts[0]
    $cacheV = $parts[1]
    foreach ($draftMax in $DraftMaxValues) {
        Write-Host "Testing draft=$draftMax cache=$cacheK/$cacheV"
        Stop-WslServer
        $process = $null
        try {
            $process = Start-WslServer -DraftMax $draftMax -CacheTypeK $cacheK -CacheTypeV $cacheV
            $plan1 = Invoke-ChatMeasure -Name "plan-1" -PromptText $Prompt -Tokens $MaxTokens
            $plan2 = Invoke-ChatMeasure -Name "plan-2" -PromptText $Prompt -Tokens $MaxTokens
            $json = Invoke-ChatMeasure -Name "json" -PromptText $JsonPrompt -Tokens 96
            $ok = $plan1.ok -and $plan2.ok -and $json.ok -and ($json.preview.TrimStart().StartsWith("{"))
            $avg = if ($plan1.ok -and $plan2.ok) { [Math]::Round(($plan1.tokens_per_second + $plan2.tokens_per_second) / 2, 2) } else { 0 }
            $results += [pscustomobject]@{
                draft_max = $draftMax
                cache_type_k = $cacheK
                cache_type_v = $cacheV
                ok = $ok
                plan1_tps = $plan1.tokens_per_second
                plan2_tps = $plan2.tokens_per_second
                avg_plan_tps = $avg
                json_ok = $json.ok
                json_preview = $json.preview
                errors = (($plan1.error, $plan2.error, $json.error) | Where-Object { $_ }) -join " | "
            }
        } catch {
            $results += [pscustomobject]@{
                draft_max = $draftMax
                cache_type_k = $cacheK
                cache_type_v = $cacheV
                ok = $false
                plan1_tps = 0
                plan2_tps = 0
                avg_plan_tps = 0
                json_ok = $false
                json_preview = ""
                errors = $_.Exception.Message
            }
        } finally {
            Stop-WslServer
        }
    }
}

$results | Sort-Object -Property ok, avg_plan_tps -Descending | Format-Table -AutoSize
if (-not $OutputPath) {
    $stamp = Get-Date -Format "yyyyMMdd-HHmmss"
    $OutputPath = Join-Path $env:TEMP "qwen-mtp-tuning-$stamp.json"
}
$results | ConvertTo-Json -Depth 5 | Set-Content -LiteralPath $OutputPath -Encoding UTF8
Write-Host "Tuning results written to: $OutputPath"
