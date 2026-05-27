from pathlib import Path
import yaml

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


def test_beellama_litellm_config_uses_dflash_port_and_ollama_fallback(tmp_path: Path):
    config_path = tmp_path / "litellm.qwen-beellama.yaml"

    qwen_fast_lane.write_litellm_beellama_config(config_path)

    text = config_path.read_text(encoding="utf-8")
    assert "BeeLlama DFlash" in text
    assert "model_name: qwen3.6-27b-local" in text
    assert "model: openai/qwen3.6-27b-local" in text
    assert "api_base: http://localhost:8082/v1" in text
    assert "model_name: qwen3.6-27b-ollama" in text
    assert '- qwen3.6-27b-local: ["qwen3.6-27b-ollama"]' in text


def test_beellama_quality_litellm_config_is_valid_and_separate():
    config_path = Path("configs/litellm.qwen-beellama-quality.yaml")

    data = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    model_names = [item["model_name"] for item in data["model_list"]]

    assert "qwen3.6-27b-quality" in model_names
    assert "qwen3.6-27b-local" in model_names
    quality = next(item for item in data["model_list"] if item["model_name"] == "qwen3.6-27b-quality")
    assert quality["litellm_params"]["api_base"] == "http://localhost:8083/v1"
    assert {"qwen3.6-27b-quality": ["qwen3.6-27b-local", "qwen3.6-27b-ollama"]} in data["litellm_settings"]["fallbacks"]


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


def test_fastlane_server_command_can_use_qwen_mtp_checkpoint():
    command = qwen_fast_lane.build_llama_server_command(
        llama_server_path=Path("C:/llama/llama-server.exe"),
        model_path=Path("D:/models/Qwen3.6-27B-MTP-Q4_K_M-Q8nextn.gguf"),
        speculative_mode="draft-mtp",
    )

    assert "--model-draft" not in command
    assert "--spec-type" in command
    assert "draft-mtp" in command
    assert "--spec-draft-n-max" in command


def test_beellama_dflash_command_uses_documented_runtime_flags():
    command = qwen_fast_lane.build_beellama_dflash_command(
        beellama_server_path=Path("C:/beellama/beellama-server.exe"),
        model_path=Path("E:/Claude/models/Qwen3.6-27B-Q4_K_M.gguf"),
        draft_model_path=Path("E:/Claude/models/Qwen3.6-27B-DFlash-Q4_K_M.gguf"),
    )

    assert command[:2] == ["C:\\beellama\\beellama-server.exe", "-m"]
    assert "--spec-draft-model" in command
    assert "E:\\Claude\\models\\Qwen3.6-27B-DFlash-Q4_K_M.gguf" in command
    assert "--spec-type" in command
    assert "dflash" in command
    assert "--spec-dflash-cross-ctx" in command
    assert "--kv-unified" in command
    assert "--flash-attn" in command
    assert "--mlock" in command
    assert "--cache-type-k" in command
    assert "q4_0" in command
    assert "--port" in command
    assert "8082" in command


def test_beellama_dflash_quality_command_matches_anbeeld_4090_profile():
    command = qwen_fast_lane.build_beellama_dflash_quality_command(
        beellama_server_path=Path("C:/beellama/llama-server.exe"),
        model_path=Path("E:/Claude/models/Qwen3.6-27B-Q5_K_S.gguf"),
        draft_model_path=Path("E:/Claude/models/Qwen3.6-27B-DFlash-Q4_K_M.gguf"),
    )

    assert "E:\\Claude\\models\\Qwen3.6-27B-Q5_K_S.gguf" in command
    assert "--spec-type" in command
    assert "dflash" in command
    assert "--spec-dflash-cross-ctx" in command
    assert "1024" in command
    assert "--ctx-size" in command
    assert "102400" in command
    assert "--cache-type-k" in command
    assert "q5_0" in command
    assert "--cache-type-v" in command
    assert "q4_1" in command
    assert "--reasoning" in command
    assert "on" in command
    assert "--mlock" in command
    assert "--chat-template-kwargs" in command
    assert '{"preserve_thinking":true}' in command


def test_beellama_quality_start_script_checks_q5_model_before_launch():
    script = Path("scripts/start-qwen-beellama-dflash-quality.ps1").read_text(encoding="utf-8")

    assert "Qwen3.6-27B-Q5_K_S.gguf" in script
    assert "Qwen Q5_K_S target GGUF missing" in script
    assert "--spec-dflash-cross-ctx\", \"1024\"" in script
    assert "--ctx-size\", \"102400\"" in script
    assert "--cache-type-k\", \"q5_0\"" in script
    assert "--cache-type-v\", \"q4_1\"" in script
    assert "--mlock" in script
    assert "--reasoning\", \"on\"" in script


def test_turboq_mtp_command_uses_wsl_experimental_defaults():
    command = qwen_fast_lane.build_turboq_mtp_command(
        turboq_server_path="/home/jarvis/llama.cpp-turboq-mtp/build/bin/llama-server",
        model_path="/mnt/e/Claude/models/Qwen3.6-27B-MTP-TBQ4.gguf",
    )

    assert command[0] == "/home/jarvis/llama.cpp-turboq-mtp/build/bin/llama-server"
    assert "--port" in command
    assert "8084" in command
    assert "-np" in command
    assert "1" in command
    assert "--spec-type" in command
    assert "mtp" in command
    assert "--spec-draft-n-max" in command
    assert "3" in command
    assert "--cache-type-k" in command
    assert "tbq4_0" in command
    assert "--cache-type-v" in command
    assert "--flash-attn" in command
    assert "--reasoning" in command
    assert "off" in command
    assert "--reasoning-budget" in command
    assert "--no-cache-prompt" in command
    assert "--cache-ram" in command
    assert "--jinja" in command


def test_turboq_mtp_wsl_start_script_is_opt_in_and_separate():
    script = Path("scripts/start-qwen-mtp-turboq-wsl.ps1").read_text(encoding="utf-8")

    assert "EXPERIMENTAL" in script
    assert "llama.cpp-turboq-mtp" in script
    assert "Qwen3.6-27B-MTP-TBQ4.gguf" in script
    assert "-np 1" in script
    assert "--spec-type mtp" in script
    assert "--spec-draft-n-max $DraftMax" in script
    assert "--reasoning off" in script
    assert "--reasoning-budget 0" in script
    assert "--no-cache-prompt" in script
    assert "--cache-ram 0" in script
    assert "--cache-type-k $CacheTypeK" in script
    assert "8084" in script


def test_rotorquant_command_uses_long_context_coding_defaults():
    command = qwen_fast_lane.build_rotorquant_command(
        turboquant_server_path="/root/llama-cpp-turboquant/build/bin/llama-server",
        model_ref="majentik/Qwen3.6-35B-A3B-RotorQuant-GGUF-IQ4_XS",
    )

    assert command[0] == "/root/llama-cpp-turboquant/build/bin/llama-server"
    assert "-hf" in command
    assert "majentik/Qwen3.6-35B-A3B-RotorQuant-GGUF-IQ4_XS" in command
    assert "--port" in command
    assert "8085" in command
    assert "--ctx-size" in command
    assert "182000" in command
    assert "--cache-type-k" in command
    assert "q8_0" in command
    assert "--cache-type-v" in command
    assert "turbo4" in command
    assert "--threads" in command
    assert "24" in command
    assert "--batch-size" in command
    assert "4092" in command
    assert "--ubatch-size" in command
    assert "1024" in command
    assert "--no-context-shift" in command
    assert "--jinja" in command
    assert "--temp" in command
    assert "0.6" in command
    assert "--top-p" in command
    assert "0.95" in command
    assert "--repeat-penalty" in command
    assert "1.0" in command


def test_rotorquant_litellm_config_is_separate_deep_context_alias(tmp_path: Path):
    config_path = tmp_path / "litellm.qwen-rotorquant.yaml"

    qwen_fast_lane.write_litellm_rotorquant_config(config_path)

    data = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    model_names = [item["model_name"] for item in data["model_list"]]

    assert "qwen3.6-35b-a3b-rotorquant" in model_names
    assert "qwen3.6-27b-local" in model_names
    rotor = next(item for item in data["model_list"] if item["model_name"] == "qwen3.6-35b-a3b-rotorquant")
    assert rotor["litellm_params"]["api_base"] == "http://localhost:8085/v1"
    assert {"qwen3.6-35b-a3b-rotorquant": ["qwen3.6-27b-local", "qwen3.6-27b-ollama"]} in data["litellm_settings"]["fallbacks"]


def test_rotorquant_wsl_start_script_is_opt_in_and_separate():
    script = Path("scripts/start-qwen-rotorquant-wsl.ps1").read_text(encoding="utf-8")

    assert "EXPERIMENTAL" in script
    assert "llama-cpp-turboquant" in script
    assert "Qwen3.6-35B-A3B-RotorQuant-GGUF-IQ4_XS" in script
    assert "feature/turboquant-kv-cache" in script
    assert "--ctx-size $ContextTokens" in script
    assert "--cache-type-k $CacheTypeK" in script
    assert "--cache-type-v $CacheTypeV" in script
    assert "--no-context-shift" in script
    assert "8085" in script
