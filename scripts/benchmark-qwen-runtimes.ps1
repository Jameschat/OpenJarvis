param(
    [string]$Prompt = "Write a concise implementation plan for a Jarvis local agent that can inspect a repository, summarize risks, and propose the next safe action. Return valid JSON with keys summary, risks, next_action.",
    [int]$MaxTokens = 256,
    [int]$WarmupTokens = 32,
    [int]$ContextTokens = 4096,
    [int]$TimeoutSec = 900,
    [string]$OutputPath = ""
)

$ErrorActionPreference = "Stop"

function Invoke-OllamaBenchmark {
    param([string]$Name)
    $body = @{
        model = "qwen3.6:27b"
        prompt = $Prompt
        stream = $false
        options = @{
            num_predict = $MaxTokens
            num_ctx = $ContextTokens
            temperature = 0.2
        }
    } | ConvertTo-Json -Depth 6
    $started = Get-Date
    try {
        $response = Invoke-RestMethod -Method Post -Uri "http://127.0.0.1:11434/api/generate" -Body $body -ContentType "application/json" -TimeoutSec $TimeoutSec
        $elapsed = ((Get-Date) - $started).TotalSeconds
        $tokens = [int]($response.eval_count)
        $tps = if ($elapsed -gt 0) { [Math]::Round($tokens / $elapsed, 2) } else { 0 }
        [pscustomobject]@{
            runtime = $Name
            ok = $true
            seconds = [Math]::Round($elapsed, 2)
            tokens = $tokens
            tokens_per_second = $tps
            preview = (($response.response -replace "\s+", " ").Trim()).Substring(0, [Math]::Min(160, (($response.response -replace "\s+", " ").Trim()).Length))
            error = ""
        }
    } catch {
        [pscustomobject]@{
            runtime = $Name
            ok = $false
            seconds = [Math]::Round(((Get-Date) - $started).TotalSeconds, 2)
            tokens = 0
            tokens_per_second = 0
            preview = ""
            error = $_.Exception.Message
        }
    }
}

function Invoke-OpenAiBenchmark {
    param(
        [string]$Name,
        [string]$BaseUrl,
        [string]$Model
    )
    $body = @{
        model = $Model
        messages = @(
            @{ role = "user"; content = $Prompt }
        )
        max_tokens = $MaxTokens
        temperature = 0.2
        stream = $false
        chat_template_kwargs = @{ enable_thinking = $false }
    } | ConvertTo-Json -Depth 8
    $started = Get-Date
    try {
        $response = Invoke-RestMethod -Method Post -Uri "$BaseUrl/chat/completions" -Headers @{ Authorization = "Bearer sk-noop" } -Body $body -ContentType "application/json" -TimeoutSec $TimeoutSec
        $elapsed = ((Get-Date) - $started).TotalSeconds
        $completionTokens = 0
        if ($response.usage -and $response.usage.completion_tokens) {
            $completionTokens = [int]$response.usage.completion_tokens
        }
        $content = [string]$response.choices[0].message.content
        $tps = if ($elapsed -gt 0 -and $completionTokens -gt 0) { [Math]::Round($completionTokens / $elapsed, 2) } else { 0 }
        [pscustomobject]@{
            runtime = $Name
            ok = $true
            seconds = [Math]::Round($elapsed, 2)
            tokens = $completionTokens
            tokens_per_second = $tps
            preview = (($content -replace "\s+", " ").Trim()).Substring(0, [Math]::Min(160, (($content -replace "\s+", " ").Trim()).Length))
            error = ""
        }
    } catch {
        [pscustomobject]@{
            runtime = $Name
            ok = $false
            seconds = [Math]::Round(((Get-Date) - $started).TotalSeconds, 2)
            tokens = 0
            tokens_per_second = 0
            preview = ""
            error = $_.Exception.Message
        }
    }
}

Write-Host "Warmup: Ollama qwen3.6:27b"
$warmupBody = @{
    model = "qwen3.6:27b"
    prompt = "Reply OK."
    stream = $false
    options = @{ num_predict = $WarmupTokens; num_ctx = $ContextTokens; temperature = 0.0 }
} | ConvertTo-Json -Depth 5
try {
    Invoke-RestMethod -Method Post -Uri "http://127.0.0.1:11434/api/generate" -Body $warmupBody -ContentType "application/json" -TimeoutSec $TimeoutSec | Out-Null
} catch {
    Write-Host "Ollama warmup failed: $($_.Exception.Message)"
}

$results = @()
$results += Invoke-OllamaBenchmark -Name "ollama:qwen3.6:27b"
$results += Invoke-OpenAiBenchmark -Name "mainline-llama.cpp:8081" -BaseUrl "http://127.0.0.1:8081/v1" -Model "qwen3.6-27b-local"
$results += Invoke-OpenAiBenchmark -Name "beellama-dflash:8082" -BaseUrl "http://127.0.0.1:8082/v1" -Model "qwen3.6-27b-local"

$results | Format-Table -AutoSize

if (-not $OutputPath) {
    $stamp = Get-Date -Format "yyyyMMdd-HHmmss"
    $OutputPath = Join-Path $env:TEMP "qwen-runtime-benchmark-$stamp.json"
}
$results | ConvertTo-Json -Depth 4 | Set-Content -LiteralPath $OutputPath -Encoding UTF8
Write-Host "Benchmark written to: $OutputPath"
