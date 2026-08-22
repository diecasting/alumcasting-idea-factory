"""Scoring framework for opportunity prioritization.

Phase 0: weights and priority mapping are centralized here — single source
of truth. Actual production scoring (real signals, AI) is deferred to later
phases. All sub-scores are expected on a 0-100 scale so the weighted sum
lands on a 0-100 scale.
"""
from __future__ import annotations

from app.models import Opportunity

# Central scoring weights — do NOT scatter these across files.
WEIGHTS = {
    "discussion": 0.25,        # Discussion Volume
    "engagement": 0.20,        # Engagement
    "problem_intent": 0.20,    # Problem Intent
    "freshness": 0.15,         # Freshness
    "commercial_intent": 0.10, # Commercial Intent
    "content_gap": 0.10,       # Content Gap
}

MIN_SCORE = 0.0
MAX_SCORE = 100.0

# Priority buckets.
P0_MIN = 80
P1_MIN = 60
P2_MIN = 40


def compute_scores(
    discussion_score: float,
    engagement_score: float,
    problem_intent: float,
    freshness: float,
    commercial_intent: float,
    content_gap: float,
) -> float:
    """Compute the weighted opportunity score (0-100)."""
    raw = (
        discussion_score * WEIGHTS["discussion"]
        + engagement_score * WEIGHTS["engagement"]
        + problem_intent * WEIGHTS["problem_intent"]
        + freshness * WEIGHTS["freshness"]
        + commercial_intent * WEIGHTS["commercial_intent"]
        + content_gap * WEIGHTS["content_gap"]
    )
    return round(max(MIN_SCORE, min(MAX_SCORE, raw)), 2)


def map_priority(score: float) -> str:
    """Map an opportunity score to a priority bucket."""
    if score >= P0_MIN:
        return "P0"   # 80-100
    if score >= P1_MIN:
        return "P1"   # 60-79
    if score >= P2_MIN:
        return "P2"   # 40-59
    return "P3"       # 0-39


def build_opportunity(topic: str, **kwargs) -> Opportunity:
    """Assemble an ``Opportunity`` from sub-scores (Phase 0: no AI)."""
    opportunity_score = compute_scores(
        kwargs.get("discussion_score", 0.0),
        kwargs.get("engagement_score", 0.0),
        kwargs.get("problem_intent", 0.0),
        kwargs.get("freshness_score", 0.0),
        kwargs.get("commercial_intent", 0.0),
        kwargs.get("content_gap_score", 0.0),
    )
    return Opportunity(
        topic=topic,
        source_signals=kwargs.get("source_signals", []),
        problem_statement=  kwargs.get("problem_statement", ""),
        problem_intent=kwargs.get("problem_intent", 0.0),
        commercial_intent=kwargs.get("commercial_intent", 0.0),
        discussion_score=kwargs.get("discussion_score", 0.0),
        engagement_score=kwargs.get("engagement_score", 0.0),
        freshness_score=kwargs.get("freshness_score", 0.0),
        content_gap_score=kwargs.get("content_gap_score", 0.0),
        opportunity_score=opportunity_score,
        priority=map_priority(opportunity_score),
        recommended_article=kwargs.get("recommended_article", ""),
    )
