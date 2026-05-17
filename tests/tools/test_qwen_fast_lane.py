from pathlib import Path

from openjarvis.tools import qwen_fast_lane


def test_fastlane_litellm_config_keeps_public_alias_and_ollama_fallback(tmp_path: Path):
    config_path = tmp_path / "litellm.qwen-fastlane.yaml"

    qwen_fast_lane.write_litellm_fastlane_config(config_path)

    text = config_path.read_text(encoding="utf-8")
    assert "model_name: qwen3.6-27b-local" in text
    assert "model: openai/qwen3.6-27b-local" in text
    assert "api_base: http://localhost:8081/v1" in text
    assert "model_name: qwen3.6-27b-ollama" in text
    assert "model: ollama_chat/qwen3.6:27b" in text
    assert '- qwen3.6-27b-local: ["qwen3.6-27b-ollama"]' in text


def test_fastlane_server_command_uses_llama_cpp_speculative_defaults():
    command = qwen_fast_lane.build_llama_server_command(
        llama_server_path=Path("C:/llama/llama-server.exe"),
        model_path=Path("D:/models/Qwen3.6-27B-Q4_K_M.gguf"),
        draft_model_path=Path("D:/models/Qwen3.5-0.8B-Q8_0.gguf"),
    )

    assert command[:2] == ["C:\\llama\\llama-server.exe", "--model"]
    assert "--model-draft" in command
    assert "--spec-draft-n-max" in command
    assert "16" in command
    assert "--host" in command
    assert "127.0.0.1" in command
    assert "--port" in command
    assert "8081" in command
    assert "-ngl" in command
    assert "99" in command


def test_fastlane_server_command_can_use_ngram_probe_without_draft_model():
    command = qwen_fast_lane.build_llama_server_command(
        llama_server_path=Path("C:/llama/llama-server.exe"),
        model_path=Path("D:/models/Qwen3.6-27B-Q4_K_M.gguf"),
        speculative_mode="ngram-simple",
    )

    assert "--model-draft" not in command
    assert "--spec-type" in command
    assert "ngram-simple" in command
    assert "--spec-draft-n-max" in command
