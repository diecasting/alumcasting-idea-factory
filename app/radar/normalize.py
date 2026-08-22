"""Normalization: clean raw text and assign a stable signal id.

Normalization is pure and deterministic -- no network, no randomness. The id is
derived from the canonical URL (or source+external_id fallback) so the same
signal always hashes the same, which makes deduplication stable across runs.
"""

from __future__ import annotations

import hashlib
import re
from datetime import datetime, timezone
from html import unescape

from app.radar.models import NormalizedSignal, RawSignal

_TAG_RE = re.compile(r"<[^>]+>")
_WS_RE = re.compile(r"\s+")


def _clean(text: str) -> str:
    if not text:
        return ""
    text = _TAG_RE.sub(" ", text)
    text = unescape(text)
    return _WS_RE.sub(" ", text).strip()


def _signal_id(raw: RawSignal) -> str:
    basis = raw.url or f"{raw.source}:{raw.external_id}" or raw.title
    return hashlib.sha256(basis.encode("utf-8")).hexdigest()[:16]


def normalize_signal(raw: RawSignal) -> NormalizedSignal:
    title = _clean(raw.title)
    body = _clean(raw.body)
    text = f"{title}. {body}".strip()
    return NormalizedSignal(
        id=_signal_id(raw),
        source=raw.source,
        source_type=raw.source_type,
        topic=None,
        title=title,
        text=text,
        url=raw.url,
        author=raw.author,
        published_at=raw.published_at,
        collected_at=raw.collected_at or datetime.now(timezone.utc),
        engagement=raw.engagement,
    )


def normalize_many(raws) -> list[NormalizedSignal]:
    return [normalize_signal(r) for r in raws]
