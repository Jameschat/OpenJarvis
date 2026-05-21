"""Helpers for the optional Qwen llama.cpp speculative fast lane.

The public Jarvis model contract stays ``qwen3.6-27b-local``. This module
prepares an opt-in LiteLLM config that tries a local llama.cpp OpenAI-compatible
server first and falls back to the existing Ollama route.
"""
from __future__ import annotations

from pathlib import Path
from typing import Literal


DEFAULT_LLAMA_BASE_URL = "http://localhost:8081/v1"
DEFAULT_OLLAMA_BASE_URL = "http://localhost:11434"
PUBLIC_QWEN_ALIAS = "qwen3.6-27b-local"
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


def _render_litellm_fastlane_config(
    *,
    llama_base_url: str,
    ollama_base_url: str,
) -> str:
    return f"""# Optional Qwen fast-lane LiteLLM config.
#
# Use only after the llama.cpp benchmark beats the current Ollama path.
# Public model contract remains {PUBLIC_QWEN_ALIAS}; if the llama.cpp server is
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
