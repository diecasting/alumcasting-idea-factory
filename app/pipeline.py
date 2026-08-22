"""Content Opportunity Radar — pipeline orchestration (Phase 0 skeleton).

Phase 0 scope: wire the processing stages and prove the skeleton runs
end-to-end with NO external API and NO secrets. Live collectors and the
AI topic generator are deferred to later phases.

Intended data flow (full system, built incrementally):

    Sources
      -> Collector
      -> Normalizer
      -> Deduplicator
      -> Problem Detector
      -> Scoring
      -> AI Topic Generator
      -> Opportunity Report
"""
from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

from app.models import Opportunity
from app.processing.deduplicate import deduplicate
from app.processing.normalize import normalize_many
from app.processing.scoring import build_opportunity

REPORTS_DIR = Path(__file__).resolve().parent.parent / "reports"


def collect_signals() -> list:
    """Phase 0: no live sources connected.

    Future phases will instantiate ``RedditCollector`` / ``NewsCollector``
    here and merge their results into a single raw list.
    """
    return []


def detect_problem_signals(signals) -> list:
    """Phase 0 placeholder for Problem Detector stage.

    Real detection (problem-intent classification) arrives in a later phase.
    """
    return signals


def run_pipeline() -> dict:
    """Run the Phase 0 smoke pipeline and write a report artifact.

    Runs without errors using only the local skeleton. Returns a summary
    dict and writes ``reports/pipeline_smoke.json``.
    """
    raw = collect_signals()
    signals = deduplicate(normalize_many(raw))
    signals = detect_problem_signals(signals)

    # Phase 0: no real signals -> no ranked opportunities yet.
    opportunities: list[Opportunity] = []

    report = {
        "generated_at": datetime.now(datetime.UTC).isoformat(),
        "phase": "Phase 0",
        "signals_collected": len(signals),
        "opportunities_found": len(opportunities),
        "note": "Skeleton run — no live sources connected yet.",
    }
    REPORTS_DIR.mkdir(exist_ok=True)
    report_path = REPORTS_DIR / "pipeline_smoke.json"
    report_path.write_text(json.dumps(report, indent=2), encoding="utf-8")
    return report


if __name__ == "__main__":
    summary = run_pipeline()
    print(json.dumps(summary, indent=2))
