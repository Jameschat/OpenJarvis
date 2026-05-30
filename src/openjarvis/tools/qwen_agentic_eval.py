"""Verify-graded agentic eval suite — the real Qwen accuracy gate.

Unlike the single-shot `qwen_eval` (does Qwen's *text* contain the right words),
this measures whether Qwen can produce code that ACTUALLY RUNS. Each case is a
tiny project fixture (starter files + a declared check). Qwen proposes file
contents; the proposal is executed in a disposable sandbox via the v2 machinery
(`qwen_sandbox` / `qwen_verify_loop`); "pass" = the check exits clean.

Run it two ways to measure the autonomy loop's lift:
  * ``max_revise=0`` — first-pass accuracy (one proposal, no self-correction).
  * ``max_revise=N`` — accuracy after the execute->observe->fix loop.

The core (``run_agentic_eval``) injects ``propose`` and ``run_check``, so it's
fully testable OFFLINE with a fake proposer and the real sandbox (py_compile /
pytest are deterministic — no model required to test the harness).
"""

from __future__ import annotations

import json
import shutil
import tempfile
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from openjarvis.tools import qwen_sandbox, qwen_verify_loop
from openjarvis.tools.qwen_eval import CaseResult, EvalReport, format_report_markdown

Files = list[dict[str, Any]]
Propose = Callable[[str, Path], Files | None]
RunCheck = Callable[[Any, Files], dict[str, Any]]


@dataclass
class AgenticCase:
    id: str
    prompt: str
    category: str = "code"
    fixture: dict[str, Any] = field(default_factory=dict)  # {"files": {rel: content}, "verify": "argv line"}


def setup_case_project(case: AgenticCase, tmp_root: str | Path) -> Path:
    """Materialise a case fixture into a temp project: starter files + the
    declared ``.jarvis-verify.txt`` check."""
    proj = Path(tmp_root) / "project"
    proj.mkdir(parents=True, exist_ok=True)
    for rel, content in (case.fixture.get("files") or {}).items():
        target = proj / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(str(content), encoding="utf-8")
    verify = case.fixture.get("verify")
    if verify:
        (proj / ".jarvis-verify.txt").write_text(str(verify) + "\n", encoding="utf-8")
    return proj


def read_project_files(proj: Path, *, max_files: int = 20, max_chars: int = 4000) -> str:
    """A compact rendering of the project's current files, to show Qwen what it
    is working with (excludes the verify declaration itself)."""
    chunks: list[str] = []
    for p in sorted(proj.rglob("*")):
        if len(chunks) >= max_files:
            break
        if not p.is_file() or p.name == ".jarvis-verify.txt":
            continue
        rel = p.relative_to(proj).as_posix()
        try:
            body = p.read_text(encoding="utf-8", errors="replace")[:max_chars]
        except OSError:
            continue
        chunks.append(f"--- {rel} ---\n{body}")
    return "\n\n".join(chunks)


def run_agentic_eval(
    cases: list[AgenticCase],
    *,
    propose: Propose,
    run_check: RunCheck = qwen_sandbox.run_check_in_sandbox,
    max_revise: int = 0,
    clock: Callable[[], float] = time.monotonic,
    label: str = "qwen-agentic",
) -> EvalReport:
    results: list[CaseResult] = []
    for case in cases:
        t0 = clock()
        tmp = Path(tempfile.mkdtemp(prefix="jarvis-agentic-"))
        try:
            proj = setup_case_project(case, tmp)
            files = propose(case.prompt, proj)
            if not files:
                results.append(
                    CaseResult(case.id, case.category, False, "no proposal produced", "", clock() - t0)
                )
                continue

            if max_revise > 0:
                _final, verdict, _rounds = qwen_verify_loop.verify_and_revise(
                    files,
                    proj,
                    redraft_proposal=lambda fb, _p=proj: propose(fb, _p),
                    run_check=run_check,
                    max_rounds=max_revise,
                )
                passed = bool(verdict.get("verified"))
                detail = f"verified={passed} rounds={verdict.get('rounds', 0)}"
            else:
                res = run_check(proj, files)
                passed = bool(res.get("ok") and res.get("passed"))
                if passed:
                    detail = "passed first try"
                elif not res.get("ok"):
                    detail = f"unverifiable: {res.get('error', 'n/a')}"
                else:
                    detail = f"exit={res.get('exit_code')} {(res.get('stderr') or '')[:80]}"
            results.append(CaseResult(case.id, case.category, passed, detail, "", clock() - t0))
        finally:
            shutil.rmtree(tmp, ignore_errors=True)
    return EvalReport(results=results, label=label)


def load_agentic_cases(path: str | Path) -> list[AgenticCase]:
    data = json.loads(Path(path).read_text(encoding="utf-8-sig"))
    cases = data["cases"] if isinstance(data, dict) else data
    out: list[AgenticCase] = []
    for raw in cases:
        out.append(
            AgenticCase(
                id=str(raw["id"]),
                prompt=str(raw["prompt"]),
                category=str(raw.get("category", "code")),
                fixture=raw.get("fixture", {}) or {},
            )
        )
    return out


def default_propose(prompt: str, proj: Path, *, model: str = "qwen3.6-27b-local", timeout: int = 180) -> Files | None:
    """Real Qwen proposer: show the project, ask for a repo_patch_proposal, parse
    the proposed files. CLI-only (lazy imports)."""
    import os

    from openai import OpenAI

    from openjarvis.tools import qwen_tool_bridge

    listing = read_project_files(proj)
    sys_prompt = (
        "You are a precise local coding agent. You are given a project's files and a task. "
        "Return EXACTLY one fenced block named qwen_tool_requests containing JSON with a single "
        "repo_patch_proposal request whose args.files is a list of {path, content} with the FULL "
        "corrected file content. Output only that block."
    )
    user = (
        f"TASK:\n{prompt}\n\nCURRENT PROJECT FILES:\n{listing}\n\n"
        'Respond with: {"requests":[{"id":"r1","tool":"repo_patch_proposal",'
        '"args":{"rationale":"...","files":[{"path":"...","content":"..."}]}}]}'
    )
    base_url = os.environ.get("OPENAI_BASE_URL", "http://localhost:4000")
    client = OpenAI(base_url=base_url, api_key=os.environ.get("OPENAI_API_KEY", "sk-noop"))
    resp = client.chat.completions.create(
        model=model,
        messages=[{"role": "system", "content": sys_prompt}, {"role": "user", "content": user}],
        max_tokens=1500,
        timeout=timeout,
        extra_body={"chat_template_kwargs": {"enable_thinking": False}},
    )
    if not resp.choices:
        return None
    msg = resp.choices[0].message
    content = (msg.content or getattr(msg, "reasoning_content", "") or "").strip()
    for req in qwen_tool_bridge.parse_tool_requests(content):
        if req.get("tool") == "repo_patch_proposal":
            files = (req.get("args") or {}).get("files")
            if isinstance(files, list) and files:
                return files
    return None


def main(argv: list[str] | None = None) -> int:
    import argparse
    from datetime import datetime, timezone

    parser = argparse.ArgumentParser(prog="qwen-agentic-eval", description="Verify-graded agentic Qwen eval.")
    parser.add_argument(
        "--cases",
        default=str(Path(__file__).resolve().parents[3] / "evals" / "qwen" / "agentic-cases.json"),
    )
    parser.add_argument("--model", default="qwen3.6-27b-local")
    parser.add_argument("--revise", type=int, default=3, help="self-correction rounds for the second pass")
    parser.add_argument("--out", default="")
    args = parser.parse_args(argv)

    cases = load_agentic_cases(args.cases)

    def propose(p, proj):
        return default_propose(p, proj, model=args.model)

    first = run_agentic_eval(cases, propose=propose, max_revise=0, label="agentic-first-pass")
    looped = run_agentic_eval(cases, propose=propose, max_revise=args.revise, label="agentic-with-self-correction")

    when = datetime.now(timezone.utc).isoformat(timespec="seconds")
    md = (
        format_report_markdown(first, when=when)
        + "\n"
        + format_report_markdown(looped, when=when)
        + f"\n**Self-correction lift: {first.pass_rate:.0%} -> {looped.pass_rate:.0%}**\n"
    )
    print(md)
    if args.out:
        Path(args.out).write_text(md, encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
