"""Free RSS / Atom feed collector (stdlib-only, no extra dependencies).

Parses RSS 2.0 (``<item>``) and Atom (``<entry>``) with xml.etree. Dates are
parsed from RFC-822 (RSS) and ISO-8601 (Atom). HTML in descriptions/summaries
is stripped. A source fetch/parse failure returns an empty list so one bad feed
never breaks the whole radar run.
"""

from __future__ import annotations

import re
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from html import unescape
from typing import Optional

from app.radar.models import RawSignal
from app.radar.sources.base import SourceAdapter, DEFAULT_UA

_ATOM = "{http://www.w3.org/2005/Atom}"


def _strip_html(text: str) -> str:
    if not text:
        return ""
    cleaned = re.sub(r"<[^>]+>", " ", text)
    cleaned = unescape(cleaned)
    return re.sub(r"\s+", " ", cleaned).strip()


def _parse_date(value: Optional[str]) -> Optional[datetime]:
    if not value:
        return None
    s = value.strip()
    # RFC-822 (RSS pubDate)
    try:
        dt = parsedate_to_datetime(s)
        if dt is not None:
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return dt
    except Exception:
        pass
    # ISO-8601 (Atom updated/published)
    try:
        iso = s.replace("Z", "+00:00")
        dt = datetime.fromisoformat(iso)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt
    except Exception:
        return None


def _text(el) -> str:
    if el is None or el.text is None:
        return ""
    return el.text.strip()


def _atom_link(entry) -> str:
    best = None
    for link in entry.findall(f"{_ATOM}link"):
        href = link.get("href")
        rel = link.get("rel")
        if href and rel == "alternate":
            return href
        if href and best is None:
            best = href
    return best or ""


class RSSSource(SourceAdapter):
    source_type = "rss"

    def __init__(
        self,
        feed_url: str,
        name: Optional[str] = None,
        transport=None,
        user_agent: str = DEFAULT_UA,
    ) -> None:
        super().__init__(
            name=name or f"rss:{feed_url}",
            transport=transport,
            user_agent=user_agent,
        )
        self.feed_url = feed_url

    def collect(self) -> list[RawSignal]:
        try:
            content = self.transport(self.feed_url)
        except Exception:
            return []
        return self.parse_feed(content)

    def parse_feed(self, content: str) -> list[RawSignal]:
        """Parse an RSS or Atom feed string into RawSignals."""
        out: list[RawSignal] = []
        try:
            root = ET.fromstring(content)
        except Exception:
            return out

        # RSS 2.0
        items = root.findall(".//item")
        if items:
            for it in items:
                out.append(
                    self._make(
                        title=_text(it.find("title")),
                        body=_strip_html(_text(it.find("description"))),
                        link=_text(it.find("link")),
                        guid=_text(it.find("guid")) or _text(it.find("link")),
                        pub=_text(it.find("pubDate")),
                    )
                )
            return out

        # Atom
        entries = root.findall(f".//{_ATOM}entry")
        if entries:
            for e in entries:
                summary = _text(e.find(f"{_ATOM}summary")) or _text(
                    e.find(f"{_ATOM}content")
                )
                out.append(
                    self._make(
                        title=_text(e.find(f"{_ATOM}title")),
                        body=_strip_html(summary),
                        link=_atom_link(e),
                        guid=_text(e.find(f"{_ATOM}id")) or _atom_link(e),
                        pub=_text(e.find(f"{_ATOM}updated"))
                        or _text(e.find(f"{_ATOM}published")),
                    )
                )
            return out

        return out

    def _make(self, title, body, link, guid, pub) -> RawSignal:
        return RawSignal(
            source=self.name,
            source_type=self.source_type,
            external_id=guid or "",
            title=title,
            body=body,
            url=link or "",
            author=None,
            published_at=_parse_date(pub),
            collected_at=self._now(),
            raw={},
        )
