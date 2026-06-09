from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]


def test_gemma4_start_script_is_experimental_and_isolated():
    script = (ROOT / "scripts" / "start-gemma4-unsloth-wsl.ps1").read_text(
        encoding="utf-8"
    )

    assert "EXPERIMENTAL Gemma 4" in script
    assert "gemma-4-26B-A4B-it-qat-UD-Q4_K_XL.gguf" in script
    assert "[int]$Port = 8087" in script
    assert "[int]$ContextTokens = 60000" in script
    assert "[string]$CacheTypeK = \"q8_0\"" in script
    assert "[string]$CacheTypeV = \"turbo4\"" in script
    assert "--ctx-size $ContextTokens" in script
    assert "--cache-type-v $CacheTypeV" in script
    assert "--ctx-checkpoints 0" in script
    assert "--checkpoint-every-n-tokens -1" in script
    assert "--cache-ram 0" in script
    assert "--no-cache-prompt" in script
    assert "--reasoning off" in script
    assert "Qwen" not in script


def test_gemma4_benchmark_config_does_not_replace_default_litellm():
    default_config = (ROOT / "configs" / "litellm.yaml").read_text(encoding="utf-8")
    benchmark_config_path = ROOT / "configs" / "litellm.gemma4-benchmark.yaml"
    benchmark_config = benchmark_config_path.read_text(encoding="utf-8")

    assert "model_name: gemma4-26b-unsloth-local" in benchmark_config
    assert "model_name: qwen3.6-27b-local" in benchmark_config
    assert "model_name: qwen3.6-35b-a3b-remote" in benchmark_config
    assert "http://localhost:8087/v1" in benchmark_config
    assert "gemma4-26b-unsloth-local" not in default_config


def test_gemma4_benchmark_wrapper_compares_local_and_worker_routes():
    script = (ROOT / "scripts" / "benchmark-gemma4-vs-qwen.ps1").read_text(
        encoding="utf-8"
    )

    assert "$ContextSizes = @(16000, 32000, 60000)" in script
    assert "gemma4-local" in script
    assert "qwen-local" in script
    assert "remote-worker" in script
    assert "tokens_per_second" in script
    assert "gemma4-benchmark-" in script
