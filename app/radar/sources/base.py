"""Base adapter interface for all radar sources.

A source collects raw signals from one place (a subreddit, an RSS feed, ...).
Collection is transport-injected so tests and the dry-run pipeline can feed
fixtures instead of hitting the network.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from datetime import datetime, timezone
from typing import Callable, Optional

from app.radar.models import RawSignal

# Descriptive User-Agent (Reddit requires one; RFC-compliant contact included).
DEFAULT_UA = (
    "alumcasting-idea-factory/1.0 "
    "(Manufacturing Content Opportunity Radar; +https://github.com/diecasting/alumcasting-idea-factory)"
)

# A transport fetches a URL and returns its text body.
Transport = Callable[[str], str]


def default_transport(url: str, user_agent: str = DEFAULT_UA, timeout: int = 20) -> str:
    """Stdlib HTTP fetch with a descriptive User-Agent. Raises on failure so
    callers can decide to skip the source gracefully."""
    import urllib.request

    req = urllib.request.Request(url, headers={"User-Agent": user_agent})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        charset = resp.headers.get_content_charset() or "utf-8"
        return resp.read().decode(charset, errors="replace")


class SourceAdapter(ABC):
    """Unified adapter interface. Subclasses implement ``collect`` (or override
    ``parse`` and reuse the base ``collect``)."""

    source_type: str = "base"

    def __init__(
        self,
        name: str,
        transport: Optional[Transport] = None,
        user_agent: str = DEFAULT_UA,
    ) -> None:
        self.name = name
        self.user_agent = user_agent
        self.transport = transport or (lambda u: default_transport(u, self.user_agent))

    @abstractmethod
    def collect(self) -> list[RawSignal]:
        """Return raw signals from this source. Implementations should catch
        transport/parse errors and return an empty list rather than raise."""
        raise NotImplementedError

    def _now(self) -> datetime:
        return datetime.now(timezone.utc)
