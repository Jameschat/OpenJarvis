from openjarvis.server.qwen_timings import summarize_qwen_timings


def test_summarize_extracts_decode_and_prefill_speed():
    raw = {
        "prompt_n": 18, "prompt_per_second": 105.5,
        "predicted_n": 7, "predicted_per_second": 30.06,
        "draft_n": 9, "draft_n_accepted": 3,
    }
    out = summarize_qwen_timings(raw)
    assert out["decode_tok_s"] == 30.1          # rounded 1dp
    assert out["prefill_tok_s"] == 105.5
    assert out["predicted_n"] == 7
    assert out["accept_rate"] == 0.33           # 3/9 rounded 2dp


def test_summarize_handles_no_draft_tokens():
    # Non-speculative decode: draft_n == 0 must not divide-by-zero.
    out = summarize_qwen_timings({"predicted_per_second": 40.0, "draft_n": 0, "draft_n_accepted": 0})
    assert out["decode_tok_s"] == 40.0
    assert out["accept_rate"] is None


def test_summarize_none_or_empty_returns_empty_dict():
    assert summarize_qwen_timings(None) == {}
    assert summarize_qwen_timings({}) == {}
