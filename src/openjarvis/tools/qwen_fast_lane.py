"""Helpers for the optional Qwen llama.cpp speculative fast lane.

The public Jarvis model contract stays ``qwen3.6-27b-local``. This module
prepares an opt-in LiteLLM config that tries a local llama.cpp OpenAI-compatible
server first and falls back to the existing Ollama route.
"""
from __future__ import annotations

from pathlib import Path
from typing import Literal


DEFAULT_LLAMA_BASE_URL = "http://localhost:8081/v1"
DEFAULT_BEELLAMA_BASE_URL = "http://localhost:8082/v1"
DEFAULT_TURBOQ_MTP_BASE_URL = "http://localhost:8084/v1"
DEFAULT_ROTORQUANT_BASE_URL = "http://localhost:8085/v1"
DEFAULT_OLLAMA_BASE_URL = "http://localhost:11434"
PUBLIC_QWEN_ALIAS = "qwen3.6-27b-local"
ROTORQUANT_QWEN_ALIAS = "qwen3.6-35b-a3b-rotorquant"
OLLAMA_QWEN_ALIAS = "qwen3.6-27b-ollama"
OLLAMA_QWEN_MODEL = "qwen3.6:27b"

SpeculativeMode = Literal[
    "none",
    "draft-mtp",
    "ngram-simple",
    "ngram-map-k",
    "ngram-map-k4v",
    "ngram-mod",
]


def write_litellm_fastlane_config(
    path: Path,
    *,
    llama_base_url: str = DEFAULT_LLAMA_BASE_URL,
    ollama_base_url: str = DEFAULT_OLLAMA_BASE_URL,
) -> Path:
    """Write an opt-in LiteLLM config with llama.cpp first, Ollama fallback."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        _render_litellm_fastlane_config(
            llama_base_url=llama_base_url,
            ollama_base_url=ollama_base_url,
        ),
        encoding="utf-8",
    )
    return path


def write_litellm_beellama_config(
    path: Path,
    *,
    beellama_base_url: str = DEFAULT_BEELLAMA_BASE_URL,
    ollama_base_url: str = DEFAULT_OLLAMA_BASE_URL,
) -> Path:
    """Write an opt-in LiteLLM config with BeeLlama DFlash first, Ollama fallback."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        _render_litellm_fastlane_config(
            llama_base_url=beellama_base_url,
            ollama_base_url=ollama_base_url,
            runtime_name="BeeLlama DFlash",
        ),
        encoding="utf-8",
    )
    return path


def write_litellm_rotorquant_config(
    path: Path,
    *,
    rotorquant_base_url: str = DEFAULT_ROTORQUANT_BASE_URL,
    qwen27_base_url: str = DEFAULT_TURBOQ_MTP_BASE_URL,
    ollama_base_url: str = DEFAULT_OLLAMA_BASE_URL,
) -> Path:
    """Write an opt-in config for the 35B-A3B RotorQuant deep-context lane."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        f"""# Optional Qwen RotorQuant deep-context LiteLLM config.
#
# Prototype only. Use this to benchmark Qwen3.6-35B-A3B RotorQuant/IQ4_XS on
# a separate OpenAI-compatible server before promoting it to the live alias.

model_list:
  - model_name: {ROTORQUANT_QWEN_ALIAS}
    litellm_params:
      model: openai/{ROTORQUANT_QWEN_ALIAS}
      api_base: {rotorquant_base_url}
      api_key: sk-noop

  - model_name: {PUBLIC_QWEN_ALIAS}
    litellm_params:
      model: openai/{PUBLIC_QWEN_ALIAS}
      api_base: {qwen27_base_url}
      api_key: sk-noop

  - model_name: {OLLAMA_QWEN_ALIAS}
    litellm_params:
      model: ollama_chat/{OLLAMA_QWEN_MODEL}
      api_base: {ollama_base_url}

litellm_settings:
  fallbacks:
    - {ROTORQUANT_QWEN_ALIAS}: ["{PUBLIC_QWEN_ALIAS}", "{OLLAMA_QWEN_ALIAS}"]
    - {PUBLIC_QWEN_ALIAS}: ["{OLLAMA_QWEN_ALIAS}"]
  num_retries: 1
  request_timeout: 600
  drop_params: true

general_settings:
  store_model_in_db: false
""",
        encoding="utf-8",
    )
    return path


def _render_litellm_fastlane_config(
    *,
    llama_base_url: str,
    ollama_base_url: str,
    runtime_name: str = "llama.cpp",
) -> str:
    return f"""# Optional Qwen fast-lane LiteLLM config.
#
# Use only after the {runtime_name} benchmark beats the current Ollama path.
# Public model contract remains {PUBLIC_QWEN_ALIAS}; if the {runtime_name} server is
# down or unstable, LiteLLM falls back to {OLLAMA_QWEN_ALIAS}.

model_list:
  - model_name: gpt-5.4-mini
    litellm_params:
      model: openai/gpt-5.4-mini
      api_key: os.environ/OPENAI_API_KEY

  - model_name: gpt-4o-mini
    litellm_params:
      model: openai/gpt-4o-mini
      api_key: os.environ/OPENAI_API_KEY

  - model_name: {PUBLIC_QWEN_ALIAS}
    litellm_params:
      model: openai/{PUBLIC_QWEN_ALIAS}
      api_base: {llama_base_url}
      api_key: sk-noop

  - model_name: {OLLAMA_QWEN_ALIAS}
    litellm_params:
      model: ollama_chat/{OLLAMA_QWEN_MODEL}
      api_base: {ollama_base_url}

  - model_name: gpt-4o
    litellm_params:
      model: openai/gpt-4o
      api_key: os.environ/OPENAI_API_KEY

litellm_settings:
  fallbacks:
    - gpt-5.4-mini: ["gpt-4o-mini", "{PUBLIC_QWEN_ALIAS}"]
    - gpt-4o-mini: ["{PUBLIC_QWEN_ALIAS}"]
    - {PUBLIC_QWEN_ALIAS}: ["{OLLAMA_QWEN_ALIAS}"]
  num_retries: 1
  request_timeout: 600
  drop_params: true

general_settings:
  store_model_in_db: false
"""


def build_llama_server_command(
    *,
    llama_server_path: Path,
    model_path: Path,
    draft_model_path: Path | None = None,
    host: str = "127.0.0.1",
    port: int = 8081,
    context_tokens: int = 8192,
    gpu_layers: int = 99,
    draft_max: int = 16,
    speculative_mode: SpeculativeMode = "none",
) -> list[str]:
    """Return argv for a local llama.cpp OpenAI-compatible Qwen server."""
    command = [
        str(llama_server_path),
        "--model",
        str(model_path),
        "--host",
        host,
        "--port",
        str(port),
        "-c",
        str(context_tokens),
        "-ngl",
        str(gpu_layers),
        "-fa",
        "on",
        "--cache-prompt",
        "--no-mmproj",
    ]
    if draft_model_path is not None:
        command.extend([
            "--model-draft",
            str(draft_model_path),
            "-ngld",
            str(gpu_layers),
            "--spec-draft-n-max",
            str(draft_max),
        ])
    elif speculative_mode != "none":
        command.extend([
            "--spec-type",
            speculative_mode,
            "--spec-draft-n-max",
            str(max(draft_max, 16)),
        ])
    return command


def build_beellama_dflash_command(
    *,
    beellama_server_path: Path,
    model_path: Path,
    draft_model_path: Path,
    mmproj_path: Path | None = None,
    host: str = "127.0.0.1",
    port: int = 8082,
    context_tokens: int = 32768,
    gpu_layers: str = "all",
    draft_gpu_layers: str = "all",
    cache_type_k: str = "q4_0",
    cache_type_v: str = "q4_0",
    cross_context_tokens: int = 512,
    draft_max: int = 8,
) -> list[str]:
    """Return argv for BeeLlama's Qwen DFlash OpenAI-compatible server.

    Defaults start conservative for a 24 GB RTX 4090: Q4 target, Q4 caches,
    32K context, one server slot, and adaptive DFlash with an 8-token ceiling.
    """
    command = [
        str(beellama_server_path),
        "-m",
        str(model_path),
        "--spec-draft-model",
        str(draft_model_path),
        "--spec-type",
        "dflash",
        "--spec-dflash-cross-ctx",
        str(cross_context_tokens),
        "--host",
        host,
        "--port",
        str(port),
        "-np",
        "1",
        "--kv-unified",
        "-ngl",
        str(gpu_layers),
        "--spec-draft-ngl",
        str(draft_gpu_layers),
        "-b",
        "2048",
        "-ub",
        "512",
        "--ctx-size",
        str(context_tokens),
        "--cache-type-k",
        cache_type_k,
        "--cache-type-v",
        cache_type_v,
        "--flash-attn",
        "on",
        "--cache-ram",
        "0",
        "--jinja",
        "--no-mmap",
        "--mlock",
        "--no-host",
        "--temp",
        "0.6",
        "--top-k",
        "20",
        "--top-p",
        "1.0",
        "--min-p",
        "0.0",
        "--spec-draft-n-max",
        str(draft_max),
    ]
    if mmproj_path is not None:
        command.extend(["--mmproj", str(mmproj_path), "--no-mmproj-offload"])
    return command


def build_beellama_dflash_quality_command(
    *,
    beellama_server_path: Path,
    model_path: Path,
    draft_model_path: Path,
    mmproj_path: Path | None = None,
    host: str = "127.0.0.1",
    port: int = 8083,
) -> list[str]:
    """Return argv for the Anbeeld RTX 4090 Qwen DFlash quality profile.

    This profile is intentionally separate from the live fast profile. It
    targets the Q5_K_S model, larger DFlash cross-context, 100K context, and
    higher-quality KV cache settings for Studio/coding deep-work benchmarks.
    """
    command = build_beellama_dflash_command(
        beellama_server_path=beellama_server_path,
        model_path=model_path,
        draft_model_path=draft_model_path,
        mmproj_path=mmproj_path,
        host=host,
        port=port,
        context_tokens=102400,
        cache_type_k="q5_0",
        cache_type_v="q4_1",
        cross_context_tokens=1024,
        draft_max=8,
    )
    command.extend([
        "--reasoning",
        "on",
        "--chat-template-kwargs",
        '{"preserve_thinking":true}',
    ])
    return command


def build_turboq_mtp_command(
    *,
    turboq_server_path: str | Path,
    model_path: str | Path,
    host: str = "0.0.0.0",
    port: int = 8084,
    context_tokens: int = 65536,
    gpu_layers: int = 99,
    cache_type_k: str = "tbq4_0",
    cache_type_v: str = "tbq4_0",
    draft_max: int = 3,
) -> list[str]:
    """Return argv for the experimental TurboQuant/MTP Qwen server.

    This is not the live Jarvis lane. It models the WSL/Linux prototype path
    used to compare a custom llama.cpp TurboQuant+MTP fork against BeeLlama.
    """
    return [
        str(turboq_server_path),
        "-m",
        str(model_path),
        "--host",
        host,
        "--port",
        str(port),
        "-np",
        "1",
        "--ctx-size",
        str(context_tokens),
        "-ngl",
        str(gpu_layers),
        "--flash-attn",
        "on",
        "--cache-type-k",
        cache_type_k,
        "--cache-type-v",
        cache_type_v,
        "--spec-type",
        "mtp",
        "--spec-draft-n-max",
        str(draft_max),
        "--jinja",
        "--reasoning",
        "off",
        "--reasoning-budget",
        "0",
        "--no-cache-prompt",
        "--cache-ram",
        "0",
        "--no-mmap",
        "--temp",
        "0.6",
        "--top-k",
        "20",
        "--top-p",
        "1.0",
    ]


def build_rotorquant_command(
    *,
    turboquant_server_path: str | Path,
    model_ref: str,
    host: str = "0.0.0.0",
    port: int = 8085,
    context_tokens: int = 128000,
    gpu_layers: int = 99,
    threads: int = 24,
    batch_size: int = 4092,
    ubatch_size: int = 1024,
    cache_type_k: str = "q8_0",
    cache_type_v: str = "turbo4",
) -> list[str]:
    """Return argv for the experimental 35B-A3B RotorQuant long-context lane."""
    return [
        str(turboquant_server_path),
        "-hf",
        model_ref,
        "--host",
        host,
        "--port",
        str(port),
        "--ctx-size",
        str(context_tokens),
        "-ngl",
        str(gpu_layers),
        "--flash-attn",
        "on",
        "--threads",
        str(threads),
        "--batch-size",
        str(batch_size),
        "--ubatch-size",
        str(ubatch_size),
        "--cache-type-k",
        cache_type_k,
        "--cache-type-v",
        cache_type_v,
        "--parallel",
        "1",
        "--no-context-shift",
        "--jinja",
        "--temp",
        "0.6",
        "--top-p",
        "0.95",
        "--top-k",
        "20",
        "--min-p",
        "0.0",
        "--presence-penalty",
        "0.0",
        "--repeat-penalty",
        "1.0",
    ]
