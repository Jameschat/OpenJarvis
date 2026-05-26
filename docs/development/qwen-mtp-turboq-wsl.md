# Qwen MTP/TurboQuant WSL Prototype

This is an experimental Qwen 3.6 27B runtime lane for chasing higher token/sec on an RTX 4090. It does not replace the current BeeLlama DFlash fast lane.

## Current Status

2026-05-26 smoke: `JarvisUbuntu` starts the CUDA-built `llama.cpp-turboq-mtp` fork on port `8084` with the existing `Qwen3.6-27B-MTP-Q4_K_M-Q8nextn.gguf` model. Health and a chat smoke passed. The benchmark script measured 65.56 tok/s on a 128-token Jarvis prompt versus Ollama at 3.25 tok/s, with BeeLlama/mainline endpoints offline for that run.

2026-05-26 promotion check: WSL MTP is viable only when it owns the GPU. The earlier timeouts/slow runs were caused by co-running BeeLlama and WSL MTP on the RTX 4090, leaving WSL only ~6.4 GB free VRAM and forcing both runtimes to contend for compute. With BeeLlama stopped and `Qwen3.6-27B-Q4_K_M-mtp.gguf` running alone, the same 256-token Studio planning prompt completed at 52.72, 71.32, and 71.92 tok/s. JSON, XML tool-request, and multi-turn checks passed at ~52-61 tok/s. `qwen3.6-27b-local` now routes to WSL MTP on `8084`; BeeLlama remains configured as fallback on `8082`.

2026-05-26 tuning sweep: tested draft depths `3,4,5,6,8` across `tbq4_0/tbq4_0`, `q4_0/q4_0`, and `q5_0/q4_1` KV cache profiles using two 256-token Studio planning prompts plus a strict JSON check per profile. Best reliable profile was draft `3` with `q4_0/q4_0`, averaging 73.53 tok/s. `tbq4_0/tbq4_0` draft `3` averaged 70.91 tok/s. Higher draft depths were slower and some profiles wrapped JSON in code fences, so the promoted launcher now defaults to `q4_0/q4_0` and draft `3`.

2026-05-26 chat template update: added Froggeric's fixed Qwen chat template at `configs/qwen/froggeric-chat-template.jinja` and wired the WSL launcher to `--chat-template-file /mnt/e/Claude/OpenJarvis/configs/qwen/froggeric-chat-template.jinja`. With the fixed template plus the tuned `q4_0/q4_0` profile, the 256-token Studio planning prompt measured 78.21 tok/s, strict JSON measured 88.12 tok/s, XML tool-request output measured 82.48 tok/s, and multi-turn recall measured 59.15 tok/s. This improves effective reliability and speed, but still does not turn real Studio prompts into a sustained 130-150 tok/s path.

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
--cache-type-k q4_0
--cache-type-v q4_0
--flash-attn on
--jinja
--chat-template-file /mnt/e/Claude/OpenJarvis/configs/qwen/froggeric-chat-template.jinja
--reasoning off
--no-cache-prompt
--cache-ram 0
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

The promoted Froggeric MTP lane uses a dedicated launcher:

```powershell
scripts\start-qwen-mtp-froggeric-wsl.ps1
```

Do not run BeeLlama and WSL MTP together for performance tests. They share the
same RTX 4090, and co-running them produces misleading low token/sec numbers.

## Benchmark

```powershell
scripts\benchmark-qwen-runtimes.ps1
```

The benchmark now includes `wsl-turboq-mtp:8084`. If the server is not running, that row fails without affecting the other rows.

## Safety

Keep this lane out of `configs/litellm.yaml` until benchmarks prove it. Use it as an opt-in benchmark target only.
