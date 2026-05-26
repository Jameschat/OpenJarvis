# Qwen MTP/TurboQuant WSL Prototype

This is an experimental Qwen 3.6 27B runtime lane for chasing higher token/sec on an RTX 4090. It does not replace the current BeeLlama DFlash fast lane.

## Current Status

2026-05-26 smoke: `JarvisUbuntu` starts the CUDA-built `llama.cpp-turboq-mtp` fork on port `8084` with the existing `Qwen3.6-27B-MTP-Q4_K_M-Q8nextn.gguf` model. Health and a chat smoke passed. The benchmark script measured 65.56 tok/s on a 128-token Jarvis prompt versus Ollama at 3.25 tok/s, with BeeLlama/mainline endpoints offline for that run. Do not promote yet: the desired `Qwen3.6-27B-MTP-TBQ4.gguf` model is still missing and BeeLlama quality/throughput comparison is still pending.

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
--spec-type mtp
--spec-draft-n-max 3
-np 1
--cache-type-k tbq4_0
--cache-type-v tbq4_0
--flash-attn on
--jinja
```

## Start

If WSL is not installed, run this once from an elevated Administrator PowerShell:

```powershell
scripts\setup-wsl-qwen-turboq-prereqs.ps1
```

Windows may require a reboot after enabling `Microsoft-Windows-Subsystem-Linux` and `VirtualMachinePlatform`.

```powershell
scripts\start-qwen-mtp-turboq-wsl.ps1
```

Override paths if your WSL checkout/model paths differ:

```powershell
scripts\start-qwen-mtp-turboq-wsl.ps1 `
  -WslDistro JarvisUbuntu `
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
