"""Reddit collector (skeleton — NOT IMPLEMENTED in Phase 0).

Live collection requires a Reddit OAuth credential
(REDDIT_CLIENT_ID / REDDIT_CLIENT_SECRET), which will be injected via
GitLab CI/CD variables in a later phase. This stub defines the interface.
"""
from __future__ import annotations

from typing import List

from app.models import Signal


class RedditCollector:
    """Collects manufacturing-related discussions from Reddit.

    Phase 0: ``collect()`` is intentionally not wired to any API so the
    pipeline can run end-to-end without secrets.
    """

    def __init__(self, keywords: List[str] | None = None):
        self.keywords = keywords or []

    def collect(self) -> List[dict]:
        # TODO(Phase 1): implement via Reddit OAuth (CI/CD variables).
        raise NotImplementedError(
            "RedditCollector.collect() is not implemented until Phase 1. "
            "Credentials will be injected via GitLab CI/CD variables."
        )
