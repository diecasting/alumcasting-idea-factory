"""Data models for the Manufacturing Content Opportunity Radar.

Phase 1 only. These models describe the SOURCE -> COLLECT -> NORMALIZE ->
DEDUP -> REPORT pipeline. No LLM, no paid APIs, no external services.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Optional


def _iso(dt: Optional[datetime]) -> Optional[str]:
    """Serialize a datetime to ISO-8601; naive datetimes are treated as UTC."""
    if dt is None:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.isoformat()


class SignalType(str, Enum):
    """Taxonomy of signal types. HIGH-value types map to real problems people
    are trying to solve; LOW-value types are noise we want to demote/drop."""

    QUESTION = "question"
    TROUBLESHOOTING = "troubleshooting"
    FAILURE = "failure"
    COMPARISON = "comparison"
    RECOMMENDATION = "recommendation"
    HOW_TO = "how_to"
    PROCESS_PROBLEM = "process_problem"
    QUALITY_PROBLEM = "quality_problem"
    DEFECT_PROBLEM = "defect_problem"
    MATERIAL_PROBLEM = "material_problem"
    TOOLING_PROBLEM = "tooling_problem"
    SURFACE_PROBLEM = "surface_problem"
    DIMENSIONAL_PROBLEM = "dimensional_problem"
    GENERIC = "generic"
    PROMOTIONAL = "promotional"
    NEWS = "news"
    OTHER = "other"


class Priority(str, Enum):
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"


# Manufacturing topics the radar tracks (ordered for report grouping).
TOPICS = ("die_casting", "casting", "cnc_machining", "powder_coating")


@dataclass
class RawSignal:
    """A signal exactly as collected from a source, before normalization."""

    source: str
    source_type: str
    external_id: str
    title: str
    body: str
    url: str
    author: Optional[str] = None
    published_at: Optional[datetime] = None
    collected_at: Optional[datetime] = None
    engagement: int = 0
    raw: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "source": self.source,
            "source_type": self.source_type,
            "external_id": self.external_id,
            "title": self.title,
            "body": self.body,
            "url": self.url,
            "author": self.author,
            "published_at": _iso(self.published_at),
            "collected_at": _iso(self.collected_at),
            "engagement": self.engagement,
        }


@dataclass
class NormalizedSignal:
    """A cleaned, classified signal ready for relevance filtering and reporting."""

    id: str
    source: str
    source_type: str
    topic: Optional[str]
    title: str
    text: str
    url: str
    author: Optional[str] = None
    published_at: Optional[datetime] = None
    collected_at: Optional[datetime] = None
    signal_type: SignalType = SignalType.OTHER
    priority: Priority = Priority.LOW
    relevance_score: float = 0.0
    matched_keywords: list = field(default_factory=list)
    # Phase 1.2: Problem Signal Quality & Opportunity Ranking fields.
    engagement: int = 0
    heat_score: float = 0.0
    problem_score: float = 0.0
    opportunity_score: float = 0.0
    is_problem_signal: bool = False
    opportunity_rank: Optional[int] = None
    score_reasons: list = field(default_factory=list)
    # Phase 1.3: Content Opportunity Brief (attached only to problem signals).
    content_brief: Any = None
    # Phase 1.5C: First-party GSC search-demand evidence (sibling of content_brief).
    # Attached only to problem signals. None for non-problem signals. Carries a
    # SearchEvidence object (status + matched GSC rows) or None. Never affects
    # any score.
    search_evidence: Any = None

    def to_dict(self) -> dict:
        brief = None
        if getattr(self, "content_brief", None) is not None:
            brief = self.content_brief.to_dict()
        return {
            "id": self.id,
            "source": self.source,
            "source_type": self.source_type,
            "topic": self.topic,
            "title": self.title,
            "text": self.text,
            "url": self.url,
            "author": self.author,
            "published_at": _iso(self.published_at),
            "collected_at": _iso(self.collected_at),
            "signal_type": self.signal_type.value,
            "priority": self.priority.value,
            "relevance_score": round(self.relevance_score, 3),
            "matched_keywords": self.matched_keywords,
            "engagement": self.engagement,
            "heat_score": round(self.heat_score, 2),
            "problem_score": round(self.problem_score, 2),
            "opportunity_score": round(self.opportunity_score, 2),
            "is_problem_signal": self.is_problem_signal,
            "opportunity_rank": self.opportunity_rank,
            "score_reasons": self.score_reasons,
            "content_brief": brief,
            "search_evidence": (
                self.search_evidence.to_dict()
                if getattr(self, "search_evidence", None) is not None
                else None
            ),
        }


@dataclass
class RadarReport:
    """The daily content opportunity report."""

    generated_at: datetime
    total_raw: int = 0
    total_normalized: int = 0
    total_relevant: int = 0
    total_deduped: int = 0
    total_problem: int = 0
    total_briefs: int = 0
    by_topic: dict = field(default_factory=dict)
    signals: list = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "generated_at": _iso(self.generated_at),
            "total_raw": self.total_raw,
            "total_normalized": self.total_normalized,
            "total_relevant": self.total_relevant,
            "total_deduped": self.total_deduped,
            "total_problem": self.total_problem,
            "total_briefs": self.total_briefs,
            "by_topic": self.by_topic,
            "signals": [s.to_dict() for s in self.signals],
        }
