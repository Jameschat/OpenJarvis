# tests/tiktok/test_finance.py
import time, pytest

def test_estimate_earnings():
    from openjarvis.tiktok.finance import estimate_earnings_gbp
    assert estimate_earnings_gbp(1000, 7.5) == 7.5
    assert estimate_earnings_gbp(0, 7.5) == 0.0
    assert estimate_earnings_gbp(500000, 7.5) == 3750.0

def test_monthly_summary_empty():
    from openjarvis.tiktok.finance import monthly_summary
    result = monthly_summary([])
    assert result["total_views"] == 0
    assert result["est_earnings_gbp"] == 0.0
    assert result["target_5k_pct"] == 0.0

def test_monthly_summary_only_counts_this_month():
    from openjarvis.tiktok.finance import monthly_summary
    old_post = {"posted_at": 0.0, "views": 100000, "likes": 5000, "comments": 200}
    new_post = {"posted_at": time.time(), "views": 50000, "likes": 2000, "comments": 100}
    result = monthly_summary([old_post, new_post])
    assert result["total_views"] == 50000

def test_monthly_summary_target_progress():
    from openjarvis.tiktok.finance import monthly_summary
    post = {"posted_at": time.time(), "views": 333333, "likes": 1000, "comments": 50}
    result = monthly_summary([post], rpm=7.5)
    assert result["est_earnings_gbp"] == pytest.approx(2499.99, abs=1)
    assert result["target_5k_pct"] == pytest.approx(50.0, abs=1)

def test_per_video_earnings_sorted():
    from openjarvis.tiktok.finance import per_video_earnings
    posts = [
        {"video_id": "a", "title": "low", "views": 1000, "likes": 10},
        {"video_id": "b", "title": "high", "views": 100000, "likes": 5000},
    ]
    result = per_video_earnings(posts)
    assert result[0]["video_id"] == "b"
    assert result[0]["est_earnings_gbp"] == pytest.approx(750.0)
