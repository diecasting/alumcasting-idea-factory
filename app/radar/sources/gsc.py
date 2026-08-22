"""Read-only Google Search Console Search Analytics adapter (retrieval only).

Phase 1.5B — INFRASTRUCTURE ONLY. This module implements the *retrieval
mechanics* for the Search Console API (Webmasters API v3) ``searchanalytics.query``
endpoint. It does NOT:

  * match GSC queries against ContentBrief,
  * compute search-demand status / search volume / content gap / competition,
  * attach GSC evidence to production signals,
  * modify problem_score / opportunity_score / ContentBrief,
  * change the report schema.

Those are deferred to later phases (1.5C / 1.5D).

Security guarantees (hard requirements from the Phase 1.5A audit):

  * NO credential, token, or secret is embedded in this file, the config, or
    any test. Authentication, when enabled, is supplied at *runtime* only
    (via a caller-supplied bearer token) and is NEVER persisted.
  * NO raw GSC response is written to disk; only normalized, aggregated rows
    are returned in memory.
  * All network access goes through an INJECTABLE transport so the unit tests
    run fully offline. The default transport uses only the stdlib ``urllib``
    (no ``requests`` and no heavy Google SDK).
  * GSC is DISABLED by default (config ``[gsc] enabled = false``) and the radar
    pipeline never invokes this adapter.

The adapter subclasses ``SourceAdapter`` to reuse the existing transport-
injection / failure-tolerant architecture. Note that ``collect()`` returns a
list of GSC evidence *rows* (``dict``s), not ``RawSignal`` objects, because GSC
provides performance analytics rather than problem posts; wiring the output
into the radar is a later-phase concern.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from typing import Any, Callable, Optional

from app.radar.sources.base import SourceAdapter

# Official Search Console (Webmasters API v3) Search Analytics query endpoint.
# The siteUrl path segment MUST be URL-encoded and must exactly match the
# verified property (e.g. "sc-domain:alumcasting.com" or the exact URL-prefix
# including trailing slash). We URL-encode the property in _build_url().
SEARCH_ANALYTICS_TEMPLATE = (
    "https://www.googleapis.com/webmasters/v3/sites/{site_url}/searchAnalytics/query"
)

# Read-only OAuth scope. The full "webmasters" scope is intentionally NOT used.
READONLY_SCOPE = "https://www.googleapis.com/auth/webmasters.readonly"

# Maximum rows the API returns per request. Smaller values are permitted and
# are used by tests to exercise pagination cheaply.
MAX_ROW_LIMIT = 5000

# Sentinel status used when the transport itself fails (timeout / network error)
# rather than returning an HTTP status code.
TRANSPORT_FAILURE = -1


# A GSC transport: (url, method, headers, data_bytes, timeout) -> (status, body_str)
GSCTransport = Callable[[str, str, dict, bytes, float], tuple[int, str]]


def _build_url(site_url: str) -> str:
    """Build the Search Analytics query URL.

    The property identifier is URL-encoded so it matches exactly what the API
    expects (colon in "sc-domain:..." becomes "%3A", slashes in URL-prefix
    properties become "%2F", etc.).
    """
    from urllib.parse import quote

    return SEARCH_ANALYTICS_TEMPLATE.format(site_url=quote(site_url, safe=""))


def _build_request_body(
    start_date: str,
    end_date: str,
    dimensions: list[str],
    data_state: str = "final",
    row_limit: int = MAX_ROW_LIMIT,
    start_row: int = 0,
) -> dict[str, Any]:
    """Build the Search Analytics query JSON body (deterministic, no secrets).

    Only the dimensions/date/dataState/rowLimit/startRow fields needed for
    read-only retrieval are set. ``searchType`` is omitted (defaults to "web").
    """
    return {
        "startDate": start_date,
        "endDate": end_date,
        "dimensions": dimensions,
        "dataState": data_state,
        "rowLimit": row_limit,
        "startRow": start_row,
    }


def _normalize_rows(rows: Optional[list[dict[str, Any]]]) -> list[dict[str, Any]]:
    """Flatten raw GSC rows into a stable structure.

    A missing or empty ``rows`` key is NOT an error — it means no data for the
    window. Both cases return an empty list.
    """
    if not rows:
        return []
    out: list[dict[str, Any]] = []
    for row in rows:
        keys = row.get("keys", []) if isinstance(row, dict) else []
        out.append(
            {
                "query": keys[0] if len(keys) > 0 else "",
                "page": keys[1] if len(keys) > 1 else "",
                "clicks": row.get("clicks", 0) if isinstance(row, dict) else 0,
                "impressions": row.get("impressions", 0) if isinstance(row, dict) else 0,
                "ctr": row.get("ctr", 0.0) if isinstance(row, dict) else 0.0,
                "position": row.get("position", 0.0) if isinstance(row, dict) else 0.0,
            }
        )
    return out


def default_gsc_transport(
    url: str,
    method: str = "POST",
    headers: Optional[dict] = None,
    data: bytes = b"",
    timeout: float = 30.0,
) -> tuple[int, str]:
    """Stdlib-urllib POST transport. Returns (status_code, body_str).

    Non-blocking by design: HTTP errors return their status code (so callers can
    decide to skip), and any transport-level failure (timeout, DNS, TLS) returns
    (TRANSPORT_FAILURE, ""). No third-party HTTP library is used.
    """
    import urllib.error
    import urllib.request

    req = urllib.request.Request(
        url, data=data or None, headers=headers or {}, method=method
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            charset = resp.headers.get_content_charset() or "utf-8"
            return resp.getcode(), resp.read().decode(charset, errors="replace")
    except urllib.error.HTTPError as exc:
        try:
            body = exc.read().decode("utf-8", errors="replace")
        except Exception:
            body = ""
        return exc.code, body
    except Exception:
        # Timeout / network / TLS failure: signal non-blocking failure.
        return TRANSPORT_FAILURE, ""


class GSCAdapter(SourceAdapter):
    """Read-only GSC Search Analytics adapter (retrieval mechanics only).

    Mirrors the offline, transport-injectable, failure-tolerant design of the
    other source adapters. Performs NO authentication beyond accepting a
    caller-supplied bearer token via ``auth_token``; it never creates, stores,
    or persists tokens.
    """

    source_type = "gsc"

    def __init__(
        self,
        site_url: str,
        auth_token: str = "",
        lookback_days: int = 90,
        data_state: str = "final",
        enabled: bool = False,
        transport: Optional[GSCTransport] = None,
        timeout: float = 30.0,
        row_limit: int = MAX_ROW_LIMIT,
    ) -> None:
        # Base class sets self.name / self.user_agent / self.transport; we then
        # install the GSC-specific transport (POST + auth + status aware).
        super().__init__(name=f"gsc:{site_url}", transport=None)
        self.site_url = site_url
        self.auth_token = auth_token  # ephemeral only; never persisted
        self.lookback_days = lookback_days
        self.data_state = data_state
        self.enabled = enabled
        self.timeout = timeout
        self.row_limit = row_limit
        self.transport: GSCTransport = transport or default_gsc_transport

    def is_enabled(self) -> bool:
        return bool(self.enabled)

    # -- date handling -----------------------------------------------------

    def _date_range(self, end_date: Optional[str] = None) -> tuple[str, str]:
        """Return (start_date, end_date) as ``YYYY-MM-DD``.

        ``end_date`` defaults to (today UTC - 3 days) to skip the partial
        processing lag; ``start_date`` is ``end_date - lookback_days``.
        """
        if end_date is None:
            end_date = (datetime.now(timezone.utc) - timedelta(days=3)).strftime(
                "%Y-%m-%d"
            )
        start = (
            datetime.strptime(end_date, "%Y-%m-%d") - timedelta(days=self.lookback_days)
        ).strftime("%Y-%m-%d")
        return start, end_date

    # -- collection entry point -------------------------------------------

    def collect(self) -> list[dict[str, Any]]:  # type: ignore[override]
        """Entry point used by the source pattern.

        Returns an empty list when disabled. When enabled, delegates to
        ``query()``. Returns ``list[dict]`` (GSC evidence rows) rather than
        ``RawSignal`` — GSC provides performance analytics, not problem posts.
        """
        if not self.is_enabled():
            return []
        return self.query()

    # -- retrieval mechanics ----------------------------------------------

    def query(self, end_date: Optional[str] = None) -> list[dict[str, Any]]:
        """Fetch all (query, page) rows across pagination.

        Non-blocking: any failure (auth/quota/server error/timeout/transport
        error/malformed JSON) yields whatever has been collected so far (often
        an empty list). A missing or empty ``rows`` key is treated as "no data"
        and terminates the loop normally — NOT as an error.
        """
        start_date, end_date = self._date_range(end_date)
        all_rows: list[dict[str, Any]] = []
        start_row = 0
        while True:
            body = _build_request_body(
                start_date=start_date,
                end_date=end_date,
                dimensions=["query", "page"],
                data_state=self.data_state,
                row_limit=self.row_limit,
                start_row=start_row,
            )
            status, raw = self._post(body)
            if status != 200:
                # 401/403/429/5xx or transport failure -> non-blocking.
                return all_rows
            try:
                payload = json.loads(raw)
            except (ValueError, TypeError):
                # Malformed response -> non-blocking.
                return all_rows
            rows = payload.get("rows") if isinstance(payload, dict) else None
            if not rows:
                # Missing or empty rows is not an error.
                break
            all_rows.extend(_normalize_rows(rows))
            if len(rows) < self.row_limit:
                break
            start_row += self.row_limit
        return all_rows

    def _post(self, body: dict[str, Any]) -> tuple[int, str]:
        """POST a query body via the injected transport.

        Returns (status, body_str), or (TRANSPORT_FAILURE, "") if the transport
        itself raises. Never raises.
        """
        url = _build_url(self.site_url)
        headers: dict[str, str] = {"Content-Type": "application/json"}
        if self.auth_token:
            headers["Authorization"] = f"Bearer {self.auth_token}"
        data = json.dumps(body).encode("utf-8")
        try:
            return self.transport(
                url, method="POST", headers=headers, data=data, timeout=self.timeout
            )
        except Exception:
            return TRANSPORT_FAILURE, ""


def build_gsc_adapter(cfg: Optional[dict], transport: Optional[GSCTransport] = None) -> GSCAdapter:
    """Build a GSCAdapter from a parsed ``config/radar.toml`` dict.

    Reads the ``[gsc]`` section. The adapter is DISABLED unless ``enabled`` is
    explicitly true. This helper is intentionally NOT wired into the radar
    pipeline yet (future phases 1.5C/1.5D will call it).
    """
    gsc_cfg = (cfg or {}).get("gsc", {}) if isinstance(cfg, dict) else {}
    return GSCAdapter(
        site_url=gsc_cfg.get("property", "sc-domain:alumcasting.com"),
        lookback_days=int(gsc_cfg.get("lookback_days", 90)),
        data_state=gsc_cfg.get("data_state", "final"),
        enabled=bool(gsc_cfg.get("enabled", False)),
        transport=transport,
    )
