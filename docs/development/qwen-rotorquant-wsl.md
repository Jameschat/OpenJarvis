# Qwen 3.6 35B-A3B RotorQuant WSL Prototype

Prototype lane for comparing the long-context RotorQuant setup against Jarvis's
current local Qwen route.

## Current Live Baseline

- Alias: `qwen3.6-27b-local`
- Runtime: WSL TurboQ/MTP on port `8084`
- Model: `E:\Claude\models\Qwen3.6-27B-Q4_K_M-mtp.gguf`
- Real Studio prompt speed: roughly `73-88 tok/s`
- Fallbacks: BeeLlama DFlash on `8082`, then Ollama

## RotorQuant Candidate

- Alias: `qwen3.6-35b-a3b-rotorquant`
- Runtime: TheTom `llama-cpp-turboquant`
- Branch target: `feature/turboquant-kv-cache`
- Model ref: `majentik/Qwen3.6-35B-A3B-RotorQuant-GGUF-IQ4_XS`
- Port: `8085`
- Context target: `182000`
- KV cache: `q8_0` key, `turbo4` value
- Sampling: `temp=0.6`, `top_p=0.95`, `top_k=20`, `min_p=0.0`,
  `presence_penalty=0.0`, `repeat_penalty=1.0`

## Build Sketch

Inside `JarvisUbuntu`:

```bash
git clone https://github.com/thetom/llama-cpp-turboquant.git /root/llama-cpp-turboquant
cd /root/llama-cpp-turboquant
git checkout feature/turboquant-kv-cache
cmake -B build -DGGML_CUDA=ON -DCMAKE_BUILD_TYPE=Release -DLLAMA_BUILD_BORINGSSL=ON -DGGML_NATIVE=ON -DGGML_CUDA_FA_ALL_QUANTS=ON -DGGML_AVX2=ON -DLLAMA_CURL=ON
cmake --build build -j 24
```

Start the prototype server from Windows:

```powershell
E:\Claude\OpenJarvis\scripts\start-qwen-rotorquant-wsl.ps1
```

Benchmark against all known Qwen lanes:

```powershell
E:\Claude\OpenJarvis\scripts\benchmark-qwen-runtimes.ps1 -ContextTokens 4096
E:\Claude\OpenJarvis\scripts\benchmark-qwen-runtimes.ps1 -ContextTokens 65536
E:\Claude\OpenJarvis\scripts\benchmark-qwen-runtimes.ps1 -ContextTokens 100000
```

## Promotion Gate

Do not switch the live `qwen3.6-27b-local` alias until RotorQuant passes:

1. Studio planning prompt returns visible output.
2. Strict JSON response is valid.
3. `qwen_tool_requests` XML/fenced tool-call path remains parseable.
4. Multi-turn memory recall remains coherent at long context.
5. Real Studio prompt speed beats the current WSL MTP lane.
6. VRAM use stays stable on RTX 4090 with Jarvis background services stopped.
