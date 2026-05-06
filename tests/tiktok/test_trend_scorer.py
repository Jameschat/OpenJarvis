# tests/tiktok/test_trend_scorer.py
import pytest
from unittest.mock import patch

def test_score_high_for_viral_language():
    from openjarvis.tiktok.trend_scorer import score_for_tiktok
    item = {"title": "Why everyone is secretly using Claude AI", "points": 600, "url": ""}
    assert score_for_tiktok(item) >= 70

def test_score_low_for_niche_jargon():
    from openjarvis.tiktok.trend_scorer import score_for_tiktok
    item = {"title": "CUDA kernel optimization for sparse attention tensors", "points": 20, "url": ""}
    assert score_for_tiktok(item) < 50

def test_score_capped_at_100():
    from openjarvis.tiktok.trend_scorer import score_for_tiktok
    item = {"title": "Why everyone actually needs this AI tool explained", "points": 9999, "url": ""}
    assert score_for_tiktok(item) <= 100

def test_fetch_and_score_filters_by_threshold():
    from openjarvis.tiktok.trend_scorer import fetch_and_score
    mock_items = [
        {"title": "Why everyone is using AI now", "points": 800, "url": "http://a.com"},
        {"title": "Obscure kernel patch v2.3.1 released", "points": 5, "url": "http://b.com"},
    ]
    with patch("openjarvis.tiktok.trend_scorer._hn_top", return_value=mock_items), \
         patch("openjarvis.tiktok.trend_scorer._reddit_top", return_value=[]):
        results = fetch_and_score(threshold=70)
    assert all(r["tiktok_score"] >= 70 for r in results)

def test_write_tiktok_trends_creates_file(tmp_path):
    from openjarvis.tiktok.trend_scorer import write_tiktok_trends
    mock_items = [{"title": "How to use AI to make money", "points": 500, "url": "http://a.com", "source": "HN"}]
    with patch("openjarvis.tiktok.trend_scorer._hn_top", return_value=mock_items), \
         patch("openjarvis.tiktok.trend_scorer._reddit_top", return_value=[]), \
         patch.dict("os.environ", {"OPENJARVIS_VAULT_PATH": str(tmp_path)}):
        items, note_path = write_tiktok_trends(threshold=0)
    assert len(items) >= 1
    from pathlib import Path
    assert Path(note_path).exists()
