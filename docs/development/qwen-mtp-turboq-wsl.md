# Qwen MTP/TurboQuant WSL Prototype

This is an experimental Qwen 3.6 27B runtime lane for chasing higher token/sec on an RTX 4090. It does not replace the current BeeLlama DFlash fast lane.

## Goal

Benchmark a WSL/Linux `llama.cpp-turboq-mtp` server on port `8084` against:

- Ollama `qwen3.6:27b`
- mainline llama.cpp on `8081`
- BeeLlama DFlash on `8082`
- BeeLlama Q5 quality lane on `8083` when manually added

Promotion bar before using it as Jarvis default:

- Beats BeeLlama visible-output prompts by at least 15%
- No raw tool-request leaks
- No broken JSON/tool-call formatting on Studio prompts
- Stable 30-turn Studio chat test
- No quality regression versus BeeLlama Q5 for coding/planning prompts

## Expected Shape

Run the custom server inside WSL/Linux and expose OpenAI-compatible HTTP on:

```text
http://127.0.0.1:8084/v1
```

Recommended prototype flags:

```text
--spec-type draft-mtp
--spec-draft-n-max 3
--cache-type-k tbq4_0
--cache-type-v tbq4_0
--flash-attn on
--jinja
```

## Start

```powershell
scripts\start-qwen-mtp-turboq-wsl.ps1
```

Override paths if your WSL checkout/model paths differ:

```powershell
scripts\start-qwen-mtp-turboq-wsl.ps1 `
  -WslDistro Ubuntu-24.04 `
  -TurboQServer "~/llama.cpp-turboq-mtp/build/bin/llama-server" `
  -Model "/mnt/e/Claude/models/Qwen3.6-27B-MTP-TBQ4.gguf"
```

## Benchmark

```powershell
scripts\benchmark-qwen-runtimes.ps1
```

The benchmark now includes `wsl-turboq-mtp:8084`. If the server is not running, that row fails without affecting the other rows.

## Safety

Keep this lane out of `configs/litellm.yaml` until benchmarks prove it. Use it as an opt-in benchmark target only.
