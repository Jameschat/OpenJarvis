param(
  [string]$RepoRoot = "E:\Claude\OpenJarvis",
  [string]$Branch = $env:JARVIS_WORKER_BRANCH,
  [string]$WorkerModel = $env:JARVIS_WORKER_MODEL,
  [string]$WorkerRepo = $env:JARVIS_WORKER_REPO,
  [string]$WorkerRepoRoot = "E:\Claude\OpenJarvis3090",
  [string]$LiteLLMUrl = $env:JARVIS_WORKER_LITELLM_URL,
  [switch]$AllowDirty,
  [switch]$PushWorkerStatus
)

$ErrorActionPreference = "Stop"

if ([string]::IsNullOrWhiteSpace($env:JARVIS_NODE_ROLE) -or $env:JARVIS_NODE_ROLE -ne 'worker') {
  throw "Refusing to update: set JARVIS_NODE_ROLE=worker on the second PC."
}

if ([string]::IsNullOrWhiteSpace($Branch)) {
  $Branch = "feat/qwen-autonomy"
}
if ([string]::IsNullOrWhiteSpace($WorkerModel)) {
  $WorkerModel = "qwen3.6-35b-a3b-rotorquant"
}
if ([string]::IsNullOrWhiteSpace($WorkerRepo)) {
  $WorkerRepo = "Jameschat/OpenJarvis3090"
}
if ([string]::IsNullOrWhiteSpace($LiteLLMUrl)) {
  $LiteLLMUrl = "http://127.0.0.1:4000/v1/chat/completions"
}

if (-not (Test-Path -LiteralPath $RepoRoot)) {
  throw "RepoRoot does not exist: $RepoRoot"
}

Push-Location $RepoRoot
try {
  $dirty = git status --porcelain
  if ($dirty -and -not $AllowDirty) {
    throw "Refusing to update with dirty tracked files. Commit/stash them or pass -AllowDirty."
  }

  git fetch origin $Branch
  git checkout $Branch
  git pull --ff-only origin $Branch

  $body = @{
    model = $WorkerModel
    messages = @(
      @{ role = "user"; content = "Reply with jarvis-remote-ok only." }
    )
    max_tokens = 16
  } | ConvertTo-Json -Depth 5

  $response = Invoke-RestMethod -Uri $LiteLLMUrl -Method Post -Body $body -ContentType "application/json" -TimeoutSec 30
  $content = [string]$response.choices[0].message.content
  if ($content.Trim() -ne "jarvis-remote-ok") {
    throw "Worker smoke test failed. Expected jarvis-remote-ok, received: $content"
  }

  $statusDir = Join-Path $env:USERPROFILE ".openjarvis"
  New-Item -ItemType Directory -Force -Path $statusDir | Out-Null
  $statusPath = Join-Path $statusDir "worker-node-status.json"
  @{
    node_role = "worker"
    node_id = $env:JARVIS_NODE_ID
    worker_model = $WorkerModel
    worker_repo = $WorkerRepo
    branch = $Branch
    litellm_url = $LiteLLMUrl
    smoke = "jarvis-remote-ok"
    updated_at = (Get-Date).ToUniversalTime().ToString("o")
  } | ConvertTo-Json -Depth 5 | Set-Content -Encoding UTF8 -Path $statusPath

  if ($PushWorkerStatus) {
    if (-not (Test-Path -LiteralPath $WorkerRepoRoot)) {
      throw "WorkerRepoRoot does not exist: $WorkerRepoRoot"
    }
    Push-Location $WorkerRepoRoot
    try {
      Copy-Item -LiteralPath $statusPath -Destination "worker-node-status.json" -Force
      git add "worker-node-status.json"
      $statusDirty = git status --porcelain -- "worker-node-status.json"
      if ($statusDirty) {
        git commit -m "chore(worker): update 3090 node status"
        git push
      }
      else {
        Write-Host "No worker status changes to push"
      }
    }
    finally {
      Pop-Location
    }
  }

  Write-Host "Worker node updated and verified: jarvis-remote-ok"
}
finally {
  Pop-Location
}
