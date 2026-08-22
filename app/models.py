"""Unified data models for the Content Opportunity Radar.

Phase 0 defines the schemas only. No external API calls happen here.

Every source (Reddit, News, ...) must be normalized into the single
``Signal`` structure so downstream processing is source-agnostic. Future
``Opportunity`` objects are built from scored signals.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import List


@dataclass
class Signal:
    """A single normalized mention from a public source.

    This is the canonical record produced by collectors + normalizers.
    All processing stages consume ``Signal`` objects, never raw payloads.
    """

    source: str                       # e.g. "reddit", "news"
    source_id: str                    # unique id within the source
    url: str
    title: str
    text: str                         # body / snippet text
    author: str
    published_at: datetime
    engagement: int                   # upvotes / comments / shares (source-specific)
    keyword_matches: List[str] = field(default_factory=list)
    category: str = ""                # mapped from keyword taxonomy

    def validate(self) -> None:
        """Raise if the signal is missing required identity fields."""
        if not self.source:
            raise ValueError("Signal.source is required")
        if not self.source_id:
            raise ValueError("Signal.source_id is required")


@dataclass
class Opportunity:
    """A ranked content opportunity derived from clustered signals."""

    topic: str
    source_signals: List[str] = field(default_factory=list)
    problem_statement: str = ""
    problem_intent: float = 0.0
    commercial_intent: float = 0.0
    discussion_score: float = 0.0
    engagement_score: float = 0.0
    freshness_score: float = 0.0
    content_gap_score: float = 0.0
    opportunity_score: float = 0.0
    priority: str = "P3"
    recommended_article: str = ""
