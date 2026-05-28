"""OpenJarvis - modular AI assistant backend with composable intelligence primitives."""

from __future__ import annotations

from importlib.metadata import PackageNotFoundError
from importlib.metadata import version as _pkg_version

try:
    __version__ = _pkg_version("openjarvis")
except PackageNotFoundError:  # pragma: no cover - uninstalled source tree
    __version__ = "0.0.0+unknown"

__all__ = ["Jarvis", "JarvisSystem", "MemoryHandle", "SystemBuilder", "__version__"]


def __getattr__(name: str):
    if name in {"Jarvis", "JarvisSystem", "MemoryHandle", "SystemBuilder"}:
        from openjarvis.sdk import Jarvis, JarvisSystem, MemoryHandle, SystemBuilder

        values = {
            "Jarvis": Jarvis,
            "JarvisSystem": JarvisSystem,
            "MemoryHandle": MemoryHandle,
            "SystemBuilder": SystemBuilder,
        }
        return values[name]
    raise AttributeError(f"module 'openjarvis' has no attribute {name!r}")
