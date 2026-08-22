"""Phase 1.2: Problem Signal Quality & Opportunity Ranking tests.

Deterministic, explainable scoring -- no AI, no LLM, no embeddings, no paid
API. These tests pin down the behaviour required by the Phase 1.2 spec and
preserve the earlier relevance regression guarantees.
"""

from datetime import datetime, timezone, timedelta

from app.radar.models import NormalizedSignal, Priority, RawSignal
from app.radar.normalize import normalize_signal
from app.radar.relevance import classify, is_relevant
from app.radar.scoring import (
    compute_heat_score,
    compute_opportunity_score,
    load_config,
    rank_opportunities,
    score_signal,
)

NOW = datetime(2026, 8, 22, tzinfo=timezone.utc)
CFG = load_config()


def _signal(title, body="", engagement=0, published_at=None, url="https://x.com/1"):
    raw = RawSignal(
        source="reddit:test",
        source_type="reddit",
        external_id=url,
        title=title,
        body=body,
        url=url,
        engagement=engagement,
        published_at=published_at,
    )
    n = normalize_signal(raw)
    classify(n)
    return n


def _scored(title, body="", engagement=0, published_at=None, url="https://x.com/1"):
    n = _signal(title, body=body, engagement=engagement, published_at=published_at, url=url)
    score_signal(n, CFG, now=NOW)
    return n


# --- Config sanity: the shipped TOML is valid and drives the numbers ----------
def test_config_loads_and_has_expected_weights():
    assert CFG["scoring"]["problem_threshold"] == 60
    assert CFG["scoring"]["question_weight"] == 20
    assert CFG["scoring"]["troubleshooting_weight"] == 20
    assert CFG["scoring"]["defect_weight"] == 20
    assert CFG["scoring"]["technical_specificity_weight"] == 15
    assert CFG["scoring"]["engagement_weight"] == 15
    assert CFG["scoring"]["freshness_weight"] == 10
    # weights sum to 100
    w = CFG["scoring"]
    assert (
        w["question_weight"]
        + w["troubleshooting_weight"]
        + w["defect_weight"]
        + w["technical_specificity_weight"]
        + w["engagement_weight"]
        + w["freshness_weight"]
        == 100
    )
    o = CFG["opportunity"]
    assert o["problem_weight"] + o["heat_weight"] + o["relevance_weight"] == 100


# --- Score boundaries ---------------------------------------------------------
def test_problem_score_within_bounds():
    for title in ("a", "why is this broken", "porosity in die casting", "market report on casting"):
        n = _scored(title)
        assert 0.0 <= n.problem_score <= 100.0


def test_opportunity_score_within_bounds():
    for title in ("a", "why is this broken", "porosity in die casting", "market report on casting"):
        n = _scored(title)
        assert 0.0 <= n.opportunity_score <= 100.0


def test_known_signal_scores_respect_bounds():
    # Explicit checks on extreme inputs.
    n_zero = _scored("completely unrelated cat video title here")
    # It may not be relevant; scores still bounded.
    assert 0.0 <= n_zero.problem_score <= 100.0
    assert 0.0 <= n_zero.opportunity_score <= 100.0


# --- problem_threshold behaviour ----------------------------------------------
def test_threshold_marks_high_problem_signal():
    n = _scored("Why am I getting porosity in my aluminum die casting?")
    assert n.problem_score >= CFG["scoring"]["problem_threshold"]
    assert n.is_problem_signal is True


def test_threshold_rejects_low_problem_signal():
    n = _scored("Global die casting market forecast 2030")
    assert n.problem_score < CFG["scoring"]["problem_threshold"]
    assert n.is_problem_signal is False


# --- Spec example signals -----------------------------------------------------
def test_porosity_question_is_problem_signal():
    n = _scored("Why am I getting porosity in my aluminum die casting?")
    assert n.topic in ("die_casting", "casting")
    assert n.is_problem_signal is True
    assert n.problem_score >= 60


def test_cnc_chatter_is_problem_signal():
    n = _scored("CNC machining chatter: causes and solutions")
    assert n.topic == "cnc_machining"
    assert n.is_problem_signal is True
    assert n.problem_score >= 60


def test_powder_coating_peeling_is_problem_signal():
    n = _scored("Powder coating peeling from aluminum")
    assert n.topic == "powder_coating"
    assert n.is_problem_signal is True
    assert n.problem_score >= 60


def test_market_forecast_is_low_problem_score():
    n = _scored("Global die casting market forecast 2030")
    # Even if relevance keeps it, it must not look like a technical problem.
    assert n.problem_score < 60


def test_laptop_question_is_not_a_problem_signal_and_not_cnc():
    n = _scored("How do I fix my laptop not turning on?", body="it just won't power on")
    # Relevance drops it (topic is None); even if scored directly it is not CNC
    # and not a manufacturing problem signal.
    assert n.topic != "cnc_machining"
    assert n.topic is None
    assert n.is_problem_signal is False
    assert "turning" not in (n.matched_keywords or [])


# --- Score reasons populated --------------------------------------------------
def test_score_reasons_populated_for_problem_signal():
    n = _scored("Why am I getting porosity in my aluminum die casting?")
    assert isinstance(n.score_reasons, list)
    assert len(n.score_reasons) > 0
    joined = " | ".join(n.score_reasons).lower()
    assert "question" in joined
    assert "defect" in joined or "failure" in joined


def test_score_reasons_explainable_no_ai_language():
    import re

    n = _scored("Powder coating peeling from aluminum")
    joined = " | ".join(n.score_reasons).lower()
    # No real AI phrasing -- word-boundary checks so "failure" (contains "ai")
    # does not false-positive.
    assert not re.search(r"\bai\b", joined)
    assert not re.search(r"\bllm\b", joined)
    assert "model" not in joined
    assert "predicts" not in joined
    assert "thinks" not in joined


# --- Engagement / heat --------------------------------------------------------
def test_heat_zero_with_no_engagement():
    assert compute_heat_score(0, CFG) == 0.0
    assert compute_heat_score(-5, CFG) == 0.0


def test_heat_saturates_with_high_engagement():
    low = compute_heat_score(50, CFG)
    high = compute_heat_score(1000, CFG)
    assert 0.0 < low <= high <= 100.0
    assert high > low


def test_engagement_boosts_problem_score():
    base = _scored("Why am I getting porosity in my aluminum die casting?")
    engaged = _scored(
        "Why am I getting porosity in my aluminum die casting?", engagement=500
    )
    assert engaged.problem_score >= base.problem_score
    assert engaged.heat_score > 0


# --- Freshness ----------------------------------------------------------------
def test_fresh_signal_full_freshness():
    fresh = _scored("Porosity in die casting", published_at=NOW - timedelta(days=1))
    reasons = " | ".join(fresh.score_reasons).lower()
    assert "fresh" in reasons


def test_stale_signal_loses_freshness():
    stale = _scored("Porosity in die casting", published_at=NOW - timedelta(days=120))
    reasons = " | ".join(stale.score_reasons).lower()
    assert "stale" in reasons


def test_no_date_treated_as_fresh():
    n = _scored("Porosity in die casting", published_at=None)
    assert any("fresh" in r.lower() for r in n.score_reasons)


# --- Ranking ------------------------------------------------------------------
def _ranked_signals(titles):
    sigs = [_scored(t) for t in titles]
    rank_opportunities(sigs)
    return sigs


def test_rank_only_assigned_to_problem_signals():
    sigs = _ranked_signals(
        [
            "Why am I getting porosity in my aluminum die casting?",
            "Global die casting market forecast 2030",  # not a problem signal
            "CNC machining chatter: causes and solutions",
        ]
    )
    problems = [s for s in sigs if s.is_problem_signal]
    non_problems = [s for s in sigs if not s.is_problem_signal]
    for s in problems:
        assert isinstance(s.opportunity_rank, int)
    for s in non_problems:
        assert s.opportunity_rank is None


def test_ranking_is_deterministic():
    titles = [
        "Why am I getting porosity in my aluminum die casting?",
        "CNC machining chatter: causes and solutions",
        "Powder coating peeling from aluminum",
        "Haas vs Mazak for 5-axis CNC machining center?",  # relevant, not problem
    ]
    run_a = _ranked_signals(titles)
    run_b = _ranked_signals(titles)
    ranks_a = [s.opportunity_rank for s in run_a if s.is_problem_signal]
    ranks_b = [s.opportunity_rank for s in run_b if s.is_problem_signal]
    assert ranks_a == ranks_b
    # Ranks are a contiguous 1..N sequence.
    assert ranks_a == list(range(1, len(ranks_a) + 1))


def test_ranking_orders_by_opportunity_score():
    # Build two problem signals with clearly different opportunity scores by
    # varying engagement (heat) on otherwise identical problem text.
    weak = _scored("Why am I getting porosity in my aluminum die casting?", engagement=0)
    strong = _scored("Why am I getting porosity in my aluminum die casting?", engagement=1000)
    sigs = [weak, strong]
    rank_opportunities(sigs)
    ranked = sorted([s for s in sigs if s.is_problem_signal], key=lambda s: s.opportunity_rank)
    assert ranked[0].opportunity_score >= ranked[1].opportunity_score


def test_ranking_tie_break_by_published_at():
    # Identical text/engagement; newer published_at ranks first.
    old = _scored(
        "Porosity in aluminum die casting troubleshooting",
        engagement=0,
        published_at=NOW - timedelta(days=5),
    )
    new = _scored(
        "Porosity in aluminum die casting troubleshooting",
        engagement=0,
        published_at=NOW - timedelta(days=1),
    )
    sigs = [old, new]
    rank_opportunities(sigs)
    assert new.opportunity_rank is not None
    assert old.opportunity_rank is not None
    # Deterministic: newer must rank at least as high (lower rank number).
    assert new.opportunity_rank <= old.opportunity_rank


# --- Opportunity score composition -------------------------------------------
def test_opportunity_score_uses_configured_weights():
    # With heat=0 and relevance scaled, opportunity = 0.5*problem + 0.25*rel*100.
    problem = 80.0
    heat = 0.0
    relevance = 0.5  # -> 50 on 0-100 scale
    expected = round(
        (
            CFG["opportunity"]["problem_weight"] * problem
            + CFG["opportunity"]["heat_weight"] * heat
            + CFG["opportunity"]["relevance_weight"] * (relevance * 100)
        )
        / 100.0,
        2,
    )
    assert compute_opportunity_score(problem, heat, relevance, CFG) == expected


# --- Regression: earlier relevance guarantees still hold ----------------------
def test_regression_laptop_not_cnc():
    n = classify(
        normalize_signal(
            RawSignal(
                source="s",
                source_type="reddit",
                external_id="1",
                title="How do I fix my laptop not turning on?",
                body="it just won't power on",
                url="https://x.com/1",
            )
        )
    )
    assert n.topic != "cnc_machining"
    assert n.topic is None
    assert not is_relevant(n)


def test_regression_lathe_still_cnc():
    n = _signal("Best CNC lathe for small batch titanium machining?")
    assert n.topic == "cnc_machining"
    assert is_relevant(n)
    assert "lathe" in n.matched_keywords


def test_regression_cast_word_boundary():
    n = _signal("Shrinkage defect in sand cast housing")
    assert n.topic in ("casting", "die_casting")
    assert is_relevant(n)
