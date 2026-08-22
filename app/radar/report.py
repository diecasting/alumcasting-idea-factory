"""Report writers: JSON (raw/normalized/deduped), CSV, and Markdown.

Outputs are written under a configurable directory (default repo root: data/
and reports/). The Markdown report groups opportunities by topic and orders
them by priority then relevance score, so the most actionable problems surface
first.
"""

from __future__ import annotations

import csv
import json
from datetime import datetime, timezone
from pathlib import Path

from app.radar.models import NormalizedSignal, Priority, RadarReport

PRIORITY_RANK = {Priority.HIGH: 0, Priority.MEDIUM: 1, Priority.LOW: 2}
TOPIC_ORDER = ("die_casting", "casting", "cnc_machining", "powder_coating")


def _iso(dt) -> str:
    if dt is None:
        return ""
    if isinstance(dt, datetime) and dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.isoformat() if isinstance(dt, datetime) else str(dt)


def build_report(signals: list[NormalizedSignal], generated_at=None) -> RadarReport:
    generated_at = generated_at or datetime.now(timezone.utc)
    by_topic: dict[str, int] = {}
    for s in signals:
        topic = s.topic or "uncategorized"
        by_topic[topic] = by_topic.get(topic, 0) + 1
    return RadarReport(
        generated_at=generated_at,
        total_relevant=len(signals),
        total_deduped=len(signals),
        by_topic=by_topic,
        signals=signals,
    )


def write_json(path, obj) -> None:
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    serializable = [o.to_dict() for o in obj] if isinstance(obj, list) else obj
    with open(path, "w", encoding="utf-8") as f:
        json.dump(serializable, f, indent=2, ensure_ascii=False)


def write_csv(path, signals: list[NormalizedSignal]) -> None:
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="") as f:
        w = csv.writer(f)
        w.writerow(
            ["priority", "topic", "signal_type", "score", "title", "url", "source", "published_at"]
        )
        for s in signals:
            w.writerow(
                [
                    s.priority.value,
                    s.topic or "",
                    s.signal_type.value,
                    round(s.relevance_score, 3),
                    s.title,
                    s.url,
                    s.source,
                    _iso(s.published_at),
                ]
            )


def write_markdown(path, report: RadarReport) -> None:
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    lines: list[str] = []
    lines.append("# Manufacturing Content Opportunity Report")
    lines.append("")
    lines.append(f"Generated: {_iso(report.generated_at)}")
    lines.append("")
    lines.append(f"- Raw signals collected: {report.total_raw}")
    lines.append(f"- Normalized: {report.total_normalized}")
    lines.append(f"- Relevant (after filtering): {report.total_relevant}")
    lines.append(f"- After deduplication: {report.total_deduped}")
    lines.append("")

    order = [t for t in TOPIC_ORDER if t in report.by_topic]
    order += [t for t in report.by_topic if t not in order]

    for topic in order:
        items = [s for s in report.signals if (s.topic or "uncategorized") == topic]
        items.sort(key=lambda s: (PRIORITY_RANK.get(s.priority, 9), -s.relevance_score))
        label = topic.replace("_", " ").title()
        lines.append(f"## {label} ({len(items)})")
        lines.append("")
        if not items:
            lines.append("_No high-value signals in this window._")
            lines.append("")
            continue
        for s in items:
            lines.append(f"### [{s.priority.value.upper()}] {s.title}")
            lines.append(f"- Type: {s.signal_type.value}")
            lines.append(f"- Relevance score: {round(s.relevance_score, 3)}")
            lines.append(f"- Source: {s.source}")
            if s.url:
                lines.append(f"- URL: {s.url}")
            if s.matched_keywords:
                shown = ", ".join(s.matched_keywords[:8])
                lines.append(f"- Matched: {shown}")
            lines.append("")

    Path(path).write_text("\n".join(lines), encoding="utf-8")
