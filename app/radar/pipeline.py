"""Content Opportunity Radar pipeline (Phase 1).

SOURCE -> COLLECT -> NORMALIZE -> RELEVANCE FILTER -> DEDUPE -> REPORT

Run:
    python -m app.radar.pipeline              # live collection (needs network)
    python -m app.radar.pipeline --dry-run    # fixtures only, no network (CI-safe)
    python -m app.radar.pipeline --out-dir /tmp/radar

Outputs (under --out-dir, default repo root):
    data/raw_signals.json
    data/normalized_signals.json
    data/deduplicated_signals.json
    reports/content_opportunity_report.json
    reports/content_opportunity_report.csv
    reports/content_opportunity_report.md

No LLM, no paid APIs, no database, no external server. Public-data only.
"""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
from pathlib import Path

from app.radar.dedupe import dedupe
from app.radar.models import RadarReport, RawSignal
from app.radar.normalize import normalize_many
from app.radar.relevance import classify, is_relevant
from app.radar.report import (
    PRIORITY_RANK,
    build_report,
    write_csv,
    write_json,
    write_markdown,
)
from app.radar.scoring import load_config, rank_opportunities, score_signal
from app.radar.sources.reddit import RedditSource
from app.radar.sources.rss import RSSSource

# Curated, public subreddits relevant to the tracked topics. Missing/private
# subreddits simply yield no signals (collection is failure-tolerant).
DEFAULT_SUBREDDITS = [
    ("manufacturing", "hot", 25),
    ("machining", "hot", 25),
    ("CNC", "hot", 25),
    ("MetalCasting", "hot", 25),
    ("finishing", "hot", 25),
    ("AskEngineers", "hot", 25),
]

# Free RSS/Atom feeds (best-effort, easily edited). A feed that 404s or fails to
# parse is skipped without breaking the run.
DEFAULT_FEEDS = [
    "https://www.engineering.com/feed/",
    "https://www.thefabricator.com/rss",
    "https://www.canadianmetalworking.com/rss",
    "https://www.pcimag.com/rss",
    "https://www.powderbulletin.com/feed/",
]


def _default_fixtures_dir() -> Path:
    return Path(__file__).resolve().parent / "fixtures"


def _dry_run_sources(fixtures_dir: Path):
    """Build sources that return bundled fixtures instead of hitting network."""
    sources = []
    fd = Path(fixtures_dir)
    reddit_fixture = fd / "reddit_hot.json"
    if reddit_fixture.exists():
        content = reddit_fixture.read_text(encoding="utf-8")
        sources.append(RedditSource("manufacturing", transport=lambda u, c=content: c))
    for name, fname in (
        ("sample_rss", "sample_feed.xml"),
        ("sample_atom", "sample_atom.xml"),
    ):
        p = fd / fname
        if p.exists():
            content = p.read_text(encoding="utf-8")
            sources.append(
                RSSSource(f"https://example.com/{name}", transport=lambda u, c=content: c)
            )
    return sources


def build_default_sources():
    sources = []
    for sub, sort, lim in DEFAULT_SUBREDDITS:
        sources.append(RedditSource(sub, sort=sort, limit=lim))
    for feed in DEFAULT_FEEDS:
        sources.append(RSSSource(feed))
    return sources


def run_pipeline(
    sources=None,
    out_dir: str = ".",
    dry_run: bool = False,
    generated_at: datetime | None = None,
    fixtures_dir=None,
) -> RadarReport:
    generated_at = generated_at or datetime.now(timezone.utc)

    if dry_run:
        sources = _dry_run_sources(Path(fixtures_dir) if fixtures_dir else _default_fixtures_dir())
    else:
        sources = sources or build_default_sources()

    raw: list[RawSignal] = []
    for src in sources:
        try:
            raw.extend(src.collect())
        except Exception:
            # Defensive: never let one source crash the whole pipeline.
            continue

    normalized = normalize_many(raw)
    for n in normalized:
        classify(n)

    relevant = [n for n in normalized if is_relevant(n)]
    deduped = dedupe(relevant)

    # Phase 1.2: Problem Signal Quality & Opportunity Ranking.
    cfg = load_config()
    for n in deduped:
        score_signal(n, cfg)
    rank_opportunities(deduped)

    deduped.sort(key=lambda s: (PRIORITY_RANK.get(s.priority, 9), -s.relevance_score))

    report = build_report(deduped, generated_at=generated_at)
    report.total_raw = len(raw)
    report.total_normalized = len(normalized)

    out = Path(out_dir)
    write_json(out / "data" / "raw_signals.json", raw)
    write_json(out / "data" / "normalized_signals.json", normalized)
    write_json(out / "data" / "deduplicated_signals.json", deduped)
    write_json(out / "reports" / "content_opportunity_report.json", report.to_dict())
    write_csv(out / "reports" / "content_opportunity_report.csv", deduped)
    write_markdown(out / "reports" / "content_opportunity_report.md", report)

    return report


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Manufacturing Content Opportunity Radar")
    parser.add_argument("--dry-run", action="store_true", help="Use bundled fixtures (no network)")
    parser.add_argument("--out-dir", default=".", help="Output directory (default: repo root)")
    args = parser.parse_args(argv)

    report = run_pipeline(out_dir=args.out_dir, dry_run=args.dry_run)

    print("Content Opportunity Radar complete")
    print(f"  raw collected : {report.total_raw}")
    print(f"  normalized    : {report.total_normalized}")
    print(f"  relevant      : {report.total_relevant}")
    print(f"  deduped       : {report.total_deduped}")
    print(f"  problem signals: {report.total_problem}")
    print(f"  by topic      : {report.by_topic}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
