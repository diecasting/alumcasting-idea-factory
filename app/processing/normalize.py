"""Normalize raw collected items into the unified Signal model.

Phase 0 skeleton: defines the normalization contract used by future
collectors (Reddit, News). No live collection happens here.
"""
from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, List

from app.models import Signal


def normalize(raw: Dict[str, Any]) -> Signal:
    """Flatten a raw source item into a normalized ``Signal``.

    ``published_at`` is accepted as an ISO string and parsed to datetime.
    Missing fields default gracefully so the skeleton never crashes.
    """
    published = raw.get("published_at")
    if isinstance(published, str):
        published = datetime.fromisoformat(published)
    elif published is None:
        published = datetime.min

    return Signal(
        source=raw.get("source", ""),
        source_id=str(raw.get("source_id", "")),
        url=raw.get("url", ""),
        title=raw.get("title", ""),
        text=raw.get("text", ""),
        author=raw.get("author", ""),
        published_at=published,
        engagement=int(raw.get("engagement", 0)),
        keyword_matches=list(raw.get("keyword_matches", [])),
        category=raw.get("category", ""),
    )


def normalize_many(raw_items: List[Dict[str, Any]]) -> List[Signal]:
    """Normalize a list of raw items."""
    return [normalize(item) for item in raw_items]
