"""Data-driven registration of OpenAI-compatible inference engines."""

from openjarvis.core.registry import EngineRegistry
from openjarvis.engine._openai_compat import _OpenAICompatibleEngine

_ENGINES = {
    "vllm": ("VLLMEngine", "http://localhost:8000", "/v1"),
    "sglang": ("SGLangEngine", "http://localhost:30000", "/v1"),
    "llamacpp": ("LlamaCppEngine", "http://localhost:8080", "/v1"),
    "mlx": ("MLXEngine", "http://localhost:8080", "/v1"),
    "lmstudio": ("LMStudioEngine", "http://localhost:1234", "/v1"),
    "exo": ("ExoEngine", "http://localhost:52415", "/v1"),
    "nexa": ("NexaEngine", "http://localhost:18181", "/v1"),
    "uzu": ("UzuEngine", "http://localhost:8000", ""),
    "apple_fm": ("AppleFmEngine", "http://localhost:8079", "/v1"),
    "lemonade": ("LemonadeEngine", "http://localhost:8000", "/v1"),
}

for _key, (_cls_name, _default_host, _api_prefix) in _ENGINES.items():
    _cls = type(
        _cls_name,
        (_OpenAICompatibleEngine,),
        {"engine_id": _key, "_default_host": _default_host, "_api_prefix": _api_prefix},
    )
    EngineRegistry.register(_key)(_cls)
    globals()[_cls_name] = _cls


# Cloud model prefixes the local proxy may also advertise (via its fallback
# chain) but that should still be served by CloudEngine, not the proxy.
_CLOUD_PREFIXES = ("gpt-", "o1-", "o3-", "o4-", "chatgpt-", "claude-", "gemini-", "MiniMax-")


@EngineRegistry.register("litellm_proxy")
class LiteLLMProxyEngine(_OpenAICompatibleEngine):
    """The local LiteLLM proxy (:4000), which serves the qwen local aliases WITH
    tool calling (verified). Use this as the default engine so the agent/tool
    loop reaches the local model. ``list_models`` excludes cloud-prefixed entries
    so a ``MultiEngine`` wrap still routes those to ``CloudEngine``.
    """

    engine_id = "litellm_proxy"
    _default_host = "http://localhost:4000"
    _api_prefix = "/v1"

    def list_models(self):
        return [m for m in super().list_models() if not m.startswith(_CLOUD_PREFIXES)]


globals()["LiteLLMProxyEngine"] = LiteLLMProxyEngine

__all__ = [name for name, _, _ in _ENGINES.values()] + ["LiteLLMProxyEngine"]
