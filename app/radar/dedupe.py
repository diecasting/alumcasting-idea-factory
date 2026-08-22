"""Deduplication: drop exact-URL duplicates and near-duplicate titles.

Near-duplicate detection uses Jaccard similarity over token sets (case-folded
alphanumeric tokens) within the same topic. Threshold 0.9 keeps only the first
of two very similar posts.
"""

from __future__ import annotations

import re

from app.radar.models import NormalizedSignal

_TOKEN_RE = re.compile(r"[a-z0-9]+")


def _tokens(text: str) -> set[str]:
    return set(_TOKEN_RE.findall((text or "").lower()))


def _similar(a: str, b: str, threshold: float = 0.9) -> bool:
    ta, tb = _tokens(a), _tokens(b)
    if not ta or not tb:
        return False
    inter = len(ta & tb)
    union = len(ta | tb)
    if union == 0:
        return False
    return (inter / union) >= threshold


def dedupe(signals: list[NormalizedSignal], threshold: float = 0.9) -> list[NormalizedSignal]:
    seen_urls: set[str] = set()
    seen_titles: list[tuple[str, str]] = []  # (topic, title)
    out: list[NormalizedSignal] = []
    for s in signals:
        key = (s.url or "").strip().lower()
        if key:
            if key in seen_urls:
                continue
            seen_urls.add(key)
        dup = False
        for topic, title in seen_titles:
            if s.topic == topic and _similar(s.title, title, threshold):
                dup = True
                break
        if dup:
            continue
        seen_titles.append((s.topic, s.title))
        out.append(s)
    return out
