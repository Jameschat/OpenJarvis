from __future__ import annotations


def summarize_qwen_timings(raw: dict | None) -> dict:
    """Map a llama.cpp ``timings`` block to a compact UI telemetry dict.

    Returns {} for missing/empty input so callers can merge unconditionally.
    Keys (all optional): decode_tok_s, prefill_tok_s, predicted_n, accept_rate.
    accept_rate is draft_n_accepted/draft_n (MTP speculative acceptance), or
    None when no draft tokens were proposed (non-speculative decode).
    """
    if not raw:
        return {}
    out: dict = {}
    decode = raw.get("predicted_per_second")
    if isinstance(decode, (int, float)):
        out["decode_tok_s"] = round(float(decode), 1)
    prefill = raw.get("prompt_per_second")
    if isinstance(prefill, (int, float)):
        out["prefill_tok_s"] = round(float(prefill), 1)
    if isinstance(raw.get("predicted_n"), int):
        out["predicted_n"] = raw["predicted_n"]
    draft_n = raw.get("draft_n") or 0
    if draft_n:
        out["accept_rate"] = round((raw.get("draft_n_accepted") or 0) / draft_n, 2)
    else:
        out["accept_rate"] = None
    return out
