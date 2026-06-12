"""``jarvis doctor`` — run diagnostic checks on the OpenJarvis installation."""

from __future__ import annotations

import json
import re
import shutil
import subprocess
import sys
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional

import click
from rich.console import Console
from rich.table import Table

from openjarvis.core.config import DEFAULT_CONFIG_PATH, load_config
from openjarvis.tools.runtime_health import check_runtime_health


@dataclass
class CheckResult:
    """Result of a single diagnostic check."""

    name: str
    status: str  # "ok", "warn", "fail"
    message: str
    details: Optional[str] = None


# -- Individual checks -------------------------------------------------------


def _check_python_version() -> CheckResult:
    """Check that Python version is >= 3.10."""
    ver = sys.version_info
    version_str = f"{ver.major}.{ver.minor}.{ver.micro}"
    if (ver.major, ver.minor) >= (3, 10):
        return CheckResult("Python version", "ok", version_str)
    return CheckResult("Python version", "fail", f"{version_str} (requires >= 3.10)")


def _check_config_exists() -> CheckResult:
    """Check that the config file exists."""
    if DEFAULT_CONFIG_PATH.exists():
        return CheckResult("Config file", "ok", str(DEFAULT_CONFIG_PATH))
    return CheckResult(
        "Config file",
        "warn",
        f"Not found at {DEFAULT_CONFIG_PATH}",
        details="Run `jarvis init` to generate a config file.",
    )


def _check_config_parses() -> CheckResult:
    """Check that the config file parses successfully."""
    if not DEFAULT_CONFIG_PATH.exists():
        return CheckResult("Config parsing", "warn", "Skipped (no config file)")
    try:
        load_config()
        return CheckResult("Config parsing", "ok", "Config loaded successfully")
    except Exception as exc:
        return CheckResult("Config parsing", "fail", f"Parse error: {exc}")


def _ensure_engines_imported() -> None:
    """Import engine modules to trigger registration decorators."""
    try:
        import openjarvis.engine  # noqa: F401
    except Exception:
        pass


def _get_config() -> Any:
    """Load config or return a default if parsing fails."""
    try:
        return load_config()
    except Exception:
        from openjarvis.core.config import JarvisConfig

        return JarvisConfig()


def _check_engines() -> List[CheckResult]:
    """Probe each registered engine for health."""
    results: List[CheckResult] = []

    _ensure_engines_imported()

    from openjarvis.core.registry import EngineRegistry
    from openjarvis.engine import _discovery

    config = _get_config()

    for key in sorted(EngineRegistry.keys()):
        try:
            engine = _discovery._make_engine(key, config)
            if engine.health():
                results.append(CheckResult(f"Engine: {key}", "ok", "Reachable"))
            else:
                results.append(CheckResult(f"Engine: {key}", "warn", "Unreachable"))
        except Exception as exc:
            results.append(
                CheckResult(f"Engine: {key}", "warn", f"Unreachable ({exc})")
            )

    if not results:
        results.append(CheckResult("Engines", "warn", "No engines registered"))

    return results


def _check_models() -> List[CheckResult]:
    """List models from healthy engines."""
    results: List[CheckResult] = []

    _ensure_engines_imported()

    from openjarvis.core.registry import EngineRegistry
    from openjarvis.engine import _discovery

    config = _get_config()

    for key in sorted(EngineRegistry.keys()):
        try:
            engine = _discovery._make_engine(key, config)
            if engine.health():
                models = engine.list_models()
                if models:
                    model_list = ", ".join(models[:5])
                    suffix = f" (+{len(models) - 5} more)" if len(models) > 5 else ""
                    results.append(
                        CheckResult(
                            f"Models: {key}",
                            "ok",
                            f"{model_list}{suffix}",
                        )
                    )
                else:
                    results.append(
                        CheckResult(
                            f"Models: {key}",
                            "warn",
                            "No models available",
                            details="Pull a model (e.g. `ollama pull qwen3.5:2b`).",
                        )
                    )
        except Exception:
            continue

    return results


def _check_default_model() -> CheckResult:
    """Check whether the configured default model is available."""
    try:
        config = load_config()
    except Exception:
        return CheckResult("Default model", "warn", "Skipped (config unavailable)")

    default_model = config.intelligence.default_model
    if not default_model:
        return CheckResult(
            "Default model",
            "ok",
            "Not configured (auto-routing enabled)",
            details="Router will select a model dynamically.",
        )

    _ensure_engines_imported()

    from openjarvis.core.registry import EngineRegistry
    from openjarvis.engine import _discovery

    preferred = config.intelligence.preferred_engine or config.engine.default
    check_order = []
    if preferred:
        check_order.append(preferred)
    check_order += [k for k in sorted(EngineRegistry.keys()) if k != preferred]

    for key in check_order:
        try:
            engine = _discovery._make_engine(key, config)
            if engine.health():
                models = engine.list_models()
                if default_model in models:
                    return CheckResult(
                        "Default model",
                        "ok",
                        f"{default_model} (on {key})",
                    )
        except Exception:
            continue

    return CheckResult(
        "Default model",
        "warn",
        f"{default_model} not found on any engine",
    )


def _check_optional_deps() -> List[CheckResult]:
    """Check availability of optional dependency packages."""
    results: List[CheckResult] = []
    optional_packages = [
        ("fastapi", "openjarvis[server]", "REST API server"),
        ("torch", "pip install torch", "SFT/GRPO training"),
        ("pynvml", "openjarvis[gpu-metrics]", "NVIDIA energy monitoring"),
        ("amdsmi", "openjarvis[energy-amd]", "AMD energy monitoring"),
        ("colbert", "openjarvis[memory-colbert]", "ColBERT memory backend"),
        ("zeus", "openjarvis[energy-apple]", "Apple Silicon energy monitoring"),
    ]
    for pkg, install_hint, description in optional_packages:
        try:
            __import__(pkg)
            results.append(CheckResult(f"Optional: {description}", "ok", "Installed"))
        except Exception:
            results.append(
                CheckResult(
                    f"Optional: {description}",
                    "warn",
                    f"Not installed ({install_hint})",
                )
            )
    return results


def _check_security_profile() -> CheckResult:
    """Check if a security profile is configured."""
    try:
        from openjarvis.core.config import load_config

        config = load_config()
        if config.security.profile:
            return CheckResult(
                name="Security profile",
                status="ok",
                message=f"Profile '{config.security.profile}' active",
            )
        return CheckResult(
            name="Security profile",
            status="warn",
            message="No security profile set",
            details="Recommended: add security.profile = 'personal' to config.toml",
        )
    except Exception as exc:
        return CheckResult(
            name="Security profile",
            status="fail",
            message=f"Could not check: {exc}",
        )


def _check_nodejs() -> CheckResult:
    """Check Node.js version for Node-backed integrations."""
    node_path = shutil.which("node")
    if not node_path:
        return CheckResult(
            "Node.js",
            "warn",
            "Not found",
            details=(
                "Node.js 22+ is required for ClaudeCodeAgent and the "
                "WhatsApp Baileys channel bridge."
            ),
        )
    try:
        result = subprocess.run(
            ["node", "--version"],
            capture_output=True,
            text=True,
            timeout=10,
        )
        version_str = result.stdout.strip()
        # Parse "v22.1.0" -> (22, 1, 0)
        parts = version_str.lstrip("v").split(".")
        major = int(parts[0])
        if major >= 22:
            return CheckResult("Node.js", "ok", version_str)
        return CheckResult(
            "Node.js",
            "warn",
            f"{version_str} (requires >= v22)",
            details=(
                "Upgrade Node.js for ClaudeCodeAgent and WhatsApp Baileys support."
            ),
        )
    except Exception as exc:
        return CheckResult("Node.js", "warn", f"Error checking version: {exc}")


def _run_python_module(
    args: list[str], *, timeout: int = 20
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-m", *args],
        capture_output=True,
        text=True,
        timeout=timeout,
    )


def _check_pip_available() -> CheckResult:
    """Check whether the active venv can run pip."""
    try:
        result = _run_python_module(["pip", "--version"], timeout=10)
    except Exception as exc:
        return CheckResult(
            "Pip",
            "fail",
            f"pip unavailable: {exc}",
            details="Repair with: python -m ensurepip or bundled python -m pip --python .venv\\Scripts\\python.exe install pip.",
        )
    if result.returncode != 0:
        detail = (result.stderr or result.stdout or "pip returned non-zero").strip()
        return CheckResult(
            "Pip",
            "fail",
            "pip unavailable",
            details=detail[:500],
        )
    return CheckResult("Pip", "ok", (result.stdout or "pip available").strip())


def _check_pytest_available() -> CheckResult:
    """Check whether pytest is installed in the active venv."""
    try:
        result = _run_python_module(["pytest", "--version"], timeout=10)
    except Exception as exc:
        return CheckResult(
            "Pytest",
            "fail",
            f"pytest unavailable: {exc}",
            details="Install the dev test runner into the active venv before claiming verification passed.",
        )
    if result.returncode != 0:
        detail = (result.stderr or result.stdout or "pytest returned non-zero").strip()
        return CheckResult("Pytest", "fail", "pytest unavailable", details=detail[:500])
    return CheckResult("Pytest", "ok", (result.stdout or "pytest available").strip())


def _check_venv_integrity() -> CheckResult:
    """Run pip check so broken package metadata is visible in doctor."""
    try:
        result = _run_python_module(["pip", "check"], timeout=30)
    except Exception as exc:
        return CheckResult(
            "Venv integrity",
            "warn",
            f"pip check could not run: {exc}",
            details="This usually means the active .venv is incomplete or pip is missing.",
        )
    output = (result.stdout or result.stderr or "").strip()
    if result.returncode != 0:
        return CheckResult(
            "Venv integrity",
            "warn",
            "pip check failed",
            details=output[:800] or "pip check returned non-zero",
        )
    return CheckResult("Venv integrity", "ok", output or "No broken requirements found")


def _check_runtime_stack() -> CheckResult:
    """Summarize the local Studio runtime stack from the existing health probes."""
    try:
        status = check_runtime_health(timeout_s=0.75)
    except Exception as exc:
        return CheckResult(
            "Jarvis runtime stack", "fail", f"Runtime health failed: {exc}"
        )

    summary = str(status.get("summary") or "Runtime health unavailable")
    required_down = [str(item) for item in status.get("required_down") or []]
    if required_down:
        return CheckResult(
            "Jarvis runtime stack",
            "fail",
            summary,
            details=", ".join(required_down),
        )
    return CheckResult("Jarvis runtime stack", "ok", summary)


def _check_nvidia_memory_clock() -> CheckResult:
    """Warn if NVIDIA memory clock looks above the known-stable RTX 4090 stock range."""
    nvidia_smi = shutil.which("nvidia-smi")
    if not nvidia_smi:
        return CheckResult("NVIDIA GPU", "warn", "nvidia-smi not found")
    try:
        result = subprocess.run(
            [
                nvidia_smi,
                "--query-gpu=name,clocks.mem,memory.used,memory.total",
                "--format=csv,noheader,nounits",
            ],
            capture_output=True,
            text=True,
            timeout=10,
        )
    except Exception as exc:
        return CheckResult("NVIDIA GPU", "warn", f"nvidia-smi failed: {exc}")
    if result.returncode != 0:
        return CheckResult(
            "NVIDIA GPU",
            "warn",
            "nvidia-smi returned non-zero",
            details=result.stderr[:500],
        )
    line = (result.stdout or "").strip().splitlines()[0] if result.stdout else ""
    parts = [part.strip() for part in line.split(",")]
    if len(parts) < 4:
        return CheckResult(
            "NVIDIA GPU",
            "warn",
            "Could not parse nvidia-smi output",
            details=line,
        )
    name, clock_raw, used_raw, total_raw = parts[:4]
    try:
        mem_clock = int(float(clock_raw))
    except ValueError:
        return CheckResult(
            "NVIDIA GPU", "warn", "Could not parse memory clock", details=line
        )
    message = f"{name}: memory clock {mem_clock} MHz, VRAM {used_raw}/{total_raw} MiB"
    if mem_clock > 10600:
        return CheckResult(
            "NVIDIA GPU",
            "warn",
            message,
            details=(
                "4090 memory clock above the known-stable ~10501 MHz stock range; "
                "reset MSI Afterburner memory offset to 0 before benchmarking."
            ),
        )
    return CheckResult("NVIDIA GPU", "ok", message)


_SECRET_KEY_PATTERN = re.compile(
    r"^\s*set\s+\"?([A-Za-z_]*(?:TOKEN|SECRET|PIN|PASSWORD|API_KEY|_KEY)[A-Za-z_]*)=",
    re.IGNORECASE,
)


def _check_plaintext_launcher(launcher: Optional[Path] = None) -> CheckResult:
    """Scan jarvis.bat for secret-looking set lines; report key NAMES only, never values."""
    if launcher is None:
        launcher = Path(r"E:\Claude\OpenJarvis\jarvis.bat")
    if not launcher.exists():
        return CheckResult("Launcher secrets", "ok", "No jarvis.bat found at canonical path")
    try:
        lines = launcher.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError as exc:
        return CheckResult("Launcher secrets", "warn", f"Could not read jarvis.bat: {exc}")
    secret_keys = sorted(
        {m.group(1) for line in lines if (m := _SECRET_KEY_PATTERN.match(line))}
    )
    if secret_keys:
        return CheckResult(
            "Launcher secrets",
            "warn",
            f"jarvis.bat still sets secret-looking keys: {', '.join(secret_keys)}",
            details=(
                "Run scripts\\migrate-jarvis-secrets.ps1 to move them to "
                "%USERPROFILE%\\.openjarvis\\jarvis.env (git-ignored, user-only ACL)."
            ),
        )
    return CheckResult(
        "Launcher secrets",
        "ok",
        "No plaintext secret keys in jarvis.bat (externalized to ~/.openjarvis/jarvis.env)",
    )


# -- Main command -------------------------------------------------------------

_STATUS_ICONS = {
    "ok": "[green]\u2713[/green]",
    "warn": "[yellow]![/yellow]",
    "fail": "[red]\u2717[/red]",
}


def _run_all_checks(*, quick: bool = False) -> List[CheckResult]:
    """Run diagnostic checks and return results."""
    checks: List[CheckResult] = []
    checks.append(_check_python_version())
    checks.append(_check_config_exists())
    checks.append(_check_config_parses())
    if not quick:
        checks.extend(_check_engines())
        checks.extend(_check_models())
        checks.append(_check_default_model())
        checks.extend(_check_optional_deps())
    checks.append(_check_nodejs())
    checks.append(_check_pip_available())
    checks.append(_check_pytest_available())
    checks.append(_check_venv_integrity())
    checks.append(_check_runtime_stack())
    checks.append(_check_nvidia_memory_clock())
    checks.append(_check_plaintext_launcher())
    checks.append(_check_security_profile())
    return checks


def _results_to_dicts(checks: List[CheckResult]) -> List[Dict[str, Any]]:
    """Convert CheckResult list to JSON-serializable dicts."""
    return [asdict(c) for c in checks]


@click.command()
@click.option("--json", "as_json", is_flag=True, help="Output results as JSON.")
@click.option(
    "--quick",
    is_flag=True,
    help="Skip slow engine/model enumeration and run stabilization checks only.",
)
def doctor(as_json: bool, quick: bool) -> None:
    """Run diagnostic checks on your OpenJarvis installation."""
    checks = _run_all_checks(quick=quick)

    if as_json:
        click.echo(json.dumps(_results_to_dicts(checks), indent=2))
        return

    console = Console()
    console.print()
    console.print("[bold]OpenJarvis Doctor[/bold]")
    console.print()

    table = Table(show_header=True, header_style="bold")
    table.add_column("Status", width=3, justify="center")
    table.add_column("Check")
    table.add_column("Result")

    for check in checks:
        icon = _STATUS_ICONS.get(check.status, "?")
        message = check.message
        if check.details:
            message += f"\n  [dim]{check.details}[/dim]"
        table.add_row(icon, check.name, message)

    console.print(table)

    ok_count = sum(1 for c in checks if c.status == "ok")
    warn_count = sum(1 for c in checks if c.status == "warn")
    fail_count = sum(1 for c in checks if c.status == "fail")
    console.print()
    console.print(f"  {ok_count} passed, {warn_count} warnings, {fail_count} failures")
    console.print()

    # Background tasks section
    from openjarvis.cli._bg_state import get_status

    console.print("[bold]Background tasks[/bold]")
    bg = get_status()
    bg_failed = False

    if bg.rust_extension == "ready":
        console.print("  [green]✓[/green] Rust extension: ready")
    elif bg.rust_extension == "failed":
        console.print(f"  [red]✗[/red] Rust extension: failed — {bg.rust_error[:80]}")
        console.print(
            "    retry: ~/.openjarvis/.scripts/install-rust.sh && "
            "~/.openjarvis/.scripts/build-extension.sh"
        )
        bg_failed = True
    else:
        console.print(
            "  [yellow]…[/yellow] Rust extension: building (run in background)"
        )

    if not bg.models:
        console.print("  [dim]no model downloads tracked[/dim]")
    for model_id, state in bg.models.items():
        if state == "ready":
            console.print(f"  [green]✓[/green] {model_id}: ready")
        elif state == "failed":
            console.print(f"  [red]✗[/red] {model_id}: failed")
            console.print(f"    retry: ~/.openjarvis/.scripts/pull-model.sh {model_id}")
            bg_failed = True
        else:
            console.print(f"  [yellow]…[/yellow] {model_id}: downloading")

    if bg_failed:
        raise click.exceptions.Exit(code=1)
