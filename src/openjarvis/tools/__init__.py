"""Tools primitive — tool system with ABC interface and built-in tools."""

from __future__ import annotations

from openjarvis.tools._stubs import BaseTool, ToolExecutor, ToolSpec

# Import built-in tools to trigger @ToolRegistry.register() decorators.
# Each is wrapped in try/except so the package loads even before the
# individual tool modules are created.
try:
    import openjarvis.tools.calculator  # noqa: F401
except ImportError:
    pass

try:
    import openjarvis.tools.think  # noqa: F401
except ImportError:
    pass

try:
    import openjarvis.tools.retrieval  # noqa: F401
except ImportError:
    pass

try:
    import openjarvis.tools.llm_tool  # noqa: F401
except ImportError:
    pass

try:
    import openjarvis.tools.file_read  # noqa: F401
except ImportError:
    pass

try:
    import openjarvis.tools.web_search  # noqa: F401
except ImportError:
    pass

try:
    import openjarvis.tools.code_interpreter  # noqa: F401
except ImportError:
    pass

try:
    import openjarvis.tools.code_interpreter_docker  # noqa: F401
except ImportError:
    pass

try:
    import openjarvis.tools.repl  # noqa: F401
except ImportError:
    pass

try:
    import openjarvis.tools.storage_tools  # noqa: F401
except ImportError:
    pass

try:
    import openjarvis.tools.mcp_adapter  # noqa: F401
except ImportError:
    pass

try:
    import openjarvis.tools.channel_tools  # noqa: F401
except ImportError:
    pass

try:
    import openjarvis.tools.http_request  # noqa: F401
except ImportError:
    pass

try:
    import openjarvis.tools.docker_shell_exec  # noqa: F401
    import openjarvis.tools.shell_exec  # noqa: F401
except ImportError:
    pass

try:
    import openjarvis.tools.app_launcher  # noqa: F401
except ImportError:
    pass

try:
    import openjarvis.tools.music_control  # noqa: F401
except ImportError:
    pass

try:
    import openjarvis.tools.weather  # noqa: F401
except ImportError:
    pass

try:
    import openjarvis.tools.crypto  # noqa: F401
except ImportError:
    pass

try:
    import openjarvis.tools.macro  # noqa: F401
except ImportError:
    pass

try:
    import openjarvis.tools.hue_lights  # noqa: F401
except ImportError:
    pass

try:
    import openjarvis.tools.sonos  # noqa: F401
except ImportError:
    pass

try:
    import openjarvis.tools.lutron  # noqa: F401
except ImportError:
    pass

try:
    import openjarvis.tools.calendar_tool  # noqa: F401
except ImportError:
    pass

try:
    import openjarvis.tools.memory_manage  # noqa: F401
except ImportError:
    pass
try:
    import openjarvis.tools.user_profile_manage  # noqa: F401
except ImportError:
    pass

try:
    import openjarvis.tools.skill_manage  # noqa: F401
except ImportError:
    pass

try:
    import openjarvis.tools.file_write  # noqa: F401
except ImportError:
    pass

try:
    import openjarvis.tools.apply_patch  # noqa: F401
except ImportError:
    pass

try:
    import openjarvis.tools.git_tool  # noqa: F401
except ImportError:
    pass

try:
    import openjarvis.tools.db_query  # noqa: F401
except ImportError:
    pass

try:
    import openjarvis.tools.pdf_tool  # noqa: F401
except ImportError:
    pass

try:
    import openjarvis.tools.image_tool  # noqa: F401
except ImportError:
    pass

try:
    import openjarvis.tools.audio_tool  # noqa: F401
except ImportError:
    pass

try:
    import openjarvis.tools.knowledge_tools  # noqa: F401
except ImportError:
    pass

try:
    import openjarvis.tools.text_to_speech  # noqa: F401
except ImportError:
    pass

try:
    import openjarvis.tools.digest_collect  # noqa: F401
except ImportError:
    pass

# Browser tools (Playwright-backed). Auto-importing here registers them in
# ToolRegistry so agent_runner-dispatched agents can use them. Explicitly
# excluded from the conversational tool_use.py whitelist — see the comment
# block around _AGNO_TOOL_WHITELIST — so the live LLM brain still cannot
# pick them autonomously mid-conversation. Side-effecting use only via
# explicit "spin up a browser_pilot to..." agent dispatch.
try:
    import openjarvis.tools.browser  # noqa: F401
except ImportError:
    pass

try:
    import openjarvis.tools.browser_axtree  # noqa: F401
except ImportError:
    pass

# YouTube briefing tool — fetches a transcript via youtube-transcript-api
# and writes a structured briefing to Brain/Knowledge/. Used by the
# browser-pilot agent for "watch this video and brief me" tasks.
# Excluded from the conversational tool whitelist same as browser_*.
try:
    import openjarvis.tools.youtube_brief  # noqa: F401
except ImportError:
    pass

__all__ = ["BaseTool", "ToolExecutor", "ToolSpec"]
