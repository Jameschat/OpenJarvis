from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]


def test_desktop_launcher_uses_qwen36_runtime_contract():
    launcher = (ROOT / "frontend" / "src-tauri" / "src" / "lib.rs").read_text(
        encoding="utf-8"
    )

    assert 'const STARTUP_MODEL: &str = "qwen3.6-27b-local";' in launcher
    assert 'const STARTUP_OLLAMA_MODEL: &str = "qwen3.6:27b";' in launcher
    assert "qwen3.5:" not in launcher
    assert "qwen3.6:27b" in launcher
    assert "fuser -k 8084/tcp" in launcher
    assert "let startup_model = STARTUP_MODEL;" in launcher
    assert "Do not auto-pull model variants at startup" in launcher


def test_wsl_mtp_launcher_clears_linux_side_port_before_starting():
    script = (ROOT / "scripts" / "start-qwen-mtp-froggeric-wsl.ps1").read_text(
        encoding="utf-8"
    )

    assert "Test-WslPortOpen" in script
    assert "Stop-WslPort" in script
    assert "fuser -k __PORT__/tcp" in script
    assert "Test-WslQwenHealth" in script
    assert "Start-WindowsQwenBridge" in script
    assert "qwen-wsl-port-proxy.py" in script
    assert "if (Test-WslQwenHealth -LocalPort $Port)" in script
    assert "Windows port $Port is occupied but Qwen health is not responding" in script
    assert "--port $Port" in script
    assert "--spec-type mtp" in script
    assert "--threads $Threads" in script
    assert "--batch-size $BatchSize" in script
    assert "--ubatch-size $UbatchSize" in script
    assert "--top-p 0.95" in script
    assert "--min-p 0.0" in script
    assert "--presence-penalty 0.0" in script
    assert "--repeat-penalty 1.0" in script
    assert "qwen-mtp-froggeric-8084.sh" in script
    assert '$wslLaunchScript = "/mnt/$drive$rest"' in script
    assert '$argList = @("-d", $WslDistro, "--", "bash", $wslLaunchScript)' in script


def test_qwen_wsl_port_proxy_is_user_mode_tcp_bridge():
    proxy = (ROOT / "scripts" / "qwen-wsl-port-proxy.py").read_text(
        encoding="utf-8"
    )

    assert "--listen-host" in proxy
    assert "--target-host" in proxy
    assert "socket.create_connection" in proxy
    assert "select.select" in proxy


def test_benchmark_harness_includes_vllm_jump_lane_without_replacing_8084():
    script = (ROOT / "scripts" / "benchmark-qwen-runtimes.ps1").read_text(
        encoding="utf-8"
    )

    assert "wsl-turboq-mtp:8084" in script
    assert "vllm-int4-mtp:8086" in script
    assert "qwen3.6-27b-vllm" in script
    assert "wsl-rotorquant-35b-a3b:8085" in script


def test_desktop_catalogue_only_offers_approved_qwen_models():
    palette = (
        ROOT / "frontend" / "src" / "components" / "CommandPalette.tsx"
    ).read_text(encoding="utf-8")
    overlay = (ROOT / "frontend" / "src-tauri" / "src" / "overlay.html").read_text(
        encoding="utf-8"
    )

    assert "qwen3.6:27b" in palette
    assert "qwen3.6:35b-a3b" in palette
    assert "qwen3.6-27b-local" in overlay
    assert "qwen3.5:" not in palette
    assert "qwen3.5:" not in overlay
    assert "gemma3:latest" not in palette


def test_studio_exposes_taste_skill_to_qwen_workflows():
    studio_page = (ROOT / "frontend" / "src" / "pages" / "StudioPage.tsx").read_text(
        encoding="utf-8"
    )
    runner = (ROOT / "src" / "openjarvis" / "tools" / "studio_runner.py").read_text(
        encoding="utf-8"
    )

    assert "id: 'taste-skill'" in studio_page
    assert "label: 'Taste Skill'" in studio_page
    assert '"taste-skill": {' in runner
    assert '"design-taste-frontend"' in runner
    assert "Use the installed Taste Skill for frontend work" in runner


def test_chat_surface_never_sends_blank_model():
    store = (ROOT / "frontend" / "src" / "lib" / "store.ts").read_text(
        encoding="utf-8"
    )
    input_area = (
        ROOT / "frontend" / "src" / "components" / "Chat" / "InputArea.tsx"
    ).read_text(encoding="utf-8")

    assert "const DEFAULT_LOCAL_MODEL = 'qwen3.6-27b-local';" in store
    assert "selectedModel: DEFAULT_LOCAL_MODEL" in store
    assert "model || get().selectedModel || DEFAULT_LOCAL_MODEL" in store
    assert "const defaultLocalModel = 'qwen3.6-27b-local';" in input_area
    assert "const activeModel = selectedModel || defaultLocalModel;" in input_area
    assert "{ model: activeModel" in input_area


def test_model_picker_never_shows_zero_installed_models_for_jarvis():
    api = (ROOT / "frontend" / "src" / "lib" / "api.ts").read_text(
        encoding="utf-8"
    )
    app = (ROOT / "frontend" / "src" / "App.tsx").read_text(encoding="utf-8")
    palette = (
        ROOT / "frontend" / "src" / "components" / "CommandPalette.tsx"
    ).read_text(encoding="utf-8")
    routes = (ROOT / "src" / "openjarvis" / "server" / "routes.py").read_text(
        encoding="utf-8"
    )
    cloud_router = (
        ROOT / "src" / "openjarvis" / "server" / "cloud_router.py"
    ).read_text(encoding="utf-8")

    assert "const DEFAULT_LOCAL_MODELS: ModelInfo[]" in api
    assert "qwen3.6-27b-local" in api
    assert "qwen3.6:27b" in api
    assert "qwen3.6:35b-a3b" in api
    assert "return ensureLocalModels([]);" in api
    assert "setModels(fallback);" in app
    assert "Installed Models (${models.length})" in palette
    assert 'DEFAULT_LOCAL_MODEL_IDS = ("qwen3.6-27b-local", "qwen3.6:27b", "qwen3.6:35b-a3b")' in routes
    assert "timeout=2" in cloud_router


def test_core_recommendation_only_uses_approved_qwen36_models():
    config = (ROOT / "src" / "openjarvis" / "core" / "config.py").read_text(
        encoding="utf-8"
    )

    assert '_MODEL_TIER_FALLBACK = "qwen3.6:27b"' in config
    assert '"qwen3.6:27b"' in config
    assert '"qwen3.6:35b-a3b"' in config
    assert "startswith(\"qwen3.5:\")" not in config
