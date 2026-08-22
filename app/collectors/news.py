"""News / RSS collector (skeleton — NOT IMPLEMENTED in Phase 0).

Live collection requires a news/RSS source and possibly an API key
(NEWS_API_KEY), injected via GitLab CI/CD variables in a later phase.
This stub defines the interface.
"""
from __future__ import annotations

from typing import List

from app.models import Signal


class NewsCollector:
    """Collects manufacturing-related news/articles via RSS or a news API.

    Phase 0: ``collect()`` is intentionally not wired to any API.
    """

    def __init__(self, keywords: List[str] | None = None):
        self.keywords = keywords or []

    def collect(self) -> List[dict]:
        # TODO(Phase 1): implement via RSS / News API (CI/CD variables).
        raise NotImplementedError(
            "NewsCollector.collect() is not implemented until Phase 1. "
            "Credentials will be injected via GitLab CI/CD variables."
        )
