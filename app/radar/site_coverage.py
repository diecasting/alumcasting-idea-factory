"""Site Coverage & Content Gap Validation (Phase 1.5D).

Deterministic, explainable, failure-tolerant. No AI, embeddings, fuzzy matching,
LLM semantic matching, paid APIs, or SERP APIs.

This module:
  * normalizes URLs under a configurable canonical policy,
  * fetches & parses sitemap.xml / sitemap index (injectable transport),
  * extracts candidate page signals from EXISTING ContentBrief / signal data,
  * matches candidates to inventory pages deterministically
    (exact normalized match, controlled token overlap, explicit problem-term
    matching),
  * reuses Phase 1.5C ``GSCSearchEvidence.page`` for GSC page evidence,
  * classifies ``site_coverage`` / ``content_gap_status`` / ``coverage_confidence``,
  * attaches a ``SiteCoverageEvidence`` to NormalizedSignal (sibling of
    ``content_brief`` and ``search_evidence``).

It NEVER modifies ``problem_score`` / ``heat_score`` / ``opportunity_score`` /
``opportunity_rank`` / ``score_reasons`` / ``ContentBrief`` / search-demand
evidence. GSC remains OPTIONAL and DISABLED by default; site coverage resolves
to ``"unknown"`` whenever inventory or GSC evidence is unavailable.

Key safety principles (from the Phase 1.5D spec):
  * NO INVENTORY != NO CONTENT      (absence of inventory is not absence of a page)
  * NO GSC ROW != NO CONTENT        (absence of GSC data is not absence of a page)
  * NO IMPRESSIONS != ZERO DEMAND   (handled by Phase 1.5C)
  * Page existence != problem coverage (a URL existing is not a page answering
    the specific problem)
  * Coverage evidence is kept strictly separate from search-demand evidence.
"""

from __future__ import annotations

import re
import ssl
import time
import unicodedata
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable, Optional
from urllib.parse import quote, unquote, urlsplit, urlunsplit

# Reuse the existing deterministic query normalization and candidate extraction
# from Phase 1.5C. Do NOT re-implement normalization.
from app.radar.search_evidence import dedupe_candidates, extract_candidate_queries

# Status returned by the transport when the request itself fails (mirrors the
# GSC adapter's TRANSPORT_FAILURE sentinel).
TRANSPORT_FAILURE = -1

# Transport contract: (url, timeout) -> (status_code, body_str). A status of
# 200 with a non-empty body is "success"; anything else is a failure to be
# treated as inventory unavailable (non-blocking).
SiteMapTransport = Callable[[str, float], tuple[int, str]]

# Sitemap XML namespace (sitemaps.org schema). We match on LOCAL tag names so we
# also tolerate sitemaps that omit the namespace.
_SITEMAP_NS = "{http://www.sitemaps.org/schemas/sitemap/0.9}"

# Manufacturing problem / defect vocabulary used for EXPLICIT problem-term
# matching. This is a curated, deterministic allowlist -- NOT stemming, NOT
# semantic similarity. Material and process words are included because the
# briefs themselves use them as problem context (e.g. "aluminum die casting
# porosity"); a page sharing these terms with a candidate is at least topically
# aligned. Split into strong defect terms and broader topic/material terms.
_DEFECT_TERMS = {
    # defects / failures
    "porosity", "porosit", "shrinkage", "warping", "warp", "flash",
    "cold", "shut", "blister", "cracking", "crack", "chatter", "peeling",
    "peel", "bubbles", "bubble", "surface", "finish", "roughness",
    "dimensional", "tolerance", "defect", "defects", "failure", "fail",
    "void", "voids", "inclusion", "inclusions", "misrun", "burn", "melt",
    "rupture", "adhesion", "orange", "misalignment", "ejector", "slug",
}
_TOPIC_TERMS = {
    # processes / topics
    "casting", "die", "machining", "cnc", "coating", "powder", "gating",
    "sprue", "runner", "vent", "cooling", "injection", "hpdc", "feed",
    "spindle", "welding", "anodizing",
    # materials
    "aluminum", "aluminium", "steel", "magnesium", "zinc", "titanium",
    "copper", "alloy", "brass", "bronze",
}
_PROBLEM_TERMS = _DEFECT_TERMS | _TOPIC_TERMS


# --------------------------------------------------------------------------- #
# URL normalization (canonical policy)                                        #
# --------------------------------------------------------------------------- #

class URLNormalizer:
    """Canonical URL normalization under a configurable policy.

    Steps: lowercase host; strip www prefix when configured; remove default
    ports (80/443); decode + re-quote percent-encoding; NFC-normalize path;
    remove query string and fragment; strip trailing slash (root "/" kept).
    """

    def __init__(
        self,
        canonical_host: str = "alumcasting.com",
        strip_www: bool = True,
        strip_trailing_slash: bool = True,
    ) -> None:
        self.canonical_host = (canonical_host or "").lower()
        self.strip_www = bool(strip_www)
        self.strip_trailing_slash = bool(strip_trailing_slash)

    def normalize(self, url: str) -> str:
        if not url:
            return ""
        raw = url.strip()
        try:
            parts = urlsplit(raw)
        except ValueError:
            return ""
        # Tolerate scheme-less input (e.g. "alumcasting.com/foo").
        if not parts.netloc:
            parts = urlsplit("https://" + raw)
        if not parts.netloc:
            return ""
        scheme = parts.scheme or "https"
        host = parts.netloc.lower()
        if self.strip_www and host.startswith("www."):
            host = host[4:]
        # Remove default ports.
        if ":" in host:
            h, _, port = host.partition(":")
            if port in ("80", "443"):
                host = h
        # Path: decode percent-encoding, NFC normalize, re-quote safely.
        path = parts.path or ""
        path = unquote(path)
        path = unicodedata.normalize("NFC", path)
        path = quote(path, safe="/")
        # Strip trailing slash but keep the root.
        if self.strip_trailing_slash and len(path) > 1 and path.endswith("/"):
            path = path.rstrip("/")
            if path == "":
                path = "/"
        # Rebuild WITHOUT query string or fragment.
        return urlunsplit((scheme, host, path, "", ""))


def build_url_normalizer(cfg: Optional[dict] = None) -> URLNormalizer:
    """Build a URLNormalizer from the ``[site_coverage]`` config (or defaults)."""
    sc = (cfg or {}).get("site_coverage", {}) if isinstance(cfg, dict) else {}
    return URLNormalizer(
        canonical_host=sc.get("canonical_host", "alumcasting.com"),
        strip_www=bool(sc.get("strip_www", True)),
        strip_trailing_slash=bool(sc.get("strip_trailing_slash", True)),
    )


# --------------------------------------------------------------------------- #
# Site inventory adapter (sitemap)                                             #
# --------------------------------------------------------------------------- #

def _build_ssl_context(ca_bundle_path: Optional[str] = None) -> ssl.SSLContext:
    """Build a TLS context with certificate verification ENABLED.

    Certificate verification is ALWAYS on (``CERT_REQUIRED`` + hostname check).
    This function never disables validation and never falls back to insecure
    HTTP. CA bundle resolution order:
      1. ``ca_bundle_path`` if provided (explicit override),
      2. certifi's bundled CA bundle if importable (covers environments whose
         system store is missing/empty, e.g. some managed Python builds),
      3. the system default store (``cafile=None``).
    """
    try:
        import certifi

        cafile = ca_bundle_path or certifi.where()
    except Exception:
        cafile = ca_bundle_path  # may be None -> system default store
    ctx = ssl.create_default_context(cafile=cafile)
    ctx.check_hostname = True
    ctx.verify_mode = ssl.CERT_REQUIRED
    return ctx


def default_sitemap_transport(
    url: str, timeout: float = 15.0, ca_bundle_path: Optional[str] = None
) -> tuple[int, str]:
    """Stdlib HTTPS GET transport for sitemap fetch. Returns (status, body).

    Non-blocking by design. TLS certificate verification is ENABLED (via certifi
    or the system CA bundle). HTTP errors return their status code (so callers
    can decide to skip), and any transport-level failure (timeout, DNS, TLS)
    returns (TRANSPORT_FAILURE, ""). No third-party HTTP library, no disabled
    verification, no insecure plaintext fallback.
    """
    import urllib.error
    import urllib.request

    req = urllib.request.Request(
        url, headers={"User-Agent": "alumcasting-idea-factory/1.0 (sitemap)"}
    )
    try:
        ctx = _build_ssl_context(ca_bundle_path)
        with urllib.request.urlopen(req, timeout=timeout, context=ctx) as resp:
            charset = resp.headers.get_content_charset() or "utf-8"
            return resp.getcode(), resp.read().decode(charset, errors="replace")
    except urllib.error.HTTPError as exc:
        try:
            body = exc.read().decode("utf-8", errors="replace")
        except Exception:
            body = ""
        return exc.code, body
    except Exception:
        return TRANSPORT_FAILURE, ""


class SiteInventoryAdapter:
    """Fetch & parse the site URL inventory from a sitemap (xml or index).

    Injectable transport; fully offline-testable. All failures (404, 5xx,
    timeout, DNS/TLS, malformed XML, empty sitemap) resolve to
    ``(urls=[], status="unavailable")`` rather than raising.
    """

    source_type = "site_inventory"

    def __init__(
        self,
        sitemap_url: str,
        transport: Optional[SiteMapTransport] = None,
        timeout: float = 15.0,
        max_depth: int = 3,
        max_retries: int = 1,
        retry_backoff: float = 0.5,
    ) -> None:
        self.sitemap_url = sitemap_url
        self.transport = transport or default_sitemap_transport
        self.timeout = timeout
        self.max_depth = max_depth
        # Bounded retry for TRANSIENT transport failures only. ``max_retries`` is
        # the number of *additional* attempts beyond the first (1 == 2 total).
        self.max_retries = max(0, int(max_retries))
        self.retry_backoff = max(0.0, float(retry_backoff))

    def collect(self) -> tuple[list[str], str]:
        """Return ``(inventory_urls, inventory_status)``.

        ``inventory_status`` is ``"available"`` if at least one sitemap document
        was successfully parsed (even with zero ``<url>`` entries);
        ``"unavailable"`` if no document could be fetched/parsed.
        """
        urls, parsed = self._recurse(self.sitemap_url, depth=0, seen=set())
        # De-duplicate while preserving order.
        seen: set[str] = set()
        out: list[str] = []
        for u in urls:
            if u not in seen:
                seen.add(u)
                out.append(u)
        return out, ("available" if parsed else "unavailable")

    def _fetch(self, url: str):
        """Fetch one URL with bounded retry on TRANSIENT transport failures.

        4xx/5xx are definitive responses and are NEVER retried. Only
        ``TRANSPORT_FAILURE`` (timeout, DNS, TLS, connection reset) is retried,
        up to ``max_retries`` additional attempts with a fixed ``retry_backoff``.
        Any outcome still resolves to ``None`` (caller -> inventory unavailable)
        rather than raising.
        """
        last: Optional[tuple[int, str]] = None
        for attempt in range(self.max_retries + 1):
            try:
                status, body = self.transport(url, self.timeout)
            except Exception:
                status, body = TRANSPORT_FAILURE, ""
            if status != TRANSPORT_FAILURE or attempt >= self.max_retries:
                last = (status, body)
                break
            if self.retry_backoff > 0:
                time.sleep(self.retry_backoff)
        if last is None or last[0] != 200 or not last[1]:
            return None
        return last[1]

    @staticmethod
    def _parse(body: str) -> tuple[list[str], list[str], bool]:
        """Return ``(page_urls, sub_sitemap_urls, ok)``. ``ok`` is False on
        malformed XML."""
        try:
            root = ET.fromstring(body)
        except (ET.ParseError, ValueError):
            return [], [], False
        root_local = root.tag.split("}")[-1]
        page_urls: list[str] = []
        sub_urls: list[str] = []
        for el in root.iter():
            local = el.tag.split("}")[-1]
            if local == "loc":
                text = (el.text or "").strip()
                if not text:
                    continue
                if root_local == "sitemapindex":
                    sub_urls.append(text)
                else:
                    page_urls.append(text)
        return page_urls, sub_urls, True

    def _recurse(self, url: str, depth: int, seen: set) -> tuple[list[str], bool]:
        if depth > self.max_depth or url in seen:
            return [], False
        seen.add(url)
        body = self._fetch(url)
        if body is None:
            return [], False
        page_urls, sub_urls, ok = self._parse(body)
        if not ok:
            return [], False
        all_urls = list(page_urls)
        parsed_any = True
        for sub in sub_urls:
            if sub in seen:
                continue
            su, sp = self._recurse(sub, depth + 1, seen)
            all_urls.extend(su)
            parsed_any = parsed_any or sp
        return all_urls, parsed_any


def build_site_coverage_adapter(
    cfg: Optional[dict], transport: Optional[SiteMapTransport] = None
) -> SiteInventoryAdapter:
    """Build a SiteInventoryAdapter from the ``[site_coverage]`` config.

    If no ``transport`` is injected, the live ``default_sitemap_transport`` is
    used with the configured CA bundle (certifi / system default) so TLS
    verification works in environments whose system CA store is incomplete.
    """
    sc = (cfg or {}).get("site_coverage", {}) if isinstance(cfg, dict) else {}
    ca = sc.get("ca_bundle_path", "") or None
    if transport is None:
        transport = lambda u, t: default_sitemap_transport(u, t, ca_bundle_path=ca)
    return SiteInventoryAdapter(
        sitemap_url=sc.get("sitemap_url", "https://alumcasting.com/sitemap.xml"),
        transport=transport,
        timeout=float(sc.get("timeout", 15.0)),
        max_retries=int(sc.get("sitemap_max_retries", 1)),
        retry_backoff=float(sc.get("sitemap_retry_backoff", 0.5)),
    )


# --------------------------------------------------------------------------- #
# Deterministic matching (reused / pure)                                      #
# --------------------------------------------------------------------------- #

_TOKEN_RE = re.compile(r"[^\w\s]")


def _tokenize(text: str) -> list[str]:
    """Light tokenization for overlap: NFC, lowercase, strip punctuation."""
    if not text:
        return []
    t = unicodedata.normalize("NFC", text.lower())
    t = _TOKEN_RE.sub(" ", t)
    t = re.sub(r"\s+", " ", t).strip()
    return [tok for tok in t.split() if tok]


def compute_overlap(candidate_norm: str, page_norm: str) -> float:
    """Jaccard overlap of two normalized token sets (0.0 - 1.0). Deterministic."""
    a = set(_tokenize(candidate_norm))
    b = set(_tokenize(page_norm))
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)


def has_problem_term(page_tokens: set, candidate_defect_terms: set) -> bool:
    """True iff the page shares at least one of the candidate's defect terms."""
    return bool(page_tokens & candidate_defect_terms)


def match_candidate_to_page(
    candidate_norm: str,
    page_norm_text: str,
    threshold: float = 0.60,
    candidate_defect_terms: Optional[set] = None,
) -> tuple[Optional[str], float, bool]:
    """Deterministic candidate/page matching.

    Returns ``(match_tier, overlap_ratio, problem_match)``.

      * match_tier: ``"exact"`` (normalized strings equal), ``"token_overlap"``
        (Jaccard >= threshold), or ``None``.
      * problem_match: True iff the page shares a candidate defect term.

    This is the explainable, non-AI matching used when real page content/title
    metadata is available (e.g. future local-content or GSC-page-title sources).
    Sitemap-only URLs (no title/body) do NOT use this to assert coverage.
    """
    cand_tokens = set(_tokenize(candidate_norm))
    page_tokens = set(_tokenize(page_norm_text))
    if not cand_tokens or not page_tokens:
        return None, 0.0, False
    overlap = compute_overlap(candidate_norm, page_norm_text)
    if candidate_norm.strip() == page_norm_text.strip():
        tier: Optional[str] = "exact"
    elif overlap >= threshold:
        tier = "token_overlap"
    else:
        tier = None
    problem = has_problem_term(
        page_tokens, candidate_defect_terms or set()
    )
    return tier, overlap, problem


def _page_slug_text(url: str, normalizer: URLNormalizer) -> str:
    """Derive human-readable match text from a sitemap page URL (URL-level only).

    Uses ONLY the URL path slug (last non-empty path segment, with ``-``/``_``
    replaced by spaces). This is deliberately URL-level evidence -- it does NOT
    read the page body/title -- so it can only assert topical alignment at the
    slug level. We never infer problem coverage from a slug alone.
    """
    try:
        nurl = normalizer.normalize(url)
        path = urlsplit(nurl).path
    except Exception:
        return ""
    segs = [s for s in path.split("/") if s]
    if not segs:
        return ""
    return segs[-1].replace("-", " ").replace("_", " ").strip()


def _match_candidates_to_inventory(
    norm_candidates: list,
    inventory_urls: list,
    normalizer: URLNormalizer,
    token_threshold: float,
    candidate_defect_terms: set,
) -> list:
    """Deterministically match candidate queries against sitemap page URLs.

    Pure, offline, deterministic. For each normalized candidate, compare it to
    the URL-level slug text of every inventory page using ``match_candidate_to_page``
    (exact normalized match OR Jaccard token overlap >= threshold). No fuzzy,
    substring, embedding, or LLM matching. Each distinct matching page becomes a
    ``MatchedPage`` with ``source="sitemap"`` and ``topical_match`` /
    ``problem_match`` left ``"unknown"`` -- a URL match is only page-existence
    evidence and never asserts that the page answers the problem.
    """
    matched: list = []
    seen_urls: set = set()
    for _orig, nc in norm_candidates:
        if not nc:
            continue
        for page_url in inventory_urls:
            if not page_url or page_url in seen_urls:
                continue
            slug = _page_slug_text(page_url, normalizer)
            if not slug:
                continue
            tier, overlap, _problem = match_candidate_to_page(
                nc, slug, token_threshold, candidate_defect_terms
            )
            if tier is None:
                continue
            seen_urls.add(page_url)
            matched.append(
                MatchedPage(
                    url=page_url,
                    match_tier=tier,
                    topical_match="unknown",
                    problem_match="unknown",
                    source="sitemap",
                    overlap_ratio=overlap,
                )
            )
    return matched


# --------------------------------------------------------------------------- #
# Coverage model                                                              #
# --------------------------------------------------------------------------- #

class CoverageStatus(str, Enum):
    NONE = "none"
    PARTIAL = "partial"
    EXISTING = "existing"
    STRONG = "strong"
    UNKNOWN = "unknown"


class ContentGapStatus(str, Enum):
    NEW_OPPORTUNITY = "new_opportunity"
    PARTIAL_GAP = "partial_gap"
    REFRESH_OPPORTUNITY = "refresh_opportunity"
    COVERED = "covered"
    UNKNOWN = "unknown"


class CoverageConfidence(str, Enum):
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"
    UNKNOWN = "unknown"


@dataclass
class MatchedPage:
    """One matched page (from GSC page evidence or future content metadata)."""

    url: str = ""
    match_tier: str = ""            # "gsc_query_page" | "exact" | "token_overlap" | "url_existence"
    topical_match: str = "unknown"  # "matched" | "unknown"
    problem_match: str = "unknown"  # "matched" | "unknown"
    source: str = ""                # "gsc_page" | "content_metadata" | "sitemap"
    overlap_ratio: float = 0.0
    impressions: int = 0
    clicks: int = 0
    ctr: float = 0.0
    position: float = 0.0
    performance: str = ""           # "strong" | "weak" | ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "url": self.url,
            "match_tier": self.match_tier,
            "topical_match": self.topical_match,
            "problem_match": self.problem_match,
            "source": self.source,
            "overlap_ratio": round(self.overlap_ratio, 4),
            "impressions": self.impressions,
            "clicks": self.clicks,
            "ctr": round(self.ctr, 4),
            "position": round(self.position, 4),
            "performance": self.performance,
        }


@dataclass
class ProblemCoverageEvidence:
    """Structured problem-coverage evidence, kept separate from performance."""

    page_existence: str = "unknown"   # "true" | "false" | "unknown"
    topical_match: str = "unknown"    # "matched" | "unknown"
    problem_match: str = "unknown"    # "matched" | "unknown"
    performance: str = ""             # "strong" | "weak" | ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "page_existence": self.page_existence,
            "topical_match": self.topical_match,
            "problem_match": self.problem_match,
            "performance": self.performance,
        }


@dataclass
class SiteCoverageEvidence:
    """Per-signal site-coverage / content-gap result (separate evidence class)."""

    site_coverage: CoverageStatus = CoverageStatus.UNKNOWN
    content_gap_status: ContentGapStatus = ContentGapStatus.UNKNOWN
    coverage_confidence: CoverageConfidence = CoverageConfidence.UNKNOWN
    coverage_sources: list = field(default_factory=list)
    matched_pages: list = field(default_factory=list)        # list[MatchedPage]
    problem_coverage_evidence: Any = None                     # ProblemCoverageEvidence
    multiple_competing_pages: bool = False
    notes: list = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "site_coverage": self.site_coverage.value,
            "content_gap_status": self.content_gap_status.value,
            "coverage_confidence": self.coverage_confidence.value,
            "coverage_sources": list(self.coverage_sources),
            "matched_pages": [m.to_dict() for m in self.matched_pages],
            "problem_coverage_evidence": (
                self.problem_coverage_evidence.to_dict()
                if self.problem_coverage_evidence is not None
                else None
            ),
            "multiple_competing_pages": self.multiple_competing_pages,
            "notes": list(self.notes),
        }


# --------------------------------------------------------------------------- #
# Classification                                                             #
# --------------------------------------------------------------------------- #

def build_site_coverage(
    signal: Any,
    inventory_urls: list,
    inventory_status: str,
    gsc_evidence_rows: list,
    cfg: Optional[dict] = None,
) -> Optional[SiteCoverageEvidence]:
    """Classify site coverage for one problem signal.

    Returns ``None`` for non-problem signals or signals without a ContentBrief.
    NEVER mutates the signal, its scores, or its ContentBrief.

    Evidence priority:
      1. GSC page evidence (Phase 1.5C reuse) -- strongest first-party signal.
      2. Sitemap inventory -- establishes a domain page inventory but, with no
         page content/title access, cannot confirm topical/problem match, so it
         resolves to ``unknown`` (page existence != problem coverage).
      3. No inventory and no GSC page evidence -- ``unknown``.
    """
    if not getattr(signal, "is_problem_signal", False):
        return None
    brief = getattr(signal, "content_brief", None)
    if brief is None:
        return None

    cfg = cfg or {}
    sc_cfg = cfg.get("site_coverage", {}) if isinstance(cfg, dict) else {}
    token_threshold = float(sc_cfg.get("token_overlap_threshold", 0.60))
    position_weak = float(sc_cfg.get("position_weak_threshold", 20.0))
    normalizer = build_url_normalizer(cfg)

    # Candidate problem vocabulary (reused deterministic extraction).
    candidates = extract_candidate_queries(brief)
    norm_candidates = dedupe_candidates(candidates)
    candidate_tokens: set = set()
    for _orig, nc in norm_candidates:
        candidate_tokens.update(_tokenize(nc))
    candidate_defect_terms = {t for t in candidate_tokens if t in _DEFECT_TERMS}

    # --- 1) GSC page evidence (Phase 1.5C reuse) ---------------------------
    # Only rows that actually carry a page URL count as page evidence.
    gsc_rows = [r for r in (gsc_evidence_rows or []) if getattr(r, "page", "")]
    notes: list = []

    if gsc_rows:
        matched: list = []
        for r in gsc_rows:
            pos = float(getattr(r, "position", 0.0) or 0.0)
            perf = "strong" if (pos > 0 and pos <= position_weak) else "weak"
            nq = getattr(r, "normalized_query", "") or ""
            overlap = compute_overlap(nq, getattr(r, "query", "") or "")
            matched.append(
                MatchedPage(
                    url=getattr(r, "page", ""),
                    match_tier="gsc_query_page",
                    topical_match="matched",
                    problem_match="matched",
                    source="gsc_page",
                    overlap_ratio=overlap,
                    impressions=int(getattr(r, "impressions", 0) or 0),
                    clicks=int(getattr(r, "clicks", 0) or 0),
                    ctr=float(getattr(r, "ctr", 0.0) or 0.0),
                    position=pos,
                    performance=perf,
                )
            )
        page_urls = {m.url for m in matched}
        multiple = len(page_urls) > 1
        if multiple:
            # CASE H: multiple pages compete for the same query.
            site_cov = CoverageStatus.PARTIAL
            gap = ContentGapStatus.PARTIAL_GAP
            conf = CoverageConfidence.MEDIUM
            notes.append(
                "Multiple pages compete for the same query; consider consolidating "
                "rather than creating new content."
            )
            # No single page performance is attributed for the PCE when pages
            # compete; the matrix intentionally does NOT auto-pick a winner.
            perf_for_pce = ""
        else:
            m = matched[0]
            if m.performance == "strong":
                # CASE E: strong problem coverage + strong search performance.
                site_cov = CoverageStatus.STRONG
                gap = ContentGapStatus.COVERED
                conf = CoverageConfidence.HIGH
            else:
                # CASE D: strong problem coverage + weak search performance.
                site_cov = CoverageStatus.EXISTING
                gap = ContentGapStatus.REFRESH_OPPORTUNITY
                conf = CoverageConfidence.HIGH
            perf_for_pce = m.performance
        pce = ProblemCoverageEvidence(
            page_existence="true",
            topical_match="matched",
            problem_match="matched",
            performance=perf_for_pce,
        )
        return SiteCoverageEvidence(
            site_coverage=site_cov,
            content_gap_status=gap,
            coverage_confidence=conf,
            coverage_sources=["gsc_page"],
            matched_pages=matched,
            problem_coverage_evidence=pce,
            multiple_competing_pages=multiple,
            notes=notes,
        )

    # --- 2) Sitemap inventory only (no GSC page evidence) ------------------
    # Safe model (Phase 1.5D spec section 3): sitemap gives URL existence but
    # NOT page title/headings/body/problem coverage. We still ACTIVATE the
    # deterministic candidate->page matching so a real URL-level match is
    # recorded in ``matched_pages``. However, a URL/slug match is ONLY
    # page-existence / topical-alignment evidence -- we do NOT infer that the
    # page answers the specific problem. So ``site_coverage`` stays ``unknown``
    # and problem coverage stays ``unknown`` (page existence != problem
    # coverage). When a deterministic match exists we surface it with
    # ``coverage_confidence = LOW`` (weak URL-level evidence) rather than
    # fabricating coverage.
    inventory_available = inventory_status == "available"
    if inventory_available:
        matched_pages = _match_candidates_to_inventory(
            norm_candidates, inventory_urls, normalizer,
            token_threshold, candidate_defect_terms,
        )
        if matched_pages:
            pce = ProblemCoverageEvidence(
                page_existence="true",
                topical_match="unknown",
                problem_match="unknown",
                performance="",
            )
            best = max(matched_pages, key=lambda m: m.overlap_ratio)
            notes.append(
                "A sitemap URL deterministically matches the candidate query at "
                "the URL/slug level (best tier=%s, overlap=%.2f). This is "
                "page-existence / topical-alignment evidence ONLY; it does NOT "
                "prove the page answers the specific problem. Topical/problem "
                "coverage remains unknown (page existence != problem coverage)."
                % (best.match_tier, best.overlap_ratio)
            )
            return SiteCoverageEvidence(
                site_coverage=CoverageStatus.UNKNOWN,
                content_gap_status=ContentGapStatus.UNKNOWN,
                coverage_confidence=CoverageConfidence.LOW,
                coverage_sources=["sitemap"],
                matched_pages=matched_pages,
                problem_coverage_evidence=pce,
                notes=notes,
            )
        # No deterministic sitemap match found.
        pce = ProblemCoverageEvidence(
            page_existence="unknown",
            topical_match="unknown",
            problem_match="unknown",
            performance="",
        )
        notes.append(
            "Sitemap inventory available, but no URL in the sitemap "
            "deterministically matches the candidate query. Page title/headings/"
            "body are not inspected; topic/problem match cannot be determined "
            "(page existence does NOT imply problem coverage)."
        )
        return SiteCoverageEvidence(
            site_coverage=CoverageStatus.UNKNOWN,
            content_gap_status=ContentGapStatus.UNKNOWN,
            coverage_confidence=CoverageConfidence.UNKNOWN,
            coverage_sources=["sitemap"],
            matched_pages=[],
            problem_coverage_evidence=pce,
            notes=notes,
        )

    # --- 3) No inventory and no GSC page evidence -> unknown ---------------
    pce = ProblemCoverageEvidence(
        page_existence="unknown",
        topical_match="unknown",
        problem_match="unknown",
        performance="",
    )
    notes.append(
        "No site inventory and no GSC page evidence available; coverage cannot "
        "be determined (absence of evidence is NOT evidence of absence)."
    )
    return SiteCoverageEvidence(
        site_coverage=CoverageStatus.UNKNOWN,
        content_gap_status=ContentGapStatus.UNKNOWN,
        coverage_confidence=CoverageConfidence.UNKNOWN,
        coverage_sources=[],
        matched_pages=[],
        problem_coverage_evidence=pce,
        notes=notes,
    )


# --------------------------------------------------------------------------- #
# Pipeline attachment                                                        #
# --------------------------------------------------------------------------- #

def attach_site_coverage_to_signals(
    signals: list,
    inventory_urls: list,
    inventory_status: str,
    cfg: Optional[dict] = None,
) -> None:
    """Attach a ``SiteCoverageEvidence`` to every problem signal (sibling of
    ``content_brief`` and ``search_evidence``). Non-problem signals get ``None``.

    This NEVER mutates scores, ContentBrief, ranking, or search-demand evidence.
    GSC page evidence is read from each signal's existing ``search_evidence``.
    """
    for s in signals:
        if getattr(s, "is_problem_signal", False) and getattr(s, "content_brief", None) is not None:
            rows = []
            se = getattr(s, "search_evidence", None)
            if se is not None:
                rows = getattr(se, "evidence", None) or []
            s.site_coverage = build_site_coverage(
                s, inventory_urls, inventory_status, rows, cfg
            )
        else:
            s.site_coverage = None
