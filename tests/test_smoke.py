"""Phase 0 smoke tests. Must pass with NO API keys or secrets."""
import sys
from datetime import datetime
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app.models import Signal, Opportunity  # noqa: E402
from app.processing.deduplicate import deduplicate  # noqa: E402
from app.processing.normalize import normalize, normalize_many  # noqa: E402
from app.processing.scoring import compute_scores, map_priority, build_opportunity  # noqa: E402


def test_project_imports():
    import app
    import app.pipeline
    import app.collectors.reddit
    import app.collectors.news
    import app.ai.topic_generator
    assert app is not None


def test_keyword_config_loads():
    keywords_path = ROOT / "config" / "keywords.yml"
    assert keywords_path.exists(), "config/keywords.yml missing"
    data = yaml.safe_load(keywords_path.read_text(encoding="utf-8"))
    required = {
        "die_casting",
        "casting",
        "cnc",
        "powder_coating",
        "manufacturing_problems",
        "commercial_intent",
    }
    assert required.issubset(set(data.keys()))
    for key in required:
        assert isinstance(data[key], list) and len(data[key]) > 0


def test_signal_schema():
    sig = Signal(
        source="reddit",
        source_id="abc123",
        url="https://example.com/thread",
        title="HPDC porosity issue",
        text="body",
        author="user",
        published_at=datetime(2026, 1, 1),
        engagement=10,
        keyword_matches=["porosity"],
        category="manufacturing_problems",
    )
    assert sig.source == "reddit"
    assert sig.source_id == "abc123"
    assert sig.keyword_matches == ["porosity"]
    assert sig.category == "manufacturing_problems"
    sig.validate()  # should not raise


def test_opportunity_schema():
    opp = Opportunity(topic="porosity in HPDC")
    assert opp.topic == "porosity in HPDC"
    fields = {
        "topic",
        "source_signals",
        "problem_statement",
        "problem_intent",
        "commercial_intent",
        "discussion_score",
        "engagement_score",
        "freshness_score",
        "content_gap_score",
        "opportunity_score",
        "priority",
        "recommended_article",
    }
    assert fields.issubset(set(opp.__dataclass_fields__.keys()))


def test_scoring_boundaries():
    # All zero
    assert compute_scores(0, 0, 0, 0, 0, 0) == 0.0
    # All max
    assert compute_scores(100, 100, 100, 100, 100, 100) == 100.0
    # Mixed: 80*.25 + 60*.20 + 70*.20 + 50*.15 + 40*.10 + 30*.10
    #      = 20 + 12 + 14 + 7.5 + 4 + 3 = 60.5
    assert compute_scores(80, 60, 70, 50, 40, 30) == 60.5


def test_priority_mapping():
    assert map_priority(100) == "P0"
    assert map_priority(80) == "P0"
    assert map_priority(79.9) == "P1"
    assert map_priority(60) == "P1"
    assert map_priority(59.9) == "P2"
    assert map_priority(40) == "P2"
    assert map_priority(39.9) == "P3"
    assert map_priority(0) == "P3"


def test_deduplicate():
    s1 = Signal(source="reddit", source_id="x", url="", title="", text="",
                author="", published_at=datetime.min, engagement=0)
    s2 = Signal(source="reddit", source_id="x", url="", title="", text="",
                author="", published_at=datetime.min, engagement=0)
    s3 = Signal(source="news", source_id="y", url="", title="", text="",
                author="", published_at=datetime.min, engagement=0)
    assert len(deduplicate([s1, s2, s3])) == 2


def test_build_opportunity_priority():
    opp = build_opportunity("porosity", discussion_score=80, engagement_score=60,
                            problem_intent=70, freshness_score=50,
                            commercial_intent=40, content_gap_score=30)
    assert opp.opportunity_score == 60.5
    assert opp.priority == "P1"
