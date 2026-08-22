"""Reddit public-data collector (read-only, no auth, no posting).

Uses Reddit's public JSON endpoints (https://www.reddit.com/r/<sub>/<sort>.json).
This is public, unauthenticated data collection only -- no account, no OAuth,
no posting, no automation beyond a polite rate-respecting fetch.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Optional

from app.radar.models import RawSignal
from app.radar.sources.base import SourceAdapter, DEFAULT_UA


class RedditSource(SourceAdapter):
    source_type = "reddit"

    def __init__(
        self,
        subreddit: str,
        sort: str = "hot",
        limit: int = 25,
        transport=None,
        user_agent: str = DEFAULT_UA,
    ) -> None:
        super().__init__(
            name=f"reddit:r/{subreddit}",
            transport=transport,
            user_agent=user_agent,
        )
        self.subreddit = subreddit
        self.sort = sort
        self.limit = limit

    def _url(self) -> str:
        return (
            f"https://www.reddit.com/r/{self.subreddit}/{self.sort}.json"
            f"?limit={self.limit}&raw_json=1"
        )

    def collect(self) -> list[RawSignal]:
        try:
            payload = self.transport(self._url())
        except Exception:
            # Network/transport failure: skip this source, keep the run green.
            return []
        return self.parse_json(payload)

    def parse_json(self, payload: str) -> list[RawSignal]:
        """Parse a Reddit listing JSON string into RawSignals. Testable without
        network by passing a fixture body."""
        try:
            data = json.loads(payload)
        except Exception:
            return []
        children = data.get("data", {}).get("children", [])
        out: list[RawSignal] = []
        for child in children:
            d = child.get("data", {})
            title = d.get("title") or ""
            selftext = d.get("selftext") or ""
            permalink = d.get("permalink") or ""
            ext_id = d.get("id") or ""
            author = d.get("author")
            created = d.get("created_utc")
            published = None
            if created:
                try:
                    published = datetime.fromtimestamp(float(created), tz=timezone.utc)
                except Exception:
                    published = None
            out.append(
                RawSignal(
                    source=self.name,
                    source_type=self.source_type,
                    external_id=ext_id,
                    title=title,
                    body=selftext,
                    url=f"https://www.reddit.com{permalink}" if permalink else "",
                    author=author,
                    published_at=published,
                    collected_at=self._now(),
                    raw=d,
                )
            )
        return out
