"""Execute-observe-fix verification loop (v2 wired into the autonomy loop).

This is the piece that turns "Qwen wrote code that looks right" into "Qwen ran
it, saw the real error, and fixed it until it passed." It takes the files Qwen
proposed, runs the project's declared check in a disposable sandbox (via
``qwen_sandbox``), and on failure feeds the *actual* stderr/exit code back so
Qwen can produce a corrected proposal — looping until the check passes or the
round budget is spent.

Pure orchestration: ``run_check`` and ``redraft_proposal`` are injected, so the
loop is fully unit-testable without running a model or a subprocess.
"""

from __future__ import annotations

from typing import Any, Callable

# files: list[{"path": str, "content": str}]
Files = list[dict[str, Any]]
RunCheck = Callable[[Any, Files], dict[str, Any]]
RedraftProposal = Callable[[str], Files | None]


def build_verify_feedback(result: dict[str, Any], files: Files, *, base_prompt: str = "") -> str:
    stderr = (result.get("stderr") or "").strip()
    stdout = (result.get("stdout") or "").strip()
    file_list = ", ".join(
        str(f.get("path", "?")) for f in files if isinstance(f, dict)
    ) or "(none)"
    return "\n\n".join(
        part
        for part in (
            base_prompt,
            "Your proposed change FAILED the project's verification check when it was "
            "actually run in a sandbox copy.",
            f"Check command: {result.get('command')}",
            f"Exit code: {result.get('exit_code')}",
            f"Files you proposed: {file_list}",
            "Real check output (fix THIS, do not guess):\n"
            + (stderr or stdout or "(no output captured)"),
            "Return a corrected repo_patch_proposal with the full updated file "
            "contents that makes the check pass. Do not repeat the same mistake.",
        )
        if part
    )


def verify_and_revise(
    files: Files,
    repo_root: Any,
    *,
    redraft_proposal: RedraftProposal,
    run_check: RunCheck,
    max_rounds: int = 3,
    base_prompt: str = "",
) -> tuple[Files, dict[str, Any], list[dict[str, Any]]]:
    """Run the declared check on ``files``; on failure, feed the real error back
    via ``redraft_proposal`` and re-check until pass or ``max_rounds`` spent.

    Returns ``(final_files, verdict, rounds)``. ``verdict`` keys:
      * ``available`` — False if the project has no usable check (no decl / blocked).
      * ``verified``  — True only if the check actually passed.
      * ``rounds``    — revision rounds used.
      * ``exhausted`` — True if budget ran out still failing.
    """
    max_rounds = max(0, int(max_rounds))
    rounds: list[dict[str, Any]] = []
    result = run_check(repo_root, files)

    for attempt in range(max_rounds + 1):
        if not result.get("ok"):
            # Config/safety problem (no .jarvis-verify.txt, blocked exe, etc.):
            # we cannot verify, so report unavailable rather than a false pass.
            return files, {
                "available": False,
                "verified": False,
                "reason": result.get("error") or "verification unavailable",
            }, rounds
        if result.get("passed"):
            return files, {
                "available": True,
                "verified": True,
                "rounds": attempt,
                "exit_code": 0,
            }, rounds
        if attempt >= max_rounds:
            break
        feedback = build_verify_feedback(result, files, base_prompt=base_prompt)
        rounds.append(
            {
                "round": attempt + 1,
                "exit_code": result.get("exit_code"),
                "stderr": (result.get("stderr") or "")[:1000],
            }
        )
        new_files = redraft_proposal(feedback)
        if new_files:
            files = new_files
        result = run_check(repo_root, files)

    return files, {
        "available": True,
        "verified": bool(result.get("passed")),
        "exhausted": True,
        "rounds": max_rounds,
        "exit_code": result.get("exit_code"),
    }, rounds
