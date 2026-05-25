param(
    [string]$BeeLlamaServer = "C:\tmp\beellama-v0.2.0\extract\llama-server.exe",
    [string]$Model = "E:\Claude\models\Qwen3.6-27B-Q4_K_M.gguf",
    [string]$DraftModel = "E:\Claude\models\Qwen3.6-27B-DFlash-Q4_K_M.gguf",
    [string]$Prompt = "Return compact JSON with keys status, plan, risk. Keep it under 180 words.",
    [int]$MaxTokens = 256,
    [int]$ContextTokens = 4096,
    [int]$DraftMax = 8,
    [int]$BasePort = 8092,
    [int]$Runs = 3,
    [int]$TimeoutSec = 180
)

$ErrorActionPreference = "Stop"

function Invoke-DFlashConfigBenchmark {
    param(
        [string]$Name,
        [string]$CacheTypeK,
        [string]$CacheTypeV,
        [int]$CrossContext,
        [int]$Port
    )

    $argsList = @(
        "-m", $Model,
        "--spec-draft-model", $DraftModel,
        "--spec-type", "dflash",
        "--spec-dflash-cross-ctx", "$CrossContext",
        "--host", "127.0.0.1",
        "--port", "$Port",
        "-np", "1",
        "--kv-unified",
        "-ngl", "all",
        "--spec-draft-ngl", "all",
        "-b", "2048",
        "-ub", "512",
        "--ctx-size", "$ContextTokens",
        "--cache-type-k", $CacheTypeK,
        "--cache-type-v", $CacheTypeV,
        "--flash-attn", "on",
        "--cache-ram", "0",
        "--jinja",
        "--no-mmap",
        "--mlock",
        "--no-host",
        "--temp", "0.6",
        "--top-k", "20",
        "--top-p", "1.0",
        "--min-p", "0.0",
        "--spec-draft-n-max", "$DraftMax"
    )

    $process = Start-Process `
        -FilePath $BeeLlamaServer `
        -ArgumentList $argsList `
        -WorkingDirectory (Split-Path -Parent $BeeLlamaServer) `
        -WindowStyle Hidden `
        -PassThru

    try {
        $deadline = (Get-Date).AddSeconds(140)
        while ((Get-Date) -lt $deadline) {
            Start-Sleep -Seconds 2
            if ($process.HasExited) {
                throw "server exited during startup"
            }

            try {
                $health = Invoke-RestMethod -Uri "http://127.0.0.1:$Port/health" -TimeoutSec 2
                if ($health.status -eq "ok") {
                    break
                }
            } catch {
                # Still warming.
            }
        }

        $samples = @()
        for ($run = 0; $run -lt $Runs; $run++) {
            $body = @{
                model = "qwen3.6-27b-local"
                messages = @(@{ role = "user"; content = $Prompt })
                max_tokens = $MaxTokens
                temperature = 0.2
                stream = $false
                chat_template_kwargs = @{ enable_thinking = $false }
            } | ConvertTo-Json -Depth 8

            $started = Get-Date
            $response = Invoke-RestMethod `
                -Method Post `
                -Uri "http://127.0.0.1:$Port/v1/chat/completions" `
                -Headers @{ Authorization = "Bearer sk-noop" } `
                -Body $body `
                -ContentType "application/json" `
                -TimeoutSec $TimeoutSec
            $elapsed = ((Get-Date) - $started).TotalSeconds
            $tokens = [int]$response.usage.completion_tokens
            $samples += [Math]::Round($tokens / $elapsed, 2)
        }

        [pscustomobject]@{
            config = $Name
            cache_k = $CacheTypeK
            cache_v = $CacheTypeV
            cross_ctx = $CrossContext
            draft_max = $DraftMax
            context_tokens = $ContextTokens
            avg_tps = [Math]::Round((($samples | Measure-Object -Average).Average), 2)
            samples = ($samples -join ", ")
            error = ""
        }
    } catch {
        [pscustomobject]@{
            config = $Name
            cache_k = $CacheTypeK
            cache_v = $CacheTypeV
            cross_ctx = $CrossContext
            draft_max = $DraftMax
            context_tokens = $ContextTokens
            avg_tps = 0
            samples = ""
            error = $_.Exception.Message
        }
    } finally {
        if ($process -and -not $process.HasExited) {
            Stop-Process -Id $process.Id -Force
            Start-Sleep -Seconds 2
        }
    }
}

if (-not (Test-Path -LiteralPath $BeeLlamaServer)) {
    throw "BeeLlama server missing: $BeeLlamaServer"
}
if (-not (Test-Path -LiteralPath $Model)) {
    throw "Target model missing: $Model"
}
if (-not (Test-Path -LiteralPath $DraftModel)) {
    throw "DFlash draft model missing: $DraftModel"
}

$configs = @(
    @{ name = "q4kv-cross512"; k = "q4_0"; v = "q4_0"; cross = 512 },
    @{ name = "q4kv-cross1024"; k = "q4_0"; v = "q4_0"; cross = 1024 },
    @{ name = "q5q4-cross512"; k = "q5_0"; v = "q4_1"; cross = 512 },
    @{ name = "q5q4-cross1024"; k = "q5_0"; v = "q4_1"; cross = 1024 }
)

$results = @()
for ($index = 0; $index -lt $configs.Count; $index++) {
    $config = $configs[$index]
    $results += Invoke-DFlashConfigBenchmark `
        -Name $config.name `
        -CacheTypeK $config.k `
        -CacheTypeV $config.v `
        -CrossContext $config.cross `
        -Port ($BasePort + $index)
}

$results | Sort-Object avg_tps -Descending | Format-Table -AutoSize
