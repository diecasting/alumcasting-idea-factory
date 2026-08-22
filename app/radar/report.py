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
    total_problem = sum(1 for s in signals if s.is_problem_signal)
    return RadarReport(
        generated_at=generated_at,
        total_relevant=len(signals),
        total_deduped=len(signals),
        total_problem=total_problem,
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
            [
                "priority",
                "topic",
                "signal_type",
                "relevance_score",
                "problem_score",
                "heat_score",
                "opportunity_score",
                "is_problem_signal",
                "opportunity_rank",
                "score_reasons",
                "audience",
                "search_intent",
                "recommended_title",
                "core_question",
                "content_angle",
                "priority_band",
                "supporting_questions",
                "suggested_outline",
                "title",
                "url",
                "source",
                "published_at",
            ]
        )
        for s in signals:
            b = getattr(s, "content_brief", None)
            w.writerow(
                [
                    s.priority.value,
                    s.topic or "",
                    s.signal_type.value,
                    round(s.relevance_score, 3),
                    round(s.problem_score, 2),
                    round(s.heat_score, 2),
                    round(s.opportunity_score, 2),
                    s.is_problem_signal,
                    s.opportunity_rank if s.opportunity_rank is not None else "",
                    "; ".join(s.score_reasons),
                    b.audience if b else "",
                    b.search_intent if b else "",
                    b.recommended_title if b else "",
                    b.core_question if b else "",
                    b.content_angle if b else "",
                    b.priority if b else "",
                    "; ".join(b.supporting_questions) if b else "",
                    "; ".join(b.suggested_outline) if b else "",
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

    # Phase 1.2: Problem Signal Quality summary.
    relevant = report.total_relevant or 0
    problem_rate = (report.total_problem / relevant * 100.0) if relevant else 0.0
    lines.append("## Problem Signals")
    lines.append("")
    lines.append(f"- Raw signals: {report.total_raw}")
    lines.append(f"- Relevant signals: {report.total_relevant}")
    lines.append(f"- Problem signals: {report.total_problem}")
    lines.append(f"- Problem signal rate: {problem_rate:.1f}%")
    lines.append("")

    # Phase 1.2: Top Content Opportunities (problem signals only, ranked).
    problems = [s for s in report.signals if s.is_problem_signal]
    problems.sort(key=lambda s: (s.opportunity_rank or 0))
    top = problems[:10]
    lines.append("## Top Content Opportunities")
    lines.append("")
    if not top:
        lines.append("_No problem signals met the opportunity threshold in this window._")
        lines.append("")
    for s in top:
        rank = s.opportunity_rank or 0
        label = (s.topic or "uncategorized").replace("_", " ").title()
        lines.append(f"### #{rank} — {s.title}")
        lines.append(f"- Topic: {label}")
        lines.append(f"- Signal Type: {s.signal_type.value.replace('_', ' ').title()}")
        lines.append(f"- Problem Score: {round(s.problem_score, 2)}")
        lines.append(f"- Heat Score: {round(s.heat_score, 2)}")
        lines.append(f"- Relevance Score: {round(s.relevance_score * 100)}")
        lines.append(f"- Opportunity Score: {round(s.opportunity_score, 2)}")
        if s.score_reasons:
            lines.append("- Score Reasons:")
            for r in s.score_reasons:
                lines.append(f"  - {r}")
        lines.append(f"- Source: {s.source}")
        if s.published_at:
            lines.append(f"- Published: {_iso(s.published_at)}")
        if s.url:
            lines.append(f"- URL: {s.url}")
        lines.append("")

    # Phase 1.3: Content Opportunity Briefs (problem signals only, ranked).
    briefs = [s for s in problems if getattr(s, "content_brief", None) is not None]
    lines.append("## Content Opportunity Briefs")
    lines.append("")
    if not briefs:
        lines.append("_No problem signals met the opportunity threshold in this window._")
        lines.append("")
    for s in briefs:
        b = s.content_brief
        rank = s.opportunity_rank or 0
        tlabel = (s.topic or "uncategorized").replace("_", " ").title()
        lines.append(f"### CONTENT OPPORTUNITY #{rank}")
        lines.append("")
        lines.append(f"- Title: {b.recommended_title}")
        lines.append(f"- Topic: {tlabel}")
        lines.append(f"- Signal Type: {s.signal_type.value.replace('_', ' ').title()}")
        lines.append(f"- Audience: {b.audience}")
        lines.append(f"- Search Intent: {b.search_intent}")
        lines.append(f"- Problem: {b.problem}")
        lines.append(f"- Core Question: {b.core_question}")
        lines.append(f"- Content Angle: {b.content_angle}")
        lines.append(f"- Priority: {b.priority}")
        if b.supporting_questions:
            lines.append("- Supporting Questions:")
            for i, q in enumerate(b.supporting_questions, start=1):
                lines.append(f"  {i}. {q}")
        if b.suggested_outline:
            lines.append("- Suggested Outline:")
            for i, step in enumerate(b.suggested_outline, start=1):
                lines.append(f"  {i}. {step}")
        lines.append(f"- Source: {s.source}")
        if s.url:
            lines.append(f"- URL: {s.url}")
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
            if s.is_problem_signal:
                lines.append(f"- Problem score: {round(s.problem_score, 2)}")
                lines.append(f"- Opportunity rank: {s.opportunity_rank}")
            lines.append(f"- Source: {s.source}")
            if s.url:
                lines.append(f"- URL: {s.url}")
            if s.matched_keywords:
                shown = ", ".join(s.matched_keywords[:8])
                lines.append(f"- Matched: {shown}")
            lines.append("")

    Path(path).write_text("\n".join(lines), encoding="utf-8")
