"""Phase 1.5D offline tests: Site Coverage & Content Gap Validation.

Every test is deterministic and fully offline:
  * the sitemap transport is faked (no network),
  * GSC page evidence is injected directly as GSCSearchEvidence objects,
  * no credentials, secrets, OAuth, or GitHub Actions are touched.

These tests also encode the Phase 1.5D non-negotiable safety principles:
  * NO INVENTORY != NO CONTENT
  * NO GSC ROW != NO CONTENT
  * Page existence != problem coverage
  * Coverage evidence is strictly separate from search-demand evidence
  * No score / ContentBrief / ranking is ever mutated by this layer
"""

from __future__ import annotations

from app.radar.models import NormalizedSignal
from app.radar.search_evidence import GSCSearchEvidence, SearchEvidence
from app.radar.site_coverage import (
    TRANSPORT_FAILURE,
    URLNormalizer,
    SiteInventoryAdapter,
    build_site_coverage_adapter,
    build_url_normalizer,
    compute_overlap,
    has_problem_term,
    match_candidate_to_page,
    build_site_coverage,
    attach_site_coverage_to_signals,
    CoverageStatus,
    ContentGapStatus,
    CoverageConfidence,
    SiteCoverageEvidence,
    MatchedPage,
    ProblemCoverageEvidence,
)

# --------------------------------------------------------------------------- #
# Helpers                                                                      #
# --------------------------------------------------------------------------- #


class FakeBrief:
    """Minimal ContentBrief-shaped object with a to_dict() (mirrors the real
    ContentBrief used by the pipeline) and the attributes read by
    extract_candidate_queries."""

    def __init__(self, core="how to fix aluminum die casting porosity",
                 title="Fixing Aluminum Die Casting Porosity", supporting=None):
        self.core_question = core
        self.recommended_title = title
        self.supporting_questions = supporting or []

    def to_dict(self):
        return {
            "core_question": self.core_question,
            "recommended_title": self.recommended_title,
            "supporting_questions": list(self.supporting_questions),
        }


def make_brief(core="how to fix aluminum die casting porosity",
               title="Fixing Aluminum Die Casting Porosity",
               supporting=None):
    """A ContentBrief-shaped object (has to_dict() and the brief attributes)."""
    return FakeBrief(core=core, title=title, supporting=supporting)


def make_signal(is_problem=True, brief=None, **kw):
    sig = NormalizedSignal(
        id=kw.get("id", "t1"),
        source=kw.get("source", "reddit"),
        source_type=kw.get("source_type", "forum"),
        topic=kw.get("topic", "die_casting"),
        title=kw.get("title", "Porosity in die casting"),
        text=kw.get("text", "We see porosity in our aluminum die cast parts."),
        url=kw.get("url", "https://example.com/x"),
        is_problem_signal=is_problem,
        problem_score=kw.get("problem_score", 72.0),
        heat_score=kw.get("heat_score", 40.0),
        opportunity_score=kw.get("opportunity_score", 55.0),
    )
    sig.content_brief = brief if brief is not None else make_brief()
    return sig


def make_gsc_row(page="", position=5.0, impressions=120, clicks=6, ctr=0.05,
                 query="aluminum die casting porosity"):
    return GSCSearchEvidence(
        query=query,
        normalized_query=query,
        page=page,
        position=position,
        impressions=impressions,
        clicks=clicks,
        ctr=ctr,
    )


SINGLE_SITEMAP = """<?xml version="1.0" encoding="UTF-8"?>
<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
  <url><loc>https://alumcasting.com/</loc></url>
  <url><loc>https://alumcasting.com/die-casting</loc></url>
  <url><loc>https://alumcasting.com/cnc-machining</loc></url>
</urlset>"""

INDEX_SITEMAP = """<?xml version="1.0" encoding="UTF-8"?>
<sitemapindex xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
  <sitemap><loc>https://alumcasting.com/s1.xml</loc></sitemap>
  <sitemap><loc>https://alumcasting.com/s2.xml</loc></sitemap>
</sitemapindex>"""

SUB_A = """<?xml version="1.0" encoding="UTF-8"?>
<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
  <url><loc>https://alumcasting.com/a1</loc></url>
  <url><loc>https://alumcasting.com/a2</loc></url>
</urlset>"""

SUB_B = """<?xml version="1.0" encoding="UTF-8"?>
<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
  <url><loc>https://alumcasting.com/b1</loc></url>
  <url><loc>https://alumcasting.com/a2</loc></url>
</urlset>"""


def transport_returning(status, body):
    def _t(url, timeout=15.0):
        return status, body
    return _t


def index_transport(root_url="https://alumcasting.com/sitemap.xml"):
    def _t(url, timeout=15.0):
        if url == root_url:
            return 200, INDEX_SITEMAP
        if url.endswith("s1.xml"):
            return 200, SUB_A
        if url.endswith("s2.xml"):
            return 200, SUB_B
        return 404, ""
    return _t


# --------------------------------------------------------------------------- #
# URL normalization (canonical policy)                                         #
# --------------------------------------------------------------------------- #


def test_normalize_strips_www_lowercases_host_and_trailing_slash():
    n = URLNormalizer(canonical_host="alumcasting.com")
    assert n.normalize("https://www.AlumCasting.com/foo/") == "https://alumcasting.com/foo"


def test_normalize_scheme_less_input_gets_https():
    n = URLNormalizer()
    assert n.normalize("alumcasting.com/foo") == "https://alumcasting.com/foo"


def test_normalize_drops_default_port_80():
    n = URLNormalizer()
    assert n.normalize("http://alumcasting.com:80/bar") == "http://alumcasting.com/bar"


def test_normalize_drops_default_port_443():
    n = URLNormalizer()
    assert n.normalize("https://alumcasting.com:443/bar") == "https://alumcasting.com/bar"


def test_normalize_keeps_root_slash():
    n = URLNormalizer()
    assert n.normalize("https://alumcasting.com/") == "https://alumcasting.com/"
    assert n.normalize("https://alumcasting.com") == "https://alumcasting.com"


def test_normalize_drops_query_and_fragment():
    n = URLNormalizer()
    out = n.normalize("https://alumcasting.com/foo?utm=1#section")
    assert out == "https://alumcasting.com/foo"


def test_normalize_decodes_then_requotes_percent_encoding():
    n = URLNormalizer()
    out = n.normalize("https://alumcasting.com/a%20b")
    assert out == "https://alumcasting.com/a%20b"


def test_normalize_is_idempotent():
    n = URLNormalizer()
    # Host is lowercased + www stripped + trailing slash removed; path stays
    # case-sensitive (URLs are case-sensitive), so we use a lowercase path.
    url = "https://www.AlumCasting.com/products/"
    once = n.normalize(url)
    twice = n.normalize(once)
    assert once == twice == "https://alumcasting.com/products"


def test_normalize_empty_input_is_safe():
    n = URLNormalizer()
    assert n.normalize("") == ""
    assert n.normalize("   ") == ""


def test_build_url_normalizer_reads_config():
    n = build_url_normalizer({
        "site_coverage": {
            "canonical_host": "example.com",
            "strip_www": False,
            "strip_trailing_slash": False,
        }
    })
    # www kept, trailing slash kept, canonical_host used for reference only.
    assert n.normalize("https://www.example.com/foo/") == "https://www.example.com/foo/"
    # Defaults when config absent.
    n2 = build_url_normalizer({})
    assert n2.strip_www is True
    assert n2.strip_trailing_slash is True


# --------------------------------------------------------------------------- #
# Site inventory adapter (sitemap, faked transport)                            #
# --------------------------------------------------------------------------- #


def test_sitemap_adapter_single_success():
    a = SiteInventoryAdapter("https://alumcasting.com/sitemap.xml",
                             transport=transport_returning(200, SINGLE_SITEMAP))
    urls, status = a.collect()
    assert status == "available"
    assert "https://alumcasting.com/die-casting" in urls
    assert "https://alumcasting.com/cnc-machining" in urls
    assert len(urls) == 3


def test_sitemap_adapter_nested_index():
    a = SiteInventoryAdapter("https://alumcasting.com/sitemap.xml",
                             transport=index_transport())
    urls, status = a.collect()
    assert status == "available"
    # All pages from both sub-sitemaps, de-duplicated (a2 appears in both).
    assert "https://alumcasting.com/a1" in urls
    assert "https://alumcasting.com/a2" in urls
    assert "https://alumcasting.com/b1" in urls
    assert urls.count("https://alumcasting.com/a2") == 1
    assert len(urls) == 3


def test_sitemap_adapter_404_unavailable():
    a = SiteInventoryAdapter("https://alumcasting.com/sitemap.xml",
                             transport=transport_returning(404, ""))
    urls, status = a.collect()
    assert status == "unavailable"
    assert urls == []


def test_sitemap_adapter_5xx_unavailable():
    a = SiteInventoryAdapter("https://alumcasting.com/sitemap.xml",
                             transport=transport_returning(503, "down"))
    urls, status = a.collect()
    assert status == "unavailable"
    assert urls == []


def test_sitemap_adapter_transport_failure_unavailable():
    a = SiteInventoryAdapter("https://alumcasting.com/sitemap.xml",
                             transport=transport_returning(TRANSPORT_FAILURE, ""))
    urls, status = a.collect()
    assert status == "unavailable"
    assert urls == []


def test_sitemap_adapter_malformed_xml_unavailable():
    a = SiteInventoryAdapter("https://alumcasting.com/sitemap.xml",
                             transport=transport_returning(200, "<not-xml>"))
    urls, status = a.collect()
    assert status == "unavailable"
    assert urls == []


def test_sitemap_adapter_empty_body_unavailable():
    a = SiteInventoryAdapter("https://alumcasting.com/sitemap.xml",
                             transport=transport_returning(200, ""))
    urls, status = a.collect()
    assert status == "unavailable"
    assert urls == []


def test_sitemap_adapter_transport_raises_unavailable():
    def _raise(url, timeout=15.0):
        raise RuntimeError("DNS/TLS failure")
    a = SiteInventoryAdapter("https://alumcasting.com/sitemap.xml", transport=_raise)
    urls, status = a.collect()
    assert status == "unavailable"
    assert urls == []


def test_build_site_coverage_adapter_uses_fake_transport():
    a = build_site_coverage_adapter({"site_coverage": {"sitemap_url": "https://x/s.xml"}},
                                    transport=transport_returning(200, SINGLE_SITEMAP))
    urls, status = a.collect()
    assert status == "available"
    assert len(urls) == 3


# --------------------------------------------------------------------------- #
# Deterministic matching                                                       #
# --------------------------------------------------------------------------- #


def test_compute_overlap_exact():
    assert compute_overlap("aluminum die casting porosity",
                           "aluminum die casting porosity") == 1.0


def test_compute_overlap_boundary_at_threshold():
    # 3 shared of 5 unique tokens -> 0.60 exactly (>= threshold).
    o = compute_overlap("aluminum die casting porosity", "aluminum die casting defect")
    assert o == 0.6


def test_compute_overlap_below_threshold():
    assert compute_overlap("aluminum die casting porosity", "cnc machining tolerance") == 0.0


def test_has_problem_term_true():
    assert has_problem_term({"porosity", "fix"}, {"porosity"}) is True


def test_has_problem_term_false():
    assert has_problem_term({"cnc", "tolerance"}, {"porosity"}) is False


def test_match_candidate_exact():
    tier, overlap, problem = match_candidate_to_page(
        "fix porosity", "fix porosity", 0.6, {"porosity"})
    assert tier == "exact"
    assert overlap == 1.0
    assert problem is True


def test_match_candidate_token_overlap():
    tier, overlap, problem = match_candidate_to_page(
        "aluminum die casting porosity", "aluminum die casting defect", 0.6, {"defect"})
    assert tier == "token_overlap"
    assert overlap >= 0.6
    assert problem is True


def test_match_candidate_below_threshold_no_match():
    tier, overlap, problem = match_candidate_to_page(
        "aluminum die casting porosity", "cnc machining tolerance", 0.6, set())
    assert tier is None
    assert overlap == 0.0
    assert problem is False


# --------------------------------------------------------------------------- #
# Coverage classification (CASES A-H)                                          #
# --------------------------------------------------------------------------- #


def test_case_a_no_inventory_no_gsc_is_unknown():
    sig = make_signal()
    ev = build_site_coverage(sig, inventory_urls=[], inventory_status="unavailable",
                             gsc_evidence_rows=[], cfg=None)
    assert ev.site_coverage == CoverageStatus.UNKNOWN
    assert ev.content_gap_status == ContentGapStatus.UNKNOWN
    assert ev.coverage_confidence == CoverageConfidence.UNKNOWN
    assert ev.coverage_sources == []
    assert ev.problem_coverage_evidence.page_existence == "unknown"
    assert ev.problem_coverage_evidence.topical_match == "unknown"
    assert ev.problem_coverage_evidence.problem_match == "unknown"


def test_case_b_sitemap_available_no_gsc_is_unknown_not_false():
    sig = make_signal()
    ev = build_site_coverage(
        sig,
        inventory_urls=["https://alumcasting.com/die-casting"],
        inventory_status="available",
        gsc_evidence_rows=[],
        cfg=None,
    )
    # Sitemap gives URL existence ONLY. Without page content/title access we must
    # NOT infer topic/problem match from the URL slug -> unknown. Page existence
    # is "unknown" here, NOT "false" (we never deny content we cannot inspect).
    assert ev.site_coverage == CoverageStatus.UNKNOWN
    assert ev.coverage_sources == ["sitemap"]
    assert ev.problem_coverage_evidence.page_existence == "unknown"
    assert ev.problem_coverage_evidence.topical_match == "unknown"
    assert ev.problem_coverage_evidence.problem_match == "unknown"


def test_case_c_gsc_single_strong_is_covered():
    sig = make_signal()
    row = make_gsc_row(page="https://alumcasting.com/die-casting-porosity", position=5.0)
    ev = build_site_coverage(sig, inventory_urls=[], inventory_status="unavailable",
                             gsc_evidence_rows=[row], cfg=None)
    assert ev.site_coverage == CoverageStatus.STRONG
    assert ev.content_gap_status == ContentGapStatus.COVERED
    assert ev.coverage_confidence == CoverageConfidence.HIGH
    assert ev.matched_pages[0].performance == "strong"
    assert ev.matched_pages[0].source == "gsc_page"
    assert ev.matched_pages[0].match_tier == "gsc_query_page"


def test_case_d_gsc_single_weak_is_refresh_opportunity():
    sig = make_signal()
    row = make_gsc_row(page="https://alumcasting.com/die-casting-porosity", position=25.0)
    ev = build_site_coverage(sig, inventory_urls=[], inventory_status="unavailable",
                             gsc_evidence_rows=[row], cfg=None)
    assert ev.site_coverage == CoverageStatus.EXISTING
    assert ev.content_gap_status == ContentGapStatus.REFRESH_OPPORTUNITY
    assert ev.coverage_confidence == CoverageConfidence.HIGH
    assert ev.matched_pages[0].performance == "weak"


def test_case_f_multiple_competing_pages_is_partial_gap():
    sig = make_signal()
    r1 = make_gsc_row(page="https://alumcasting.com/a", position=5.0)
    r2 = make_gsc_row(page="https://alumcasting.com/b", position=8.0)
    ev = build_site_coverage(sig, inventory_urls=[], inventory_status="unavailable",
                             gsc_evidence_rows=[r1, r2], cfg=None)
    assert ev.site_coverage == CoverageStatus.PARTIAL
    assert ev.content_gap_status == ContentGapStatus.PARTIAL_GAP
    assert ev.coverage_confidence == CoverageConfidence.MEDIUM
    assert ev.multiple_competing_pages is True
    assert len(ev.matched_pages) == 2


def test_case_g_problem_signal_without_brief_returns_none():
    sig = make_signal(brief=None)
    sig.content_brief = None
    ev = build_site_coverage(sig, inventory_urls=[], inventory_status="available",
                             gsc_evidence_rows=[], cfg=None)
    assert ev is None


def test_case_h_non_problem_signal_returns_none():
    sig = make_signal(is_problem=False)
    ev = build_site_coverage(sig, inventory_urls=[], inventory_status="available",
                             gsc_evidence_rows=[], cfg=None)
    assert ev is None


def test_case_i_gsc_rows_without_page_are_ignored():
    sig = make_signal()
    # page="" -> not page evidence; falls through to inventory logic.
    row = make_gsc_row(page="", position=5.0)
    ev = build_site_coverage(sig, inventory_urls=[], inventory_status="unavailable",
                             gsc_evidence_rows=[row], cfg=None)
    assert ev.site_coverage == CoverageStatus.UNKNOWN
    assert ev.matched_pages == []


def test_position_threshold_is_configurable():
    # Custom position_weak_threshold=10 makes position 15 "weak" (refresh).
    sig = make_signal()
    row = make_gsc_row(page="https://alumcasting.com/p", position=15.0)
    ev = build_site_coverage(
        sig, inventory_urls=[], inventory_status="unavailable",
        gsc_evidence_rows=[row],
        cfg={"site_coverage": {"position_weak_threshold": 10.0}},
    )
    assert ev.matched_pages[0].performance == "weak"
    assert ev.content_gap_status == ContentGapStatus.REFRESH_OPPORTUNITY


# --------------------------------------------------------------------------- #
# Unknown-safety invariant                                                     #
# --------------------------------------------------------------------------- #


def test_unknown_never_asserts_no_content():
    # The unknown verdict must NEVER be emitted as "no content exists".
    sig = make_signal()
    ev = build_site_coverage(sig, inventory_urls=[], inventory_status="unavailable",
                             gsc_evidence_rows=[], cfg=None)
    assert ev.site_coverage == CoverageStatus.UNKNOWN
    # Explicit guard: page_existence is "unknown", never "false".
    assert ev.problem_coverage_evidence.page_existence == "unknown"


def test_build_site_coverage_does_not_mutate_signal():
    sig = make_signal()
    ps = sig.problem_score
    os_ = sig.opportunity_score
    cb = sig.content_brief
    build_site_coverage(sig, inventory_urls=[], inventory_status="unavailable",
                        gsc_evidence_rows=[], cfg=None)
    assert sig.problem_score == ps
    assert sig.opportunity_score == os_
    assert sig.content_brief is cb


# --------------------------------------------------------------------------- #
# GSC page-evidence reuse (Phase 1.5C -> 1.5D)                                #
# --------------------------------------------------------------------------- #


def test_attach_reads_search_evidence_rows_with_page():
    sig = make_signal()
    se = SearchEvidence(
        status=__import__("app.radar.search_evidence", fromlist=["SearchDemandStatus"])
        .SearchDemandStatus.VALIDATED,
        evidence=[make_gsc_row(page="https://alumcasting.com/die-casting-porosity",
                               position=5.0)],
    )
    sig.search_evidence = se
    attach_site_coverage_to_signals([sig], inventory_urls=[],
                                    inventory_status="unavailable", cfg=None)
    assert isinstance(sig.site_coverage, SiteCoverageEvidence)
    assert sig.site_coverage.site_coverage == CoverageStatus.STRONG
    assert sig.site_coverage.matched_pages[0].url == "https://alumcasting.com/die-casting-porosity"


def test_attach_sets_none_for_non_problem_signals():
    sig = make_signal(is_problem=False)
    attach_site_coverage_to_signals([sig], inventory_urls=[],
                                    inventory_status="unavailable", cfg=None)
    assert sig.site_coverage is None


# --------------------------------------------------------------------------- #
# Pipeline integration (offline)                                               #
# --------------------------------------------------------------------------- #


def test_pipeline_dry_run_attaches_site_coverage_offline():
    from app.radar.pipeline import run_pipeline

    report = run_pipeline(dry_run=True, site_coverage_transport=lambda u, t=15.0: (404, ""))
    # Documented dry-run shape: 9 raw / 5 relevant / 4 problem.
    assert report.total_raw == 9
    assert report.total_relevant == 5
    assert report.total_problem == 4

    problem_with_coverage = 0
    non_problem_none = 0
    for s in report.signals:
        if s.is_problem_signal:
            assert isinstance(s.site_coverage, SiteCoverageEvidence)
            # No GSC in dry-run + stubbed sitemap => unknown (safe).
            assert s.site_coverage.site_coverage == CoverageStatus.UNKNOWN
            problem_with_coverage += 1
        else:
            assert s.site_coverage is None
            non_problem_none += 1
    assert problem_with_coverage == 4


def test_pipeline_no_network_when_transport_none():
    # The default None transport in run_pipeline must not perform network I/O.
    from app.radar.pipeline import run_pipeline

    report = run_pipeline(dry_run=True)  # site_coverage_transport defaults to None
    assert report.total_problem == 4
    for s in report.signals:
        if s.is_problem_signal:
            assert s.site_coverage.site_coverage == CoverageStatus.UNKNOWN


# --------------------------------------------------------------------------- #
# Regression: schemas and non-mutation                                         #
# --------------------------------------------------------------------------- #


def test_normalized_signal_dict_keys_are_prior_plus_site_coverage():
    sig = make_signal()
    keys = set(sig.to_dict().keys())
    expected_prior = {
        "id", "source", "source_type", "topic", "title", "text", "url", "author",
        "published_at", "collected_at", "signal_type", "priority",
        "relevance_score", "matched_keywords", "engagement", "heat_score",
        "problem_score", "opportunity_score", "is_problem_signal",
        "opportunity_rank", "score_reasons", "content_brief", "search_evidence",
    }
    # Exactly the prior key set plus the single new site_coverage key.
    assert keys == expected_prior | {"site_coverage"}
    # The new key is None for a signal with no coverage attached yet.
    assert sig.to_dict()["site_coverage"] is None


def test_content_brief_untouched_after_attach():
    sig = make_signal()
    before = sig.content_brief.to_dict() if hasattr(sig.content_brief, "to_dict") else dict(sig.content_brief)
    attach_site_coverage_to_signals([sig], inventory_urls=[],
                                    inventory_status="unavailable", cfg=None)
    after = sig.content_brief.to_dict() if hasattr(sig.content_brief, "to_dict") else dict(sig.content_brief)
    assert before == after


def test_default_sitemap_transport_is_callable():
    # Sanity: the live transport exists and is importable (never called offline).
    from app.radar.site_coverage import default_sitemap_transport
    assert callable(default_sitemap_transport)
