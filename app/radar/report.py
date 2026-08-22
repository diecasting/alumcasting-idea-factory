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
                "search_demand_status",
                "gsc_available",
                "matched_gsc_queries",
                "gsc_evidence_rows",
                # Phase 1.5D: Site Coverage & Content Gap columns (sibling of the
                # GSC search-demand columns; strictly separate evidence class).
                "site_coverage",
                "content_gap_status",
                "coverage_confidence",
                "coverage_sources",
                "matched_pages",
                "problem_coverage_evidence",
            ]
        )
        for s in signals:
            b = getattr(s, "content_brief", None)
            se = getattr(s, "search_evidence", None)
            se_dict = se.to_dict() if se is not None else None
            sc = getattr(s, "site_coverage", None)
            sc_dict = sc.to_dict() if sc is not None else None
            if sc_dict:
                _pce = sc_dict.get("problem_coverage_evidence") or {}
                pce_str = (
                    f"page_existence={_pce.get('page_existence')}; "
                    f"topical_match={_pce.get('topical_match')}; "
                    f"problem_match={_pce.get('problem_match')}; "
                    f"performance={_pce.get('performance') or '-'}"
                )
                mp_str = "; ".join(
                    f'{m["url"]} [{m["match_tier"]}/{m["source"]}/'
                    f'perf={m["performance"] or "-"}]'
                    for m in sc_dict.get("matched_pages", [])
                )
            else:
                pce_str = ""
                mp_str = ""
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
                    (se_dict["search_demand_status"] if se_dict else ""),
                    (se_dict["gsc_available"] if se_dict else ""),
                    ("; ".join(se_dict["matched_queries"]) if se_dict else ""),
                    (len(se_dict["evidence"]) if se_dict else 0),
                    # Phase 1.5D values (empty when no site-coverage evidence).
                    (sc_dict["site_coverage"] if sc_dict else ""),
                    (sc_dict["content_gap_status"] if sc_dict else ""),
                    (sc_dict["coverage_confidence"] if sc_dict else ""),
                    ("; ".join(sc_dict["coverage_sources"]) if sc_dict else ""),
                    mp_str,
                    pce_str,
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

    # Phase 1.5C: First-party GSC search-demand evidence (problem signals only).
    # Terminology is deliberately precise: GSC impressions are FIRST-PARTY
    # performance data for the verified property and are NOT third-party search
    # volume or market demand.
    lines.append("## Search Demand Evidence")
    lines.append("")
    lines.append(
        "_First-party Google Search Console (GSC) evidence for the verified "
        "property. GSC impressions reflect performance for this property only "
        "\u2014 they are NOT third-party search volume and must not be read as "
        "total market demand. A query absent from GSC only means no matching "
        "first-party evidence was retrieved for the property / window._"
    )
    lines.append("")
    evidence_signals = [s for s in problems if getattr(s, "search_evidence", None) is not None]
    if not evidence_signals:
        lines.append("_No problem signals carried GSC search evidence in this run._")
        lines.append("")
    for s in evidence_signals:
        se = s.search_evidence
        se_dict = se.to_dict()
        rank = s.opportunity_rank or 0
        title = (se_dict.get("matched_queries") and se_dict["matched_queries"]) or (
            s.content_brief.recommended_title if s.content_brief else s.title
        )
        lines.append(f"### CONTENT OPPORTUNITY #{rank} \u2014 Search Demand")
        lines.append("")
        lines.append(f"- Recommended Title: {s.content_brief.recommended_title if s.content_brief else s.title}")
        status = se_dict["search_demand_status"]
        lines.append(f"- Search Demand Status: {status}")
        lines.append(f"- GSC Available: {'yes' if se_dict['gsc_available'] else 'no'}")
        matched = se_dict["matched_queries"]
        if matched:
            lines.append(f"- Matching GSC Queries ({len(matched)}):")
            for q in matched:
                lines.append(f"  - {q}")
        else:
            lines.append("- Matching GSC Queries: none")
        ev = se_dict["evidence"]
        if ev:
            lines.append(f"- First-party GSC evidence for this property ({len(ev)} row(s)):")
            for e in ev:
                lines.append(
                    f"  - query: \"{e['query']}\" | page: {e['page'] or '(property)'} "
                    f"| impressions: {e['impressions']} | clicks: {e['clicks']} "
                    f"| ctr: {e['ctr']:.4f} | position: {e['position']:.2f}"
                )
        if status == "unknown":
            lines.append(
                "  - NOTE: No first-party GSC evidence was retrieved (GSC disabled "
                "or unavailable). This does NOT imply zero market demand."
            )
        elif status == "not_validated":
            lines.append(
                "  - NOTE: GSC was queried successfully but no matching query "
                "evidence was found. This is NOT evidence of zero search demand."
            )
        lines.append("")

    # Phase 1.5D: Site Coverage & Content Gap Validation (problem signals only).
    # Terminology is deliberately precise: "unknown" means the evidence required
    # to decide coverage was NOT available in this run. It MUST NOT be read as
    # "no content exists" or "the topic is uncovered". Coverage evidence is kept
    # strictly separate from the GSC search-demand evidence above.
    lines.append("## Site Coverage & Content Gap")
    lines.append("")
    lines.append(
        "_Deterministic site-coverage and content-gap check for problem signals. "
        "Coverage is judged from first-party GSC page evidence (when available) "
        "and the site sitemap inventory. \"unknown\" means the evidence required "
        "to decide coverage was not available in this run \u2014 it is NOT a "
        "statement that no content exists or that the topic is uncovered._"
    )
    lines.append("")
    coverage_signals = [s for s in problems if getattr(s, "site_coverage", None) is not None]
    if not coverage_signals:
        lines.append("_No problem signals carried site-coverage evidence in this run._")
        lines.append("")
    for s in coverage_signals:
        sc = s.site_coverage
        sc_dict = sc.to_dict()
        rank = s.opportunity_rank or 0
        title = s.content_brief.recommended_title if s.content_brief else s.title
        lines.append(f"### CONTENT OPPORTUNITY #{rank} \u2014 Site Coverage")
        lines.append("")
        lines.append(f"- Recommended Title: {title}")
        lines.append(f"- Site Coverage: {sc_dict['site_coverage']}")
        lines.append(f"- Content Gap Status: {sc_dict['content_gap_status']}")
        lines.append(f"- Coverage Confidence: {sc_dict['coverage_confidence']}")
        sources = sc_dict["coverage_sources"]
        lines.append(f"- Coverage Sources: {('; '.join(sources)) if sources else 'none'}")
        mp = sc_dict["matched_pages"]
        if mp:
            lines.append(f"- Matched Pages ({len(mp)}):")
            for m in mp:
                lines.append(
                    f"  - {m['url']} | tier: {m['match_tier']} | source: {m['source']} "
                    f"| performance: {m['performance'] or '-'} "
                    f"| position: {m['position']:.2f}"
                )
        pce = sc_dict["problem_coverage_evidence"] or {}
        lines.append(
            f"- Problem Coverage: page_existence={pce.get('page_existence')}; "
            f"topical_match={pce.get('topical_match')}; "
            f"problem_match={pce.get('problem_match')}; "
            f"performance={pce.get('performance') or '-'}"
        )
        if sc_dict["site_coverage"] == "unknown":
            lines.append(
                "  - NOTE: Coverage could not be determined because no site "
                "inventory and no first-party GSC page evidence were available. "
                "This does NOT mean the topic is uncovered or that no content "
                "exists \u2014 absence of evidence is not evidence of absence."
            )
        elif sc_dict.get("multiple_competing_pages"):
            lines.append(
                "  - NOTE: Multiple pages appear to compete for the same query. "
                "Consider consolidating existing content rather than creating new."
            )
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
