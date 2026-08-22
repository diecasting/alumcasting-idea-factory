"""Deduplicate normalized signals.

Phase 0 skeleton. The strategy is a simple (source, source_id) dedup.
Future phases will add content-hash / fuzzy matching.
"""
from __future__ import annotations

from typing import List

from app.models import Signal


def deduplicate(signals: List[Signal]) -> List[  Signal]:   # noqa: E201
    """Drop signals sharing the same (source, source_id) pair."""
    seen = set()
    unique: List[Signal] = []
    for sig in signals:
        key = (sig.source, sig.source_id)
        if key in seen:
            continue
        seen.add(key)
        unique.append(sig)
    return unique
