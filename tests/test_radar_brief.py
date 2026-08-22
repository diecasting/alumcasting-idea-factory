"""Tests for the Phase 1.3 Content Opportunity Brief generator.

Deterministic, no AI, no LLM, no network. Verifies audience/intent/angle
detection, core-question normalization, title generation, supporting questions,
outline, priority, JSON/CSV serialization, and end-to-end determinism.
"""

from __future__ import annotations

import csv
import json
import os
import tempfile

from app.radar.brief import (
    ContentBrief,
    core_question,
    detect_angle,
    detect_audience,
    generate_content_brief,
    generate_content_briefs,
    map_search_intent,
    priority_from_score,
    recommended_title,
    suggested_outline,
)
from app.radar.models import NormalizedSignal, SignalType
from app.radar.normalize import normalize_signal
from app.radar.relevance import classify
from app.radar.scoring import load_config, score_signal

# Titles drawn from the real fixtures (generalized by the templates themselves).
POROSITY_TITLE = "Why am I getting porosity in my aluminum die casting?"
CHATTER_TITLE = "Troubleshooting CNC milling chatter and poor surface finish"
ORANGE_PEEL_TITLE = "Powder coating orange peel / rough finish problem"


def _scored(title: str, body: str = "", topic_override=None) -> NormalizedSignal:
    """Build a fully-scored relevant signal from a title (no network)."""
    from app.radar.models import RawSignal

    raw = RawSignal(
        source="reddit",
        source_type="reddit",
        external_id="t1",
        title=title,
        body=body,
        url="https://www.reddit.com/r/example/1",
    )
    n = normalize_signal(raw)
    classify(n)
    if topic_override is not None:
        n.topic = topic_override
    score_signal(n, load_config())
    return n


def _manual(topic, signal_type, title, opportunity_score, is_problem=True) -> NormalizedSignal:
    """Construct a minimal signal by hand (for fallback/edge tests)."""
    n = NormalizedSignal(
        id="manual",
        source="reddit",
        source_type="reddit",
        topic=topic,
        title=title,
        text=title,
        url="https://example.com/x",
    )
    n.signal_type = signal_type
    n.opportunity_score = opportunity_score
    n.is_problem_signal = is_problem
    return n


# --------------------------------------------------------------------------- #
# 1-3. Per-topic brief generation                                            #
# --------------------------------------------------------------------------- #

def test_die_casting_porosity_brief():
    n = _scored(POROSITY_TITLE)
    assert n.is_problem_signal is True
    b = generate_content_brief(n)
    assert isinstance(b, ContentBrief)
    assert b.audience == "quality_engineer"
    assert b.search_intent == "informational"
    assert b.content_angle == "causes_and_solutions"
    assert b.core_question == "Why does porosity occur in aluminum die casting?"
    assert b.recommended_title == "Aluminum Die Casting Porosity: Causes, Prevention and Solutions"
    assert any("porosity" in q.lower() for q in b.supporting_questions)
    assert b.priority in ("high", "medium", "low")  # band depends on relevance


def test_cnc_milling_chatter_brief():
    n = _scored(CHATTER_TITLE)
    assert n.is_problem_signal is True
    b = generate_content_brief(n)
    assert b.audience == "cnc_machinist"
    assert b.search_intent == "troubleshooting"
    assert b.content_angle == "troubleshooting_guide"
    assert b.core_question == "How can CNC milling chatter and poor surface finish be reduced?"
    assert b.recommended_title == "CNC Machining Chatter: Causes, Effects and How to Reduce It"
    assert b.priority in ("high", "medium", "low")  # band depends on relevance
    # outline should be the troubleshooting_guide 8-step list
    assert suggested_outline("troubleshooting_guide") == b.suggested_outline


def test_powder_coating_orange_peel_brief():
    n = _scored(ORANGE_PEEL_TITLE)
    assert n.is_problem_signal is True
    b = generate_content_brief(n)
    assert b.audience == "coating_engineer"
    assert b.content_angle == "defect_prevention"
    assert b.recommended_title == "Powder Coating Orange Peel: Causes, Prevention and Solutions"
    assert any("curing" in q.lower() for q in b.supporting_questions)


# --------------------------------------------------------------------------- #
# 4. Generic manufacturing fallback                                          #
# --------------------------------------------------------------------------- #

def test_generic_manufacturing_fallback():
    n = _manual(None, SignalType.GENERIC, "Something odd happened on the floor", 55.0)
    b = generate_content_brief(n)
    assert b.audience == "general_manufacturing"
    # no topic -> generic subject + fallback questions
    assert "Problem" in b.recommended_title
    assert all(isinstance(q, str) and q for q in b.supporting_questions)


# --------------------------------------------------------------------------- #
# 5. Deterministic title generation                                          #
# --------------------------------------------------------------------------- #

def test_deterministic_recommended_title():
    n = _scored(CHATTER_TITLE)
    assert recommended_title(n, detect_angle(n)) == "CNC Machining Chatter: Causes, Effects and How to Reduce It"
    # material awareness for die casting
    n2 = _scored(POROSITY_TITLE)
    assert recommended_title(n2, detect_angle(n2)) == "Aluminum Die Casting Porosity: Causes, Prevention and Solutions"


# --------------------------------------------------------------------------- #
# 6. Deterministic supporting questions                                      #
# --------------------------------------------------------------------------- #

def test_supporting_questions_reusable_and_topic_specific():
    n = _scored(CHATTER_TITLE)
    qs = generate_content_brief(n).supporting_questions
    assert len(qs) == 6
    assert any("tool overhang" in q.lower() for q in qs)
    assert any("workholding" in q.lower() for q in qs)
    # none should be empty or templated leftovers
    assert all("{" not in q and "}" not in q for q in qs)


# --------------------------------------------------------------------------- #
# 7. Audience classification                                                 #
# --------------------------------------------------------------------------- #

def test_audience_classification():
    assert detect_audience(_manual("cnc_machining", SignalType.TROUBLESHOOTING, "CNC chatter issue", 60)) == "cnc_machinist"
    assert detect_audience(_manual("powder_coating", SignalType.DEFECT_PROBLEM, "orange peel", 60)) == "coating_engineer"
    assert detect_audience(_manual("die_casting", SignalType.DEFECT_PROBLEM, "porosity problem", 60)) == "quality_engineer"
    assert detect_audience(_manual("die_casting", SignalType.PROCESS_PROBLEM, "gating issue", 60)) == "process_engineer"
    assert detect_audience(_manual(None, SignalType.GENERIC, "random thing", 60)) == "general_manufacturing"


# --------------------------------------------------------------------------- #
# 8. Search intent classification                                            #
# --------------------------------------------------------------------------- #

def test_search_intent_classification():
    assert map_search_intent(_manual("x", SignalType.QUESTION, "why?", 60)) == "informational"
    assert map_search_intent(_manual("x", SignalType.TROUBLESHOOTING, "fix it", 60)) == "troubleshooting"
    assert map_search_intent(_manual("x", SignalType.DEFECT_PROBLEM, "defect", 60)) == "troubleshooting"
    assert map_search_intent(_manual("x", SignalType.PROCESS_PROBLEM, "process", 60)) == "troubleshooting"
    assert map_search_intent(_manual("x", SignalType.COMPARISON, "a vs b", 60)) == "comparison"


# --------------------------------------------------------------------------- #
# 9. Priority thresholds                                                     #
# --------------------------------------------------------------------------- #

def test_priority_thresholds():
    assert priority_from_score(60) == "high"
    assert priority_from_score(50) == "high"
    assert priority_from_score(49.9) == "medium"
    assert priority_from_score(30) == "medium"
    assert priority_from_score(29.9) == "low"
    assert priority_from_score(0) == "low"


def test_priority_thresholds_configurable():
    cfg = {"priority": {"high_threshold": 70, "medium_threshold": 40}}
    assert priority_from_score(65, cfg) == "medium"
    assert priority_from_score(75, cfg) == "high"


# --------------------------------------------------------------------------- #
# 10. Outline generation                                                     #
# --------------------------------------------------------------------------- #

def test_outline_generation():
    assert len(suggested_outline("causes_and_solutions")) == 9
    assert len(suggested_outline("troubleshooting_guide")) == 8
    assert len(suggested_outline("defect_prevention")) == 9
    # unknown angle falls back to causes_and_solutions
    assert suggested_outline("nonsense") == suggested_outline("causes_and_solutions")


# --------------------------------------------------------------------------- #
# 11. JSON serialization                                                      #
# --------------------------------------------------------------------------- #

def test_brief_json_serialization():
    n = _scored(POROSITY_TITLE)
    b = generate_content_brief(n)
    d = b.to_dict()
    for key in (
        "problem",
        "topic",
        "signal_type",
        "audience",
        "search_intent",
        "recommended_title",
        "core_question",
        "supporting_questions",
        "content_angle",
        "suggested_outline",
        "priority",
        "source_signal",
    ):
        assert key in d
    # source_signal carries the originating signal reference
    assert d["source_signal"]["title"] == POROSITY_TITLE


def test_pipeline_json_contains_content_brief():
    from app.radar.pipeline import run_pipeline

    with tempfile.TemporaryDirectory() as tmp:
        report = run_pipeline(out_dir=tmp, dry_run=True)
        data = json.load(open(os.path.join(tmp, "reports", "content_opportunity_report.json")))
        briefs = [s for s in data["signals"] if s.get("content_brief")]
        assert len(briefs) == report.total_briefs
        assert report.total_briefs >= 1
        b0 = briefs[0]["content_brief"]
        assert b0["audience"] and b0["recommended_title"] and b0["core_question"]
        # With the real fixture bodies, the CNC chatter brief scores >= 50 and is high.
        chatter = next(
            (b for b in briefs if "CNC Machining Chatter" in b["content_brief"]["recommended_title"]),
            None,
        )
        assert chatter is not None
        assert chatter["content_brief"]["priority"] == "high"


# --------------------------------------------------------------------------- #
# 12. CSV serialization                                                       #
# --------------------------------------------------------------------------- #

def test_csv_serialization():
    from app.radar.pipeline import run_pipeline

    with tempfile.TemporaryDirectory() as tmp:
        run_pipeline(out_dir=tmp, dry_run=True)
        csv_path = os.path.join(tmp, "reports", "content_opportunity_report.csv")
        with open(csv_path, encoding="utf-8") as f:
            reader = csv.DictReader(f)
            header = reader.fieldnames
            for col in (
                "audience",
                "search_intent",
                "recommended_title",
                "core_question",
                "content_angle",
                "priority_band",
            ):
                assert col in header
            rows = list(reader)
        brief_rows = [r for r in rows if r["audience"]]
        assert brief_rows
        # arrays serialized via '; ' (existing project convention)
        assert "; " in brief_rows[0]["supporting_questions"]
        assert "; " in brief_rows[0]["suggested_outline"]


# --------------------------------------------------------------------------- #
# 13. Briefs attached only to problem signals                                #
# --------------------------------------------------------------------------- #

def test_briefs_only_for_problem_signals():
    n = _scored(POROSITY_TITLE)  # problem signal
    other = _manual("x", SignalType.NEWS, "market outlook", 10.0, is_problem=False)
    sigs = [n, other]
    generate_content_briefs(sigs)
    assert n.content_brief is not None
    assert other.content_brief is None


# --------------------------------------------------------------------------- #
# 16. Determinism                                                             #
# --------------------------------------------------------------------------- #

def test_determinism_same_input_twice():
    n1 = _scored(POROSITY_TITLE)
    n2 = _scored(POROSITY_TITLE)
    b1 = generate_content_brief(n1).to_dict()
    b2 = generate_content_brief(n2).to_dict()
    assert b1 == b2


def test_determinism_pipeline_twice():
    from app.radar.pipeline import run_pipeline

    def _collect():
        with tempfile.TemporaryDirectory() as tmp:
            run_pipeline(out_dir=tmp, dry_run=True)
            data = json.load(open(os.path.join(tmp, "reports", "content_opportunity_report.json")))
            return [(s["content_brief"] or {}) for s in data["signals"]]

    a = _collect()
    b = _collect()
    assert a == b
