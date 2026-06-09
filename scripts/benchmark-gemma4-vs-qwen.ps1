param(
    [string]$Prompt = "Write a concise implementation plan for a Jarvis local agent that can inspect a repository, summarize risks, and propose the next safe action. Return valid JSON with keys summary, risks, next_action.",
    [int[]]$ContextSizes = @(16000, 32000, 60000),
    [int]$MaxTokens = 256,
    [int]$TimeoutSec = 900,
    [string]$OutputPath = "",
    [string]$QwenBaseUrl = "http://127.0.0.1:8084/v1",
    [string]$GemmaBaseUrl = "http://127.0.0.1:8087/v1",
    [string]$RemoteWorkerBaseUrl = "http://192.168.1.191:4000/v1"
)

$ErrorActionPreference = "Stop"

function Invoke-OpenAiBenchmark {
    param(
        [string]$Name,
        [string]$BaseUrl,
        [string]$Model,
        [int]$ContextTokens
    )
    $padding = ""
    if ($ContextTokens -gt 4096) {
        $padding = "`nContext calibration note: " + ("Jarvis benchmark context. " * [Math]::Min(2000, [Math]::Floor($ContextTokens / 12)))
    }
    $body = @{
        model = $Model
        messages = @(
            @{ role = "user"; content = "$Prompt$padding" }
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
        $previewText = ($content -replace "\s+", " ").Trim()
        $tps = if ($elapsed -gt 0 -and $completionTokens -gt 0) { [Math]::Round($completionTokens / $elapsed, 2) } else { 0 }
        [pscustomobject]@{
            runtime = $Name
            context_tokens = $ContextTokens
            ok = $true
            seconds = [Math]::Round($elapsed, 2)
            tokens = $completionTokens
            tokens_per_second = $tps
            preview = $previewText.Substring(0, [Math]::Min(160, $previewText.Length))
            error = ""
        }
    } catch {
        [pscustomobject]@{
            runtime = $Name
            context_tokens = $ContextTokens
            ok = $false
            seconds = [Math]::Round(((Get-Date) - $started).TotalSeconds, 2)
            tokens = 0
            tokens_per_second = 0
            preview = ""
            error = $_.Exception.Message
        }
    }
}

$routes = @(
    @{ name = "qwen-local"; base_url = $QwenBaseUrl; model = "qwen3.6-27b-local" },
    @{ name = "gemma4-local"; base_url = $GemmaBaseUrl; model = "gemma4-26b-unsloth-local" },
    @{ name = "remote-worker"; base_url = $RemoteWorkerBaseUrl; model = "qwen3.6-35b-a3b-rotorquant" }
)

$results = @()
foreach ($context in $ContextSizes) {
    foreach ($route in $routes) {
        Write-Host "Benchmarking $($route.name) at $context context tokens"
        $results += Invoke-OpenAiBenchmark -Name $route.name -BaseUrl $route.base_url -Model $route.model -ContextTokens $context
    }
}

$results | Format-Table -AutoSize

if (-not $OutputPath) {
    $repoRoot = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot "..")).Path
    $dist = Join-Path $repoRoot "dist"
    New-Item -ItemType Directory -Force -Path $dist | Out-Null
    $stamp = Get-Date -Format "yyyyMMdd-HHmmss"
    $OutputPath = Join-Path $dist "gemma4-benchmark-$stamp.json"
}

$results | ConvertTo-Json -Depth 4 | Set-Content -LiteralPath $OutputPath -Encoding UTF8
Write-Host "Benchmark written to: $OutputPath"
