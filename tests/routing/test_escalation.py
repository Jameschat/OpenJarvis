from openjarvis.learning.routing.escalation import (
    LADDER,
    choose_initial_model,
    initial_target,
    next_target,
    resolve_brain,
    should_escalate,
)


def test_choose_model_stays_local_for_local_preference():
    for tier in ("trivial", "very_complex"):
        assert choose_initial_model("qwen3.6-27b-local", tier, "local", cloud_available=True) == (
            "qwen3.6-27b-local",
            None,
            None,
        )


def test_choose_model_escalates_very_complex_to_cloud_when_available():
    model, frm, reason = choose_initial_model(
        "qwen3.6-27b-local", "very_complex", "balanced", cloud_available=True
    )
    assert model == "gpt-4o"
    assert frm == "qwen3.6-27b-local"
    assert "complexity=very_complex" in reason


def test_choose_model_stays_local_when_no_stronger_target_available():
    assert choose_initial_model(
        "qwen3.6-27b-local", "very_complex", "balanced",
        cloud_available=False, remote_available=False,
    ) == ("qwen3.6-27b-local", None, None)


def test_choose_model_prefers_remote_then_falls_back_to_cloud():
    # best + complex -> target remote; remote up -> remote
    m, _, _ = choose_initial_model("qwen3.6-27b-local", "complex", "best", remote_available=True)
    assert m == "qwen3.6-35b-a3b-remote"
    # remote down, cloud up -> cloud
    m2, _, r2 = choose_initial_model(
        "qwen3.6-27b-local", "complex", "best", remote_available=False, cloud_available=True
    )
    assert m2 == "gpt-4o"
    assert "remote unavailable" in r2


def test_choose_model_no_escalation_for_moderate_balanced():
    assert choose_initial_model("qwen3.6-27b-local", "moderate", "balanced", cloud_available=True) == (
        "qwen3.6-27b-local",
        None,
        None,
    )


def test_resolve_brain_maps_models_to_descriptors():
    assert resolve_brain("qwen3.6-27b-local") == {"brain": "local", "lane": "27B", "model": "qwen3.6-27b-local"}
    assert resolve_brain("qwen3.6-35b-a3b-remote")["brain"] == "remote"
    assert resolve_brain("gpt-4o") == {"brain": "cloud", "lane": "openai", "model": "gpt-4o"}
    assert resolve_brain("claude-opus-4")["lane"] == "anthropic"
    assert resolve_brain("")["brain"] == "local"


def test_local_preference_always_starts_local():
    for tier in ("trivial", "moderate", "very_complex"):
        assert initial_target(tier, "local") == "local_fast"


def test_balanced_starts_local_but_jumps_for_very_complex():
    assert initial_target("moderate", "balanced") == "local_fast"
    assert initial_target("very_complex", "balanced") == "remote"


def test_best_starts_strong_on_hard_prompts():
    assert initial_target("simple", "best") == "local_fast"
    assert initial_target("complex", "best") == "remote"


def test_should_escalate_honours_explicit_flag():
    assert should_escalate(99, True, "local") is True


def test_should_escalate_thresholds_by_preference():
    # balanced threshold 75
    assert should_escalate(80, False, "balanced") is False
    assert should_escalate(70, False, "balanced") is True
    # local is lenient (50)
    assert should_escalate(70, False, "local") is False
    # best is eager (85)
    assert should_escalate(80, False, "best") is True


def test_next_target_walks_the_ladder_then_stops():
    assert next_target("local_fast") == "local_coder"
    assert next_target("remote") == "cloud"
    assert next_target("cloud") is None
    assert next_target("unknown") is None
    assert LADDER[0] == "local_fast"
