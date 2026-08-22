"""Problem Signal Quality & Opportunity Ranking (Phase 1.2).

Deterministic, explainable, fully testable. No AI, no LLM, no embeddings, no
paid API, no database. Every weight lives in ``config/radar.toml``.

Pipeline position:

    raw signals -> relevant signals -> problem signals -> ranked opportunities

This module consumes already-relevant, de-duplicated ``NormalizedSignal``
objects (produced by relevance.py + dedupe.py) and attaches:

    heat_score         0-100   engagement-derived
    problem_score      0-100   weighted blend of problem-intent sub-scores
    is_problem_signal  bool    problem_score >= problem_threshold
    opportunity_score  0-100   weighted blend of problem/heat/relevance
    opportunity_rank   int|None rank among problem signals only
    score_reasons      list    human-readable explanation of the score

Score explanations come ONLY from the deterministic rules below -- never from
a model.
"""

from __future__ import annotations

import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

try:  # Python 3.11+
    import tomllib
except ModuleNotFoundError:  # pragma: no cover - fallback for <3.11
    import tomli as tomllib  # type: ignore

from app.radar.models import NormalizedSignal

CONFIG_PATH = Path(__file__).resolve().parent.parent.parent / "config" / "radar.toml"

# Embedded fallback so the module works even if the config file is missing.
# The shipped config/radar.toml is the single source of truth for these numbers.
_DEFAULT_CONFIG: dict = {
    "scoring": {
        "problem_threshold": 60,
        "question_weight": 20,
        "troubleshooting_weight": 20,
        "defect_weight": 20,
        "technical_specificity_weight": 15,
        "engagement_weight": 15,
        "freshness_weight": 10,
        "engagement_scale": 50,
        "fresh_days": 7,
        "fresh_decay_days": 30,
        "technical_points_per_term": 12,
    },
    "opportunity": {
        "problem_weight": 50,
        "heat_weight": 25,
        "relevance_weight": 25,
    },
    "problem_indicators": {
        "question": {
            "patterns": [
                r"\?",
                r"\bwhy\b",
                r"\bhow\b",
                r"\bwhat\b",
                r"\bcan.?t\b",
                r"\bwon.?t\b",
                r"\bhow (do|can|to|should)\b",
                r"\bwhat (causes|is causing)\b",
                r"\bdo (i|es)\b",
                r"\bshould i\b",
            ]
        },
        "troubleshooting": {
            "patterns": [
                r"\bproblem\b",
                r"\bissue\b",
                r"\btroubleshoot",
                r"\bnot working\b",
                r"\bdoesn.?t\b",
                r"\bdoes not\b",
                r"\berror\b",
                r"\bstuck\b",
                r"\bfix\b",
                r"\bfixing\b",
                r"\bsolve\b",
                r"\bsolution\b",
                r"\bsolutions\b",
                r"\bcause\b",
                r"\bcauses\b",
                r"\bhelp\b",
                r"\badvice\b",
            ]
        },
        "defect": {
            "patterns": [
                r"\bporosit",
                r"\bvoid",
                r"\bshrinkage",
                r"\bdefect",
                r"\bdefective\b",
                r"\bwarping?\b",
                r"\bflash\b",
                r"\bcold shut",
                r"\binclusion",
                r"\bblister",
                r"\bmisrun",
                r"\bcrack",
                r"\bcracking\b",
                r"\bfail",
                r"\bfailure\b",
                r"\bbroke\b",
                r"\bburn",
                r"\bmelt",
                r"\brupture",
                r"\bchatter",
                r"\bpeeling\b",
                r"\bpeel\b",
                r"\bbubbles?\b",
                r"\borange peel",
                r"\badhesion\b",
                r"\bdimensional\b",
                r"\btoleran",
                r"\bsurface finish",
                r"\bpoor finish",
                r"\broughness\b",
            ]
        },
        "technical": {
            "patterns": [
                r"\balumin(i)?um\b",
                r"\balloy\b",
                r"\bsteel\b",
                r"\btitanium\b",
                r"\bmagnesium\b",
                r"\bzinc\b",
                r"\bcopper\b",
                r"\btemper\b",
                r"\bdie[\s-]?cast",
                r"\bcasting\b",
                r"\binjection\b",
                r"\bmachin",
                r"\bmilling\b",
                r"\blathe\b",
                r"\bcnc\b",
                r"\bcoating\b",
                r"\bcuring\b",
                r"\bg-?code\b",
                r"\bspindle\b",
                r"\bfeed rate\b",
                r"\b5-?axis\b",
                r"\btolerance\b",
                r"\bejector\b",
                r"\bgating\b",
                r"\bsprue\b",
                r"\brunner\b",
                r"\bvent\b",
                r"\bcooling\b",
                r"\bhpdc\b",
                r"\bnd[td]\b",
                r"\bx-?ray\b",
            ]
        },
    },
}

_MIN = 0.0
_MAX = 100.0


def _clamp(value: float) -> float:
    return max(_MIN, min(_MAX, value))


def load_config(path: Optional[str | Path] = None) -> dict:
    """Load scoring configuration. The file overrides the embedded defaults;
    missing keys fall back to defaults so the module is robust."""
    cfg = _DEFAULT_CONFIG
    p = Path(path) if path else CONFIG_PATH
    if p.exists():
        try:
            with open(p, "rb") as f:
                data = tomllib.load(f)
            merged = {**cfg}
            for section, values in data.items():
                if isinstance(values, dict) and section in merged:
                    merged[section] = {**merged[section], **values}
                else:
                    merged[section] = values
            cfg = merged
        except Exception:
            cfg = _DEFAULT_CONFIG
    return cfg


def _compiled(patterns: list[str]) -> list[re.Pattern]:
    return [re.compile(p, re.IGNORECASE) for p in patterns]


def _any_match(text: str, patterns: list[re.Pattern]) -> bool:
    return any(p.search(text) for p in patterns)


def _distinct_terms(text: str, patterns: list[re.Pattern]) -> list[str]:
    found: set[str] = set()
    for p in patterns:
        for m in p.finditer(text):
            found.add(m.group(0).lower())
    return sorted(found)


def compute_heat_score(engagement: int, cfg: dict) -> float:
    """Map raw engagement (upvotes + comments) to a 0-100 saturating curve.

    engagement <= 0 -> 0. Larger engagement approaches 100 asymptotically.
    """
    scale = float(cfg["scoring"]["engagement_scale"])
    if engagement is None or engagement <= 0 or scale <= 0:
        return 0.0
    raw = 100.0 * (1.0 - 1.0 / (1.0 + engagement / scale))
    return round(_clamp(raw), 2)


def compute_freshness(
    published_at: Optional[datetime], cfg: dict, now: Optional[datetime] = None
) -> tuple[float, str]:
    """Freshness sub-score (0..freshness_weight).

    No publication date is treated as fresh (full weight). Signals within
    ``fresh_days`` get full weight; beyond that they decay linearly to 0 over
    ``fresh_decay_days``.
    """
    sc = cfg["scoring"]
    full = float(sc["freshness_weight"])
    fresh_days = float(sc["fresh_days"])
    decay = float(sc["fresh_decay_days"])
    if published_at is None:
        return full, "no publication date (treated as fresh)"
    now = now or datetime.now(timezone.utc)
    if published_at.tzinfo is None:
        published_at = published_at.replace(tzinfo=timezone.utc)
    age_days = (now - published_at).total_seconds() / 86400.0
    if age_days < 0:
        age_days = 0.0
    if age_days <= fresh_days:
        return full, f"published {age_days:.0f}d ago (fresh)"
    if age_days <= fresh_days + decay:
        frac = 1.0 - (age_days - fresh_days) / decay
        return round(full * frac, 2), f"published {age_days:.0f}d ago (aging)"
    return 0.0, f"published {age_days:.0f}d ago (stale)"


def score_signal(
    norm: NormalizedSignal,
    cfg: Optional[dict] = None,
    now: Optional[datetime] = None,
) -> NormalizedSignal:
    """Attach problem quality + opportunity ranking fields to a signal.

    Pure and deterministic for a given (signal, cfg, now). Mutates ``norm``
    in place and returns it.
    """
    cfg = cfg or load_config()
    now = now or datetime.now(timezone.utc)
    text = f"{norm.title} {norm.text}".lower()
    reasons: list[str] = []

    ind = cfg["problem_indicators"]
    q_pat = _compiled(ind["question"]["patterns"])
    t_pat = _compiled(ind["troubleshooting"]["patterns"])
    d_pat = _compiled(ind["defect"]["patterns"])
    tech_pat = _compiled(ind["technical"]["patterns"])

    q_hit = _any_match(text, q_pat)
    t_hit = _any_match(text, t_pat)
    d_hit = _any_match(text, d_pat)
    tech_terms = _distinct_terms(text, tech_pat)

    # A post describing a defect/failure is inherently a problem-seeking
    # context, so a defect hit implies troubleshooting context too.
    troubleshoot_context = t_hit or d_hit

    w = cfg["scoring"]
    problem = 0.0

    if q_hit:
        problem += w["question_weight"]
        reasons.append("question detected")

    if troubleshoot_context:
        problem += w["troubleshooting_weight"]
        reasons.append("troubleshooting language detected")

    if d_hit:
        problem += w["defect_weight"]
        reasons.append("defect/failure term detected")

    tech_points = min(
        w["technical_specificity_weight"],
        len(tech_terms) * w["technical_points_per_term"],
    )
    if tech_terms:
        problem += tech_points
        shown = ", ".join(tech_terms[:6])
        reasons.append(f"technical specificity detected ({shown})")

    heat = compute_heat_score(norm.engagement, cfg)
    if heat > 0:
        eng_points = round(w["engagement_weight"] * heat / 100.0, 2)
        problem += eng_points
        reasons.append(f"engagement: {norm.engagement} (heat {heat:.0f})")
    else:
        reasons.append("no engagement signal")

    fresh, fresh_reason = compute_freshness(norm.published_at, cfg, now)
    problem += fresh
    reasons.append(fresh_reason)

    problem = round(_clamp(problem), 2)

    norm.heat_score = heat
    norm.problem_score = problem
    norm.score_reasons = reasons

    threshold = w["problem_threshold"]
    norm.is_problem_signal = problem >= threshold

    norm.opportunity_score = compute_opportunity_score(
        problem, heat, norm.relevance_score, cfg
    )
    # Rank is assigned later by rank_opportunities (problem signals only).
    norm.opportunity_rank = None
    return norm


def compute_opportunity_score(
    problem_score: float, heat_score: float, relevance_score: float, cfg: dict
) -> float:
    """Weighted blend (0-100) of problem, heat, and relevance.

    relevance_score is stored 0-1 internally, so it is scaled to 0-100 for the
    blend. All weights come from config [opportunity].
    """
    o = cfg["opportunity"]
    rel = _clamp(relevance_score * 100.0)
    raw = (
        o["problem_weight"] * problem_score
        + o["heat_weight"] * heat_score
        + o["relevance_weight"] * rel
    ) / 100.0
    return round(_clamp(raw), 2)


def rank_opportunities(signals: list[NormalizedSignal]) -> list[NormalizedSignal]:
    """Assign deterministic 1..N opportunity_rank to problem signals only.

    Sort key (descending priority):
        1. opportunity_score
        2. problem_score
        3. heat_score
        4. published_at (newest first; undated treated as oldest)
    Non-problem signals keep opportunity_rank = None.
    """
    problems = [s for s in signals if s.is_problem_signal]
    problems.sort(
        key=lambda s: (
            -s.opportunity_score,
            -s.problem_score,
            -s.heat_score,
            -(s.published_at.timestamp() if s.published_at else 0.0),
        )
    )
    for i, s in enumerate(problems, start=1):
        s.opportunity_rank = i
    return signals
