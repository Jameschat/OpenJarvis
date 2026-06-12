"""Tests for eval-gated lane promotion (Phase 7 #3)."""

from __future__ import annotations

import pytest

from openjarvis.tools import lane_promotion
from openjarvis.tools.lane_promotion import ProbeResult
from openjarvis.tools.qwen_eval import CaseResult, EvalReport


def _report(passed: int, total: int) -> EvalReport:
    results = [
        CaseResult(f"c{i}", "general", i < passed, "ok" if i < passed else "wrong")
        for i in range(total)
    ]
    return EvalReport(results)


class TestLooksGarbled:
    def test_slash_run_is_garbled(self):
        assert lane_promotion.looks_garbled("////////////////")

    def test_digit_run_is_garbled(self):
        assert lane_promotion.looks_garbled("3333333333333333333")

    def test_empty_is_garbled(self):
        assert lane_promotion.looks_garbled("")
        assert lane_promotion.looks_garbled("   ")

    def test_normal_answer_is_fine(self):
        assert not lane_promotion.looks_garbled("size-ok")
        assert not lane_promotion.looks_garbled(
            "The capital of France is Paris. Let me explain why."
        )

    def test_mostly_one_char_is_garbled(self):
        assert lane_promotion.looks_garbled("3 3 3 3 3 3 3 3 3 3 3 3 3 3")


class TestProbeLongPrompts:
    def test_all_sizes_ok(self, monkeypatch):
        monkeypatch.setattr(
            lane_promotion, "_chat_completion",
            lambda base_url, model, system, user, **kw: "size-ok",
        )
        probes = lane_promotion.probe_long_prompts("http://x:1/v1", "qwen")
        assert [p.words for p in probes] == [110, 300, 700]
        assert all(p.ok for p in probes)

    def test_garbage_output_fails_probe(self, monkeypatch):
        monkeypatch.setattr(
            lane_promotion, "_chat_completion",
            lambda base_url, model, system, user, **kw: "////////////",
        )
        probes = lane_promotion.probe_long_prompts("http://x:1/v1", "qwen")
        assert not any(p.ok for p in probes)

    def test_http_error_fails_probe_not_run(self, monkeypatch):
        def _boom(*a, **k):
            raise OSError("connection refused")

        monkeypatch.setattr(lane_promotion, "_chat_completion", _boom)
        probes = lane_promotion.probe_long_prompts("http://x:1/v1", "qwen")
        assert len(probes) == 3
        assert not any(p.ok for p in probes)
        assert "connection refused" in probes[0].error


class TestGateCandidate:
    OK_PROBES = [ProbeResult(words=w, ok=True, content="size-ok") for w in (110, 300, 700)]

    def test_promotes_when_probes_pass_and_eval_not_worse(self):
        v = lane_promotion.gate_candidate(self.OK_PROBES, _report(6, 6), _report(6, 6))
        assert v.promote
        assert v.reasons

    def test_rejects_on_any_probe_failure(self):
        probes = list(self.OK_PROBES)
        probes[2] = ProbeResult(words=700, ok=False, content="////", error=None)
        v = lane_promotion.gate_candidate(probes, _report(6, 6), _report(6, 6))
        assert not v.promote
        assert any("700" in r for r in v.reasons)

    def test_rejects_when_eval_regresses(self):
        v = lane_promotion.gate_candidate(self.OK_PROBES, _report(4, 6), _report(6, 6))
        assert not v.promote
        assert any("pass rate" in r.lower() for r in v.reasons)

    def test_rejects_below_min_pass_rate_even_if_beats_incumbent(self):
        v = lane_promotion.gate_candidate(
            self.OK_PROBES, _report(3, 6), _report(2, 6), min_pass_rate=0.9
        )
        assert not v.promote

    def test_never_auto_promotes_without_probes(self):
        v = lane_promotion.gate_candidate([], _report(6, 6), _report(6, 6))
        assert not v.promote


class TestReport:
    def test_markdown_report_contains_verdict_and_numbers(self):
        v = lane_promotion.gate_candidate(
            TestGateCandidate.OK_PROBES, _report(6, 6), _report(5, 6)
        )
        md = lane_promotion.format_promotion_report(
            v,
            candidate_label="beellama-v0.4.0",
            incumbent_label="wsl-mtp-8084",
            candidate_report=_report(6, 6),
            incumbent_report=_report(5, 6),
            probes=TestGateCandidate.OK_PROBES,
        )
        assert "PROMOTE" in md
        assert "beellama-v0.4.0" in md
        assert "100%" in md
        assert "size-ok" in md
        # v1 never flips routing itself
        assert "manual" in md.lower() or "operator" in md.lower()
