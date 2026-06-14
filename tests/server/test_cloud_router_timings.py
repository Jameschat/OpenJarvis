import json

import httpx
import pytest

from openjarvis.server import cloud_router


class _FakeStream:
    def __init__(self, lines):
        self._lines = lines

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    def raise_for_status(self):
        pass

    async def aiter_lines(self):
        for ln in self._lines:
            yield ln


@pytest.mark.asyncio
async def test_stream_litellm_local_captures_final_timings(monkeypatch):
    # Two content chunks then a final chunk carrying timings + [DONE].
    lines = [
        "data: " + json.dumps({"choices": [{"delta": {"content": "hi"}}]}),
        "data: " + json.dumps({"choices": [{"delta": {"content": " there"}}]}),
        "data: "
        + json.dumps(
            {
                "choices": [{"delta": {}}],
                "timings": {"predicted_per_second": 42.0, "draft_n": 0, "draft_n_accepted": 0},
            }
        ),
        "data: [DONE]",
    ]

    class _FakeClient:
        def __init__(self, *a, **k):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        def stream(self, *a, **k):
            return _FakeStream(lines)

    monkeypatch.setattr(httpx, "AsyncClient", _FakeClient)

    stats: dict = {}
    tokens = [
        t
        async for t in cloud_router.stream_litellm_local(
            "qwen3.6-27b-local", [], stats=stats
        )
    ]

    assert tokens == ["hi", " there"]
    assert stats["timings"]["predicted_per_second"] == 42.0
