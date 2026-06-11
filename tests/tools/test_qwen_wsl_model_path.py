from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]


def test_froggeric_launcher_prefers_wsl_ext4_model_with_windows_fallback():
    script = (ROOT / "scripts" / "start-qwen-mtp-froggeric-wsl.ps1").read_text(
        encoding="utf-8"
    )

    assert '[string]$Model = "/root/models/Qwen3.6-27B-Q4_K_M-mtp.gguf"' in script
    assert (
        '[string]$FallbackModel = "/mnt/e/Claude/models/Qwen3.6-27B-Q4_K_M-mtp.gguf"'
        in script
    )
    assert "Qwen WSL ext4 model missing" in script
    assert "-m $modelToUse" in script
