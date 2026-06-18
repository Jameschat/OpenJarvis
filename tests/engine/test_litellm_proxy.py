from unittest.mock import patch

from openjarvis.engine._openai_compat import _OpenAICompatibleEngine
from openjarvis.engine.openai_compat_engines import LiteLLMProxyEngine


def test_default_host_is_the_local_proxy():
    eng = LiteLLMProxyEngine()
    assert eng._host == "http://localhost:4000"


def test_list_models_excludes_cloud_prefixes():
    eng = LiteLLMProxyEngine(host="http://localhost:4000")
    mixed = [
        "qwen3.6-27b-local",
        "gpt-4o",
        "qwen3.6-35b-a3b-remote",
        "gpt-4o-mini",
        "o3-mini",
        "claude-opus-4",
    ]
    with patch.object(_OpenAICompatibleEngine, "list_models", return_value=mixed):
        got = eng.list_models()
    assert got == ["qwen3.6-27b-local", "qwen3.6-35b-a3b-remote"]
