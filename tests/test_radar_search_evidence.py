"""Tests for Phase 1.5C candidate-query matching & first-party GSC evidence.

Network-free: every case either synthesizes GSC rows directly or injects a fake
transport into the Phase 1.5B GSCAdapter. No real credentials, no real GSC
calls, no persistence. GSC must remain disabled by default.

Deterministic, no AI / LLM / embeddings / fuzzy / semantic matching.
"""

import json

import pytest

from app.radar.brief import ContentBrief, generate_content_brief
from app.radar.models import NormalizedSignal, SignalType
from app.radar.normalize import normalize_signal
from app.radar.relevance import classify
from app.radar.scoring import load_config, score_signal
from app.radar.search_evidence import (
    SearchDemandStatus,
    SearchEvidence,
    attach_search_evidence_to_signals,
    build_search_evidence,
    dedupe_candidates,
    extract_candidate_queries,
    match_candidates,
    normalize_query,
    query_gsc_rows,
)
from app.radar.sources.gsc import GSCAdapter
from app.radar.sources import build_gsc_adapter


# --------------------------------------------------------------------------- #
# Helpers                                                                      #
# --------------------------------------------------------------------------- #

def _gsc_row(query, page="https://alumcasting.com/p", clicks=1, impressions=10, ctr=0.1, position=5.0):
    return {
        "query": query,
        "page": page,
        "clicks": clicks,
        "impressions": impressions,
        "ctr": ctr,
        "position": position,
    }


def _brief(core, title, supporting):
    return ContentBrief(
        problem="x",
        topic="die_casting",
        signal_type="defect_problem",
        audience="quality_engineer",
        search_intent="troubleshooting",
        recommended_title=title,
        core_question=core,
        supporting_questions=list(supporting),
        content_angle="causes_and_solutions",
        suggested_outline=[],
        priority="medium",
        source_signal={},
    )


def _scored_problem(title="Why am I getting porosity in my aluminum die casting?"):
    from app.radar.models import RawSignal

    raw = RawSignal(
        source="reddit", source_type="reddit", external_id="t1", title=title,
        body="", url="https://www.reddit.com/r/example/1",
    )
    n = normalize_signal(raw)
    classify(n)
    score_signal(n, load_config())
    assert n.is_problem_signal is True
    n.content_brief = generate_content_brief(n)
    return n


# --------------------------------------------------------------------------- #
# 1. Candidate extraction                                                      #
# --------------------------------------------------------------------------- #

def test_extract_candidate_queries_order_and_sources():
    b = _brief(
        core="Why does porosity occur in aluminum die casting?",
        title="Aluminum Die Casting Porosity: Causes, Prevention and Solutions",
        supporting=[
            "What causes porosity in aluminum die casting?",
            "How does trapped gas form during casting?",
        ],
    )
    cands = extract_candidate_queries(b)
    assert cands[0] == "Why does porosity occur in aluminum die casting?"
    assert cands[1] == "Aluminum Die Casting Porosity: Causes, Prevention and Solutions"
    # supporting questions follow, in order, unchanged.
    assert cands[2] == "What causes porosity in aluminum die casting?"
    assert cands[3] == "How does trapped gas form during casting?"
    # No invented queries beyond the three documented sources.
    assert len(cands) == 4


def test_extract_candidate_queries_from_dict():
    b = {
        "core_question": "Q core",
        "recommended_title": "T title",
        "supporting_questions": ["S1", "S2"],
    }
    assert extract_candidate_queries(b) == ["Q core", "T title", "S1", "S2"]


def test_extract_candidate_queries_none_returns_empty():
    assert extract_candidate_queries(None) == []
    assert extract_candidate_queries("not a brief") == []


# --------------------------------------------------------------------------- #
# 2. Candidate ordering & deduplication                                        #
# --------------------------------------------------------------------------- #

def test_candidate_ordering_preserved():
    cands = ["alpha", "beta", "gamma"]
    out = dedupe_candidates(cands)
    assert [orig for (orig, _nc) in out] == ["alpha", "beta", "gamma"]


def test_candidate_deduplication_by_normalized():
    # Two candidates that normalize identically collapse to one (first wins).
    cands = [
        "Why am I getting porosity?",
        "why am i getting porosity?",  # same after normalization
        "Different query",
    ]
    out = dedupe_candidates(cands)
    normalized = [nc for (_orig, nc) in out]
    assert normalized.count("why am i getting porosity") == 1
    # First occurrence (original casing) is retained.
    assert out[0][0] == "Why am I getting porosity?"
    assert len(out) == 2


def test_dedupe_drops_empty_candidates():
    out = dedupe_candidates(["   ", "?", "real query"])
    assert len(out) == 1
    assert out[0][1] == "real query"


# --------------------------------------------------------------------------- #
# 3. Normalization                                                             #
# --------------------------------------------------------------------------- #

def test_unicode_nfc_normalization():
    # Precomposed 'é' (U+00E9) vs combining sequence 'e' + combining acute
    # (U+0065 U+0301) must normalize identically.
    precomposed = "caf\u00e9 porosity"
    decomposed = "cafe\u0301 porosity"
    assert normalize_query(precomposed) == normalize_query(decomposed)
    # And equals the canonical NFC form.
    import unicodedata
    assert normalize_query(precomposed) == unicodedata.normalize("NFC", decomposed).lower()


def test_case_normalization_matches_example():
    a = "Why am I getting porosity in my aluminum die casting?"
    b = "why am i getting porosity in my aluminum die casting"
    assert normalize_query(a) == normalize_query(b)
    assert normalize_query(a) == "why am i getting porosity in my aluminum die casting"


def test_punctuation_normalization():
    # These strings isolate punctuation handling (no plural forms involved).
    assert normalize_query("Die casting porosity?!") == "die casting porosity"
    # Apostrophe is punctuation -> removed, leaving a space ("what s").
    assert normalize_query("what's porosity?") == "what s porosity"
    assert normalize_query("aluminum-die-casting") == "aluminum die casting"


def test_whitespace_normalization():
    assert normalize_query("  why   am i   getting   porosity  ") == "why am i getting porosity"
    assert normalize_query("porosity\nin\taluminum die casting") == "porosity in aluminum die casting"


def test_conservative_plural_normalization():
    # Regular safe plurals.
    assert normalize_query("die casting defects") == "die casting defect"
    assert normalize_query("aluminum die casting cracks") == "aluminum die casting crack"
    assert normalize_query("what causes porosities") == "what cause porosity"
    assert normalize_query("the processes fail") == "the process fail"
    assert normalize_query("boxes of parts") == "box of part"
    # Words that are NOT plurals must be left intact (no aggressive stemming).
    assert normalize_query("trapped gas") == "trapped gas"  # 'gas' must NOT become 'ga'
    assert normalize_query("the process") == "the process"  # 'process' must NOT become 'proces'
    assert normalize_query("glass surface") == "glass surface"


def test_plural_normalization_enables_match():
    # "die casting defect" (candidate) vs "die casting defects" (GSC) -> match.
    cands = ["die casting defect"]
    rows = [_gsc_row("die casting defects", impressions=40)]
    _nc, matched_norm, ev = match_candidates(cands, rows)
    assert matched_norm == ["die casting defect"]
    assert len(ev) == 1


# --------------------------------------------------------------------------- #
# 4. Matching semantics                                                        #
# --------------------------------------------------------------------------- #

def test_exact_normalized_match():
    cands = ["porosity in aluminum die casting"]
    rows = [_gsc_row("porosity in aluminum die casting", impressions=200)]
    _nc, matched_norm, ev = match_candidates(cands, rows)
    assert matched_norm == ["porosity in aluminum die casting"]
    assert ev[0].impressions == 200


def test_non_match_when_no_gsc_row():
    cands = ["porosity in aluminum die casting"]
    rows = [_gsc_row("totally different query", impressions=200)]
    _nc, matched_norm, ev = match_candidates(cands, rows)
    assert matched_norm == []
    assert ev == []


def test_substring_false_positive_rejected():
    # Candidate "die casting porosity" must NOT match GSC "casting porosity".
    cands = ["die casting porosity"]
    rows = [_gsc_row("casting porosity", impressions=999)]
    _nc, matched_norm, ev = match_candidates(cands, rows)
    assert matched_norm == []
    assert ev == []


def test_fuzzy_match_rejected():
    # Different word order + spelling variant -> not an exact normalized match.
    cands = ["porosity in aluminum die casting"]
    rows = [_gsc_row("aluminium die casting porosity", impressions=999)]
    _nc, matched_norm, ev = match_candidates(cands, rows)
    assert matched_norm == []
    assert ev == []


def test_multiple_gsc_rows_retained():
    cands = ["porosity in aluminum die casting"]
    rows = [
        _gsc_row("porosity in aluminum die casting", page="https://a.com/p1", impressions=10),
        _gsc_row("porosity in aluminum die casting", page="https://a.com/p2", impressions=20),
        _gsc_row("other query", impressions=50),
    ]
    _nc, matched_norm, ev = match_candidates(cands, rows)
    assert matched_norm == ["porosity in aluminum die casting"]
    # Both matching rows retained (no aggregation).
    assert len(ev) == 2
    assert sorted(e.page for e in ev) == ["https://a.com/p1", "https://a.com/p2"]


# --------------------------------------------------------------------------- #
# 5. Search-demand status semantics                                            #
# --------------------------------------------------------------------------- #

def test_status_validated_when_match_with_impressions():
    b = _brief(
        core="porosity in aluminum die casting",
        title="T",
        supporting=[],
    )
    rows = [_gsc_row("porosity in aluminum die casting", impressions=120)]
    se = build_search_evidence(b, rows, gsc_status="available")
    assert se.status == SearchDemandStatus.VALIDATED
    assert se.gsc_available is True
    assert se.matched_queries == ["porosity in aluminum die casting"]


def test_status_not_validated_when_no_match():
    b = _brief(core="porosity in aluminum die casting", title="T", supporting=[])
    # GSC successfully queried but returns unrelated data.
    rows = [_gsc_row("unrelated query", impressions=500)]
    se = build_search_evidence(b, rows, gsc_status="available")
    assert se.status == SearchDemandStatus.NOT_VALIDATED
    assert se.gsc_available is True
    assert se.matched_queries == []


def test_status_not_validated_when_empty_gsc_data():
    b = _brief(core="porosity in aluminum die casting", title="T", supporting=[])
    se = build_search_evidence(b, [], gsc_status="available")
    assert se.status == SearchDemandStatus.NOT_VALIDATED
    assert se.gsc_available is True


def test_status_unknown_when_disabled():
    b = _brief(core="porosity in aluminum die casting", title="T", supporting=[])
    rows = [_gsc_row("porosity in aluminum die casting", impressions=120)]
    se = build_search_evidence(b, rows, gsc_status="disabled")
    assert se.status == SearchDemandStatus.UNKNOWN
    assert se.gsc_available is False


def test_status_unknown_when_unavailable():
    b = _brief(core="porosity in aluminum die casting", title="T", supporting=[])
    rows = [_gsc_row("porosity in aluminum die casting", impressions=120)]
    se = build_search_evidence(b, rows, gsc_status="unavailable")
    assert se.status == SearchDemandStatus.UNKNOWN
    assert se.gsc_available is False


def test_status_not_validated_when_match_has_zero_impressions():
    b = _brief(core="porosity in aluminum die casting", title="T", supporting=[])
    rows = [_gsc_row("porosity in aluminum die casting", impressions=0)]
    se = build_search_evidence(b, rows, gsc_status="available")
    # A zero-impression match is not positive first-party demand evidence.
    assert se.status == SearchDemandStatus.NOT_VALIDATED
    assert se.matched_queries == ["porosity in aluminum die casting"]


# --------------------------------------------------------------------------- #
# 6. Evidence model integrity                                                  #
# --------------------------------------------------------------------------- #

def test_impressions_preserved_and_never_relabeled():
    b = _brief(core="porosity in aluminum die casting", title="T", supporting=[])
    rows = [_gsc_row("porosity in aluminum die casting", clicks=7, impressions=318, ctr=0.1234, position=4.5)]
    se = build_search_evidence(b, rows, gsc_status="available")
    ev = se.evidence[0]
    assert ev.impressions == 318
    assert ev.clicks == 7
    assert ev.ctr == 0.1234
    assert ev.position == 4.5
    # The field is named impressions, never search volume.
    assert "impressions" in ev.to_dict()
    assert "search_volume" not in ev.to_dict()


def test_no_search_volume_field_anywhere():
    b = _brief(core="q", title="T", supporting=[])
    se = build_search_evidence(b, [], gsc_status="available")
    assert "search_volume" not in se.to_dict()
    assert "monthly_search_volume" not in se.to_dict()
    assert "CPC" not in se.to_dict()
    assert "keyword_difficulty" not in se.to_dict()
    assert "SERP_competition" not in se.to_dict()
    import app.radar.search_evidence as se_mod
    src = open(se_mod.__file__, encoding="utf-8").read()
    for forbidden in ("search_volume", "monthly_search_volume", "keyword_difficulty", "SERP_competition"):
        assert forbidden not in src, f"forbidden term {forbidden!r} present in module"


def test_evidence_candidate_queries_recorded():
    b = _brief(
        core="porosity in aluminum die casting",
        title="Aluminum Die Casting Porosity: Causes, Prevention and Solutions",
        supporting=["what causes porosity", "how to prevent porosity"],
    )
    rows = [_gsc_row("porosity in aluminum die casting", impressions=10)]
    se = build_search_evidence(b, rows, gsc_status="available")
    # 4 candidate sources: core_question + recommended_title + 2 supporting,
    # all normalized & deduped (none collide here).
    assert len(se.candidate_queries) == 4
    assert "porosity in aluminum die casting" in se.candidate_queries


# --------------------------------------------------------------------------- #
# 7. query_gsc_rows status detection (adapter wrapper)                         #
# --------------------------------------------------------------------------- #

def _fake_transport(responder):
    calls = []
    def fake(url, method="POST", headers=None, data=b"", timeout=30.0):
        body = json.loads(data.decode("utf-8")) if data else {}
        status, payload = responder(body)
        calls.append(status)
        return status, payload
    fake.calls = calls
    return fake


def test_query_gsc_rows_disabled_does_not_invoke_transport():
    adapter = GSCAdapter("sc-domain:alumcasting.com", enabled=False)
    rows, status, dstart, dend = query_gsc_rows(adapter)
    assert rows == []
    assert status == "disabled"
    # Because the adapter is disabled, query_gsc_rows returns early and never
    # wraps the transport with a recorder (no .calls attribute is attached).
    assert not hasattr(adapter.transport, "calls")


def test_query_gsc_rows_available_on_200():
    def responder(body):
        return 200, json.dumps({"rows": [_gsc_row("porosity", impressions=50)]})
    adapter = GSCAdapter("sc-domain:x", transport=_fake_transport(responder), enabled=True)
    rows, status, dstart, dend = query_gsc_rows(adapter)
    assert status == "available"
    assert len(rows) == 1


def test_query_gsc_rows_unavailable_on_401():
    def responder(body):
        return 401, json.dumps({"error": "unauthorized"})
    adapter = GSCAdapter("sc-domain:x", transport=_fake_transport(responder), enabled=True)
    rows, status, dstart, dend = query_gsc_rows(adapter)
    assert status == "unavailable"
    assert rows == []


def test_query_gsc_rows_unavailable_on_transport_failure():
    def exploding(url, method="POST", headers=None, data=b"", timeout=30.0):
        raise TimeoutError("simulated timeout")
    adapter = GSCAdapter("sc-domain:x", transport=exploding, enabled=True)
    rows, status, dstart, dend = query_gsc_rows(adapter)
    assert status == "unavailable"
    assert rows == []


def test_query_gsc_rows_restores_transport():
    def responder(body):
        return 200, json.dumps({"rows": []})
    base = _fake_transport(responder)
    adapter = GSCAdapter("sc-domain:x", transport=base, enabled=True)
    query_gsc_rows(adapter)
    # The temporary recorder wrapper must be removed; the original injected
    # transport is restored (no recorder leakage).
    assert adapter.transport is base


# --------------------------------------------------------------------------- #
# 8. Attachment: no score / ContentBrief changes                              #
# --------------------------------------------------------------------------- #

def test_attach_does_not_change_scores_or_brief():
    n = _scored_problem()
    before = {
        "problem_score": n.problem_score,
        "heat_score": n.heat_score,
        "opportunity_score": n.opportunity_score,
        "opportunity_rank": n.opportunity_rank,
        "score_reasons": list(n.score_reasons),
    }
    brief_before = n.content_brief.to_dict()
    # A GSC row that exactly matches one of the brief's normalized candidate
    # queries (a supporting question) so the status resolves to validated.
    rows = [_gsc_row("what causes porosity in aluminum die casting", impressions=10)]
    attach_search_evidence_to_signals([n], rows, gsc_status="available")
    assert n.problem_score == before["problem_score"]
    assert n.heat_score == before["heat_score"]
    assert n.opportunity_score == before["opportunity_score"]
    assert n.opportunity_rank == before["opportunity_rank"]
    assert n.score_reasons == before["score_reasons"]
    # ContentBrief untouched.
    assert n.content_brief.to_dict() == brief_before
    # Evidence attached.
    assert n.search_evidence is not None
    assert n.search_evidence.status == SearchDemandStatus.VALIDATED


def test_attach_sets_none_for_non_problem_signals():
    n = NormalizedSignal(
        id="x", source="r", source_type="reddit", topic=None, title="news",
        text="market outlook", url="https://x.com", signal_type=SignalType.NEWS,
        is_problem_signal=False,
    )
    attach_search_evidence_to_signals([n], [], gsc_status="available")
    assert n.search_evidence is None


def test_attach_unknown_when_disabled():
    n = _scored_problem()
    attach_search_evidence_to_signals([n], [], gsc_status="disabled")
    assert n.search_evidence.status == SearchDemandStatus.UNKNOWN


# --------------------------------------------------------------------------- #
# 9. Pipeline integration (existing behavior unchanged)                        #
# --------------------------------------------------------------------------- #

def test_pipeline_dry_run_counts_unchanged(tmp_path):
    from app.radar.pipeline import run_pipeline

    report = run_pipeline(out_dir=str(tmp_path), dry_run=True)
    # Existing deterministic fixture counts must be preserved (9 raw / 5 relevant / 4 problem).
    assert report.total_raw == 9
    assert report.total_relevant == 5
    assert report.total_problem == 4


def test_pipeline_gcs_disabled_no_real_request(tmp_path):
    from app.radar.pipeline import run_pipeline

    cfg = load_config()
    assert cfg["gsc"]["enabled"] is False  # safety control intact
    report = run_pipeline(out_dir=str(tmp_path), dry_run=True)
    data = json.load(open(tmp_path / "reports" / "content_opportunity_report.json", encoding="utf-8"))
    problem_signals = [s for s in data["signals"] if s.get("content_brief")]
    # Every problem signal carries search evidence, and because GSC is disabled
    # it must be 'unknown' (proving the disabled gate, not a real query, ran).
    assert problem_signals
    for s in problem_signals:
        se = s.get("search_evidence")
        assert se is not None
        assert se["search_demand_status"] == "unknown"
        assert se["gsc_available"] is False


def test_pipeline_csv_includes_search_evidence_columns(tmp_path):
    import csv

    from app.radar.pipeline import run_pipeline

    run_pipeline(out_dir=str(tmp_path), dry_run=True)
    with open(tmp_path / "reports" / "content_opportunity_report.csv", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        header = reader.fieldnames
    for col in ("search_demand_status", "gsc_available", "matched_gsc_queries", "gsc_evidence_rows"):
        assert col in header


# --------------------------------------------------------------------------- #
# 10. No embedded credentials in module                                         #
# --------------------------------------------------------------------------- #

def test_no_embedded_credentials_in_module():
    import app.radar.search_evidence as se_mod

    suspicious = ("ya29", "AIza", "private_key", "client_secret", "BEGIN PRIVATE KEY",
                  "refresh_token", "Bearer ")
    src = open(se_mod.__file__, encoding="utf-8").read()
    for s in suspicious:
        assert s not in src, f"module contains suspicious string {s!r}"
