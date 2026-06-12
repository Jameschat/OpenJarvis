"""Eval-gated lane promotion (Phase 7 #3).

One command answers "is this candidate Qwen lane safe to promote?" by
running the two checks every regression of the past month would have
needed: the long-prompt correctness probe (the ``size-ok`` test that
catches ////-style garbage — the DFlash failure mode) and the qwen_eval
accuracy suite, compared against the incumbent lane.

v1 deliberately does NOT flip LiteLLM routing. It produces a verdict +
markdown report; the operator (or a later --apply step, once trusted)
edits ``configs/litellm.yaml`` and restarts. A lane must pass BOTH gates:

  1. long-prompt probe: 110/300/700-word contexts all answer ``size-ok``
     with non-garbled output;
  2. eval pass rate >= incumbent's (and >= --min-pass-rate if given).

Usage::

    python -m openjarvis.tools.lane_promotion \
        --candidate-url http://127.0.0.1:8082/v1 --candidate-model qwen \
        --incumbent-url http://127.0.0.1:8084/v1 --incumbent-model qwen
"""

from __future__ import annotations

import argparse
import json
import re
import time
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, List, Optional

from openjarvis.tools.qwen_eval import (
    EvalCase,
    EvalReport,
    load_cases,
    run_eval,
)

_DEFAULT_CASES = Path(__file__).resolve().parents[3] / "evals" / "qwen" / "cases.json"
_PROBE_SIZES = (110, 300, 700)
_FILLER_LINE = "Jarvis Studio context line for testing purposes here. "

# A run of one repeated character this long marks output as garbage
# (the //// and 3333 GPU-corruption signatures).
_RUN_RE = re.compile(r"(.)\1{11,}")


@dataclass
class ProbeResult:
    words: int
    ok: bool
    content: str = ""
    error: Optional[str] = None


@dataclass
class PromotionVerdict:
    promote: bool
    reasons: List[str] = field(default_factory=list)


def looks_garbled(text: str) -> bool:
    """True for the known bad-lane signatures: empty output, a long run of
    one repeated character, or output dominated by a single character."""
    stripped = (text or "").strip()
    if not stripped:
        return True
    if _RUN_RE.search(stripped):
        return True
    if len(stripped) >= 20:
        compact = stripped.replace(" ", "").replace("\n", "")
        if compact and max(compact.count(c) for c in set(compact)) / len(compact) > 0.6:
            return True
    return False


def _chat_completion(
    base_url: str,
    model: str,
    system: str,
    user: str,
    *,
    max_tokens: int = 700,
    timeout: int = 180,
) -> str:
    """Minimal OpenAI-compatible chat call via urllib (no client dep)."""
    body = json.dumps(
        {
            "model": model,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            "max_tokens": max_tokens,
            "chat_template_kwargs": {"enable_thinking": False},
        }
    ).encode("utf-8")
    req = urllib.request.Request(
        base_url.rstrip("/") + "/chat/completions",
        data=body,
        headers={"Content-Type": "application/json", "Authorization": "Bearer sk-noop"},
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        data = json.loads(resp.read().decode("utf-8"))
    choices = data.get("choices") or []
    if not choices:
        return ""
    msg = choices[0].get("message") or {}
    return (msg.get("content") or msg.get("reasoning_content") or "").strip()


def probe_long_prompts(
    base_url: str,
    model: str,
    sizes: tuple = _PROBE_SIZES,
    timeout: int = 180,
) -> List[ProbeResult]:
    """Port of dist/size-test.sh, lane-agnostic: long system contexts must
    still produce the exact short answer. Garbled or failing output at any
    size fails that probe; all sizes are always attempted."""
    out: List[ProbeResult] = []
    for words in sizes:
        filler = _FILLER_LINE * words
        try:
            content = _chat_completion(
                base_url,
                model,
                filler,
                "Reply with exactly: size-ok",
                max_tokens=32,
                timeout=timeout,
            )
        except Exception as exc:  # noqa: BLE001 - a failing probe is a result
            out.append(ProbeResult(words=words, ok=False, error=str(exc)))
            continue
        ok = ("size-ok" in content) and not looks_garbled(content)
        out.append(ProbeResult(words=words, ok=ok, content=content[:80]))
    return out


def evaluate_lane(
    base_url: str,
    model: str,
    cases: Optional[List[EvalCase]] = None,
    *,
    timeout: int = 120,
    label: str = "lane",
) -> EvalReport:
    """Run the qwen_eval case set directly against a lane URL."""
    if cases is None:
        cases = load_cases(_DEFAULT_CASES)

    def _run(case: EvalCase) -> str:
        return _chat_completion(
            base_url,
            model,
            "You are a precise local assistant. Answer the task directly.",
            case.prompt,
            timeout=timeout,
        )

    return run_eval(cases, run_task=_run, label=label)


def gate_candidate(
    probes: List[ProbeResult],
    candidate_report: EvalReport,
    incumbent_report: EvalReport,
    *,
    min_pass_rate: float = 0.0,
) -> PromotionVerdict:
    """Pure promotion decision. Both gates must pass; no probes = no promotion."""
    reasons: List[str] = []
    promote = True

    if not probes:
        return PromotionVerdict(False, ["no long-prompt probes were run — refusing to promote blind"])

    failed = [p for p in probes if not p.ok]
    if failed:
        promote = False
        for p in failed:
            detail = p.error or f"output {p.content!r}"
            reasons.append(f"long-prompt probe FAILED at {p.words} words: {detail}")
    else:
        reasons.append(f"long-prompt probes passed at {', '.join(str(p.words) for p in probes)} words")

    cand, inc = candidate_report.pass_rate, incumbent_report.pass_rate
    if cand < inc:
        promote = False
        reasons.append(
            f"eval pass rate regressed: candidate {cand:.0%} < incumbent {inc:.0%}"
        )
    else:
        reasons.append(f"eval pass rate {cand:.0%} >= incumbent {inc:.0%}")

    if cand < min_pass_rate:
        promote = False
        reasons.append(f"eval pass rate {cand:.0%} below required bar {min_pass_rate:.0%}")

    return PromotionVerdict(promote, reasons)


def format_promotion_report(
    verdict: PromotionVerdict,
    *,
    candidate_label: str,
    incumbent_label: str,
    candidate_report: EvalReport,
    incumbent_report: EvalReport,
    probes: List[ProbeResult],
) -> str:
    lines = [
        "# Lane promotion gate",
        "",
        f"Candidate: **{candidate_label}** vs incumbent **{incumbent_label}**",
        "",
        f"## Verdict: {'PROMOTE' if verdict.promote else 'REJECT'}",
        "",
    ]
    lines += [f"- {r}" for r in verdict.reasons]
    lines += [
        "",
        "## Eval",
        "",
        f"- candidate: {candidate_report.passed}/{candidate_report.total} = {candidate_report.pass_rate:.0%}",
        f"- incumbent: {incumbent_report.passed}/{incumbent_report.total} = {incumbent_report.pass_rate:.0%}",
        "",
        "## Long-prompt probes (candidate)",
        "",
    ]
    for p in probes:
        status = "ok" if p.ok else f"FAIL ({p.error or p.content!r})"
        lines.append(f"- {p.words} words: {status} => {p.content!r}")
    lines += [
        "",
        "## Next step",
        "",
        (
            "Routing is NOT changed by this tool (v1). On PROMOTE, the operator "
            "manually updates configs/litellm.yaml and restarts LiteLLM; on REJECT, "
            "keep the incumbent and fix the candidate first."
        ),
        "",
    ]
    return "\n".join(lines)


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Eval-gated Qwen lane promotion (v1: verdict only)")
    parser.add_argument("--candidate-url", required=True)
    parser.add_argument("--candidate-model", default="qwen")
    parser.add_argument("--candidate-label", default=None)
    parser.add_argument("--incumbent-url", default="http://127.0.0.1:8084/v1")
    parser.add_argument("--incumbent-model", default="qwen")
    parser.add_argument("--incumbent-label", default=None)
    parser.add_argument("--cases", default=str(_DEFAULT_CASES))
    parser.add_argument("--min-pass-rate", type=float, default=0.0)
    parser.add_argument("--out", default=None, help="report path (default dist/lane-promotion-<stamp>.md)")
    args = parser.parse_args(argv)

    cases = load_cases(args.cases)
    cand_label = args.candidate_label or args.candidate_url
    inc_label = args.incumbent_label or args.incumbent_url

    print(f"[1/3] long-prompt probes against candidate {cand_label} ...")
    probes = probe_long_prompts(args.candidate_url, args.candidate_model)
    for p in probes:
        print(f"  {p.words} words: {'ok' if p.ok else 'FAIL'} {p.error or p.content!r}")

    print(f"[2/3] eval suite against candidate {cand_label} ...")
    cand_report = evaluate_lane(args.candidate_url, args.candidate_model, cases, label="candidate")
    print(f"  candidate: {cand_report.passed}/{cand_report.total}")

    print(f"[3/3] eval suite against incumbent {inc_label} ...")
    inc_report = evaluate_lane(args.incumbent_url, args.incumbent_model, cases, label="incumbent")
    print(f"  incumbent: {inc_report.passed}/{inc_report.total}")

    verdict = gate_candidate(probes, cand_report, inc_report, min_pass_rate=args.min_pass_rate)
    md = format_promotion_report(
        verdict,
        candidate_label=cand_label,
        incumbent_label=inc_label,
        candidate_report=cand_report,
        incumbent_report=inc_report,
        probes=probes,
    )
    out = Path(args.out) if args.out else (
        Path(__file__).resolve().parents[3]
        / "dist"
        / f"lane-promotion-{time.strftime('%Y%m%d-%H%M%S')}.md"
    )
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(md, encoding="utf-8")
    print(f"\nVerdict: {'PROMOTE' if verdict.promote else 'REJECT'} — report: {out}")
    return 0 if verdict.promote else 1


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
