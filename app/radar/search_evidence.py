"""Candidate-query matching & first-party GSC search-demand evidence (Phase 1.5C).

This module consumes the *already-retrieved* Search Analytics rows produced by
the Phase 1.5B ``GSCAdapter`` and attaches **search-demand evidence** to the
radar's problem signals. It does NOT:

  * change any existing score (problem_score / heat_score / opportunity_score /
    opportunity_rank / score_reasons),
  * modify ContentBrief,
  * implement SERP / Google Trends / site-coverage / content-gap classification
    (those are later phases: 1.5D / Phase 2),
  * use AI / LLM / embeddings / semantic or fuzzy matching,
  * create OAuth credentials, Google Cloud resources, secrets, or GitHub Actions
    changes,
  * make any network call itself (the transport is injected by the adapter).

GSC remains OPTIONAL and DISABLED by default. When GSC is disabled (or
unavailable / fails), every problem signal simply receives a ``search_demand_status``
of ``unknown`` — which MUST NOT be read as "zero market demand". A query absent
from GSC only means "no matching first-party GSC evidence was retrieved for this
property / window."

Key semantics:

  * Matching is NORMALIZED EXACT MATCH ONLY (candidate normalized query string
    must equal a normalized GSC row query string). No token overlap, no
    substring, no fuzzy, no semantic matching.
  * GSC ``impressions`` are first-party performance data for the verified
    property. They are NEVER relabeled as "search volume" / "monthly search
    volume" / "market demand".
  * No search-volume / monthly-search-volume / CPC / keyword-difficulty /
    SERP-competition fields are ever produced.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Optional

# Re-used so we can detect a transport/auth failure without modifying the
# Phase 1.5B adapter (the adapter returns TRANSPORT_FAILURE == -1 on a
# transport-level error).
from app.radar.sources.gsc import TRANSPORT_FAILURE


# --------------------------------------------------------------------------- #
# Search-demand status                                                         #
# --------------------------------------------------------------------------- #

class SearchDemandStatus(str, Enum):
    """Precise status of first-party GSC evidence for a candidate query set.

    validated      -> at least one exact-normalized GSC query match exists with
                      impressions > 0.
    not_validated  -> GSC was successfully queried, but no matching query
                      evidence exists (this is NOT "zero search demand").
    unknown        -> GSC disabled, unavailable, authentication failure,
                      transport failure, or otherwise unable to retrieve usable
                      GSC evidence.
    """

    VALIDATED = "validated"
    NOT_VALIDATED = "not_validated"
    UNKNOWN = "unknown"


# --------------------------------------------------------------------------- #
# Deterministic query normalization                                           #
# --------------------------------------------------------------------------- #

# Conservative, safe singular/plural map. These are manufacturing-relevant
# terms where the singular is unambiguous. We deliberately do NOT apply a
# generic "drop trailing s" rule, because that would mangle non-plural words
# like "gas", "process", "class", "bus".
_SAFE_PLURAL_MAP = {
    "defects": "defect",
    "cracks": "crack",
    "porosities": "porosity",
    "voids": "void",
    "shrinkages": "shrinkage",
    "blisters": "blister",
    "flashes": "flash",
    "warps": "warp",
    "inclusions": "inclusion",
    "alloys": "alloy",
    "casts": "cast",
    "parts": "part",
    "issues": "issue",
    "problems": "problem",
    "solutions": "solution",
    "causes": "cause",
    "cavities": "cavity",
    "deficiencies": "deficiency",
    "questions": "question",
}

# Highly-regular English plural endings that are safe to reverse without a
# dictionary. Applied per-token after the explicit map above.
_ES_ENDINGS = ("ses", "xes", "zes", "ches", "shes")


def _deplural(token: str) -> str:
    """Conservative singular normalization for a single token.

    Only regular, unambiguous plural forms are touched. Returns the token
    unchanged when no safe rule applies.
    """
    if not token:
        return token
    if token in _SAFE_PLURAL_MAP:
        return _SAFE_PLURAL_MAP[token]
    # "ies" -> "y" (deficiencies -> deficiency, cavities -> cavity).
    if len(token) >= 4 and token.endswith("ies"):
        return token[:-3] + "y"
    # Regular plural after a sibilant (boxes -> box, churches -> church,
    # glasses -> glass, processes -> process).
    if token.endswith(_ES_ENDINGS):
        return token[:-2]
    return token


_PUNCT_RE = re.compile(r"[^\w\s]")
_WS_RE = re.compile(r"\s+")


def normalize_query(query: str) -> str:
    """Deterministic candidate-query normalization.

    Sequence (per spec):
      1. Unicode NFC normalization (compose accents; makes decomposed and
         precomposed forms identical).
      2. lowercase.
      3. strip punctuation (keep alphanumerics and spaces).
      4. collapse internal whitespace to a single space.
      5. trim leading/trailing whitespace.
      6. conservative singular/plural normalization (per token, only where safe).

    No AI, embeddings, semantic similarity, fuzzy matching, external NLP
    packages, or aggressive stemming. Unrelated words are never collapsed into
    equivalent terms.
    """
    if not query:
        return ""
    q = unicodedata.normalize("NFC", query)
    q = q.lower()
    q = _PUNCT_RE.sub(" ", q)
    q = _WS_RE.sub(" ", q).strip()
    if not q:
        return ""
    return " ".join(_deplural(tok) for tok in q.split())


# --------------------------------------------------------------------------- #
# Candidate query extraction                                                   #
# --------------------------------------------------------------------------- #

def extract_candidate_queries(brief: Any) -> list[str]:
    """Derive candidate queries from a ContentBrief (preserving source order).

    Order: core_question, recommended_title, supporting_questions.
    No additional queries are invented. ``brief`` may be a ContentBrief object
    or a plain dict (for testability).
    """
    if brief is None:
        return []
    if hasattr(brief, "core_question"):
        core = brief.core_question or ""
        title = brief.recommended_title or ""
        supporting = list(getattr(brief, "supporting_questions", []) or [])
    elif isinstance(brief, dict):
        core = brief.get("core_question") or ""
        title = brief.get("recommended_title") or ""
        supporting = list(brief.get("supporting_questions") or [])
    else:
        return []
    candidates: list[str] = []
    if core:
        candidates.append(core)
    if title:
        candidates.append(title)
    candidates.extend([q for q in supporting if q])
    return candidates


def dedupe_candidates(candidates: list[str]) -> list[tuple[str, str]]:
    """Return ``(original, normalized)`` pairs, deduplicated by normalized form.

    Source order is preserved; the first occurrence of a normalized form wins.
    Empty / whitespace-only candidates are dropped.
    """
    seen: set[str] = set()
    out: list[tuple[str, str]] = []
    for c in candidates:
        nc = normalize_query(c)
        if not nc:
            continue
        if nc in seen:
            continue
        seen.add(nc)
        out.append((c, nc))
    return out


# --------------------------------------------------------------------------- #
# Search evidence data structures                                              #
# --------------------------------------------------------------------------- #

@dataclass
class GSCSearchEvidence:
    """One matched first-party GSC Search Analytics row.

    All fields preserve the meaning of the underlying GSC data. ``impressions``
    is kept verbatim and is NEVER relabeled as search volume. ``country`` /
    ``device`` are populated only when the upstream GSC request was dimensioned
    by them; otherwise they are empty strings.

    No search-volume / monthly-search-volume / CPC / keyword-difficulty /
    SERP-competition field exists here.
    """

    query: str = ""
    normalized_query: str = ""
    page: str = ""
    country: str = ""
    device: str = ""
    clicks: int = 0
    impressions: int = 0
    ctr: float = 0.0
    position: float = 0.0
    date_start: str = ""
    date_end: str = ""
    retrieved_at: str = ""
    source: str = ""

    @classmethod
    def from_row(
        cls,
        row: dict[str, Any],
        normalized_query: str,
        date_start: str = "",
        date_end: str = "",
        retrieved_at: str = "",
        source: str = "",
    ) -> "GSCSearchEvidence":
        if not isinstance(row, dict):
            row = {}
        return cls(
            query=row.get("query", "") or "",
            normalized_query=normalized_query,
            page=row.get("page", "") or "",
            country=row.get("country", "") or "",
            device=row.get("device", "") or "",
            clicks=int(row.get("clicks", 0) or 0),
            impressions=int(row.get("impressions", 0) or 0),
            ctr=float(row.get("ctr", 0.0) or 0.0),
            position=float(row.get("position", 0.0) or 0.0),
            date_start=date_start,
            date_end=date_end,
            retrieved_at=retrieved_at,
            source=source,
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "query": self.query,
            "normalized_query": self.normalized_query,
            "page": self.page,
            "country": self.country,
            "device": self.device,
            "clicks": self.clicks,
            "impressions": self.impressions,
            "ctr": self.ctr,
            "position": self.position,
            "date_start": self.date_start,
            "date_end": self.date_end,
            "retrieved_at": self.retrieved_at,
            "source": self.source,
        }


@dataclass
class SearchEvidence:
    """Per-signal search-demand evidence result.

    ``status`` is one of validated / not_validated / unknown. ``evidence`` holds
    every matched GSC row (no aggregation). ``candidate_queries`` are the
    deduplicated normalized candidate queries that were tested for a match.
    """

    status: SearchDemandStatus = SearchDemandStatus.UNKNOWN
    gsc_available: bool = False
    candidate_queries: list[str] = field(default_factory=list)
    matched_queries: list[str] = field(default_factory=list)
    evidence: list[GSCSearchEvidence] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "search_demand_status": self.status.value,
            "gsc_available": self.gsc_available,
            "candidate_queries": list(self.candidate_queries),
            "matched_queries": list(self.matched_queries),
            "evidence": [e.to_dict() for e in self.evidence],
        }


# --------------------------------------------------------------------------- #
# Matching                                                                     #
# --------------------------------------------------------------------------- #

def match_candidates(
    candidates: list[str],
    gsc_rows: list[dict[str, Any]],
    date_start: str = "",
    date_end: str = "",
    retrieved_at: str = "",
    source: str = "",
) -> tuple[list[tuple[str, str]], list[str], list[GSCSearchEvidence]]:
    """NORMALIZED EXACT MATCH of candidate queries against GSC rows.

    Returns ``(norm_candidates, matched_normalized_queries, matched_evidence)``.

    A candidate matches a GSC row iff ``normalize_query(candidate) ==
    normalize_query(gsc_row["query"])``. No token overlap, substring, fuzzy, or
    semantic matching. Multiple GSC rows for the same normalized query are all
    retained (no aggregation).
    """
    norm_candidates = dedupe_candidates(candidates)

    # Build an index: normalized GSC query -> list of raw rows.
    index: dict[str, list[dict[str, Any]]] = {}
    for row in gsc_rows or []:
        gq = (row or {}).get("query", "") or ""
        ngq = normalize_query(gq)
        if not ngq:
            continue
        index.setdefault(ngq, []).append(row)

    matched_normalized: list[str] = []
    matched_evidence: list[GSCSearchEvidence] = []
    for _orig, nc in norm_candidates:
        rows = index.get(nc)
        if not rows:
            continue
        matched_normalized.append(nc)
        for row in rows:
            matched_evidence.append(
                GSCSearchEvidence.from_row(
                    row,
                    normalized_query=nc,
                    date_start=date_start,
                    date_end=date_end,
                    retrieved_at=retrieved_at,
                    source=source,
                )
            )
    return norm_candidates, matched_normalized, matched_evidence


def build_search_evidence(
    brief: Any,
    gsc_rows: list[dict[str, Any]],
    gsc_status: str,
    date_start: str = "",
    date_end: str = "",
    retrieved_at: str = "",
    source: str = "",
) -> SearchEvidence:
    """Build a ``SearchEvidence`` for one problem signal's ContentBrief.

    ``gsc_status`` must be one of: "disabled", "unavailable", "available".
      * "disabled" / "unavailable" -> status ``unknown`` (no usable evidence).
      * "available" -> ``validated`` if at least one matched row has impressions
        > 0, otherwise ``not_validated``.
    """
    candidates = extract_candidate_queries(brief)
    _norm_candidates, matched_normalized, matched_evidence = match_candidates(
        candidates, gsc_rows, date_start, date_end, retrieved_at, source
    )
    candidate_normals = [nc for (_orig, nc) in _norm_candidates]

    if gsc_status in ("disabled", "unavailable"):
        status = SearchDemandStatus.UNKNOWN
    else:
        if matched_evidence and any(e.impressions > 0 for e in matched_evidence):
            status = SearchDemandStatus.VALIDATED
        else:
            status = SearchDemandStatus.NOT_VALIDATED

    return SearchEvidence(
        status=status,
        gsc_available=(gsc_status == "available"),
        candidate_queries=candidate_normals,
        matched_queries=matched_normalized,
        evidence=matched_evidence,
    )


# --------------------------------------------------------------------------- #
# GSC query execution (non-blocking, status-aware)                            #
# --------------------------------------------------------------------------- #

def query_gsc_rows(adapter: Any) -> tuple[list[dict[str, Any]], str, str, str]:
    """Run the Phase 1.5B adapter's query and classify the outcome.

    Returns ``(rows, gsc_status, date_start, date_end)`` where ``gsc_status`` is:

      * "disabled"    -> adapter not enabled (transport never invoked).
      * "unavailable" -> queried but never reached HTTP 200 (auth / quota /
                         server / timeout / transport failure).
      * "available"   -> at least one request returned HTTP 200 (rows may still
                         be empty).

    The adapter itself is NOT modified: we temporarily wrap its injected
    transport with a recorder, then restore the original transport in a
    ``finally`` block. Failures inside the adapter are propagated as
    "unavailable" (non-blocking).
    """
    if not getattr(adapter, "is_enabled", lambda: False)():
        return [], "disabled", "", ""

    try:
        date_start, date_end = adapter._date_range()
    except Exception:
        date_start, date_end = "", ""

    outcomes: list[int] = []
    base_transport = adapter.transport

    def _recorder(url, method="POST", headers=None, data=b"", timeout=30.0):
        try:
            status, body = base_transport(
                url, method=method, headers=headers, data=data, timeout=timeout
            )
        except Exception:
            status, body = TRANSPORT_FAILURE, ""
        outcomes.append(status)
        return status, body

    adapter.transport = _recorder
    try:
        rows = adapter.query()
    except Exception:
        rows = []
    finally:
        adapter.transport = base_transport

    if not outcomes:
        status = "unavailable"
    else:
        status = "available" if any(o == 200 for o in outcomes) else "unavailable"
    return rows, status, date_start, date_end


# --------------------------------------------------------------------------- #
# Pipeline attachment                                                          #
# --------------------------------------------------------------------------- #

def attach_search_evidence_to_signals(
    signals: list[Any],
    gsc_rows: list[dict[str, Any]],
    gsc_status: str,
    date_start: str = "",
    date_end: str = "",
    source: str = "",
) -> None:
    """Attach a ``SearchEvidence`` to every problem signal (sibling of
    content_brief). Non-problem signals get ``search_evidence = None``.

    This NEVER mutates scores, ContentBrief, ranking, or any other field.
    """
    retrieved_at = datetime.now(timezone.utc).isoformat()
    for s in signals:
        if getattr(s, "is_problem_signal", False) and getattr(s, "content_brief", None) is not None:
            s.search_evidence = build_search_evidence(
                s.content_brief,
                gsc_rows,
                gsc_status,
                date_start=date_start,
                date_end=date_end,
                retrieved_at=retrieved_at,
                source=source or getattr(s, "source", ""),
            )
        else:
            s.search_evidence = None
