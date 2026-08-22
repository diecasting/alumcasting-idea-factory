"""AI topic generator (skeleton — NOT IMPLEMENTED in Phase 0).

Generates English content briefs from ranked opportunities. Will use an LLM
(OPENAI_API_KEY) injected via GitLab CI/CD variables in a later phase.
"""
from __future__ import annotations

from typing import List

from app.models import Opportunity


class TopicGenerator:
    """Turns high-priority opportunities into English article briefs.

    Phase 0: ``generate()`` is a placeholder; no model calls are made.
    """

    def __init__(self, model: str = "gpt-4o-mini"):
        self.model = model

    def generate(self, opportunities: List[Opportunity]) -> List[dict]:
        # TODO(Phase 2): implement via LLM with OPENAI_API_KEY from CI/CD vars.
        raise NotImplementedError(
            "TopicGenerator.generate() is not implemented until Phase 2."
        )
