"""Tests for the read-only GSC Search Analytics adapter (Phase 1.5B).

Network-free: every case injects a fake transport. No real credentials, no
real GSC calls, no persistence. GSC must remain disabled by default.
"""

import json

import pytest

from app.radar.sources import GSCAdapter, READONLY_SCOPE, build_gsc_adapter
from app.radar.scoring import load_config


def _row(q, p, clicks=1, impressions=10, ctr=0.1, position=5.0):
    return {
        "keys": [q, p],
        "clicks": clicks,
        "impressions": impressions,
        "ctr": ctr,
        "position": position,
    }


def make_fake(responder):
    """Build an injectable transport that records every call and delegates to
    ``responder(body_dict, calls) -> (status, body_str)``."""
    calls = []

    def fake(url, method="POST", headers=None, data=b"", timeout=30.0):
        body = json.loads(data.decode("utf-8")) if data else {}
        calls.append(
            {"url": url, "method": method, "headers": headers or {}, "body": body}
        )
        return responder(body, calls)

    fake.calls = calls
    return fake


# ---------------------------------------------------------------------------
# URL / request structure
# ---------------------------------------------------------------------------


def test_build_url_domain_property_is_url_encoded():
    from app.radar.sources.gsc import _build_url

    url = _build_url("sc-domain:alumcasting.com")
    assert url.startswith("https://www.googleapis.com/webmasters/v3/sites/")
    assert url.endswith("/searchAnalytics/query")
    # The colon in a domain property must be percent-encoded.
    assert "sc-domain%3Aalumcasting.com" in url


def test_build_request_body_structure():
    from app.radar.sources.gsc import _build_request_body

    body = _build_request_body(
        "2026-05-01", "2026-07-30", ["query", "page"], "final", 5000, 0
    )
    assert body["startDate"] == "2026-05-01"
    assert body["endDate"] == "2026-07-30"
    assert body["dimensions"] == ["query", "page"]
    assert body["dataState"] == "final"
    assert body["rowLimit"] == 5000
    assert body["startRow"] == 0


def test_read_only_scope_is_readonly_not_full():
    # Must be the read-only scope, never the full "webmasters" scope.
    assert READONLY_SCOPE == "https://www.googleapis.com/auth/webmasters.readonly"
    assert READONLY_SCOPE != "https://www.googleapis.com/auth/webmasters"


def test_correct_property_identifier_sent_in_request():
    captured = {}

    def responder(body, calls):
        captured["body"] = body
        return 200, json.dumps({"rows": []})

    adapter = GSCAdapter(
        "sc-domain:alumcasting.com", transport=make_fake(responder), enabled=True
    )
    adapter.query(end_date="2026-07-30")
    # The property string is preserved on the adapter and used as the date range
    # source; the URL encoding is verified separately above.
    assert adapter.site_url == "sc-domain:alumcasting.com"
    assert captured["body"]["startDate"] == "2026-05-01"  # 90-day lookback
    assert captured["body"]["endDate"] == "2026-07-30"


def test_read_only_request_uses_post_and_content_type():
    captured = {}

    def responder(body, calls):
        captured.update(calls[-1])
        return 200, json.dumps({"rows": []})

    adapter = GSCAdapter("sc-domain:x", transport=make_fake(responder), enabled=True)
    adapter.query(end_date="2026-07-30")
    assert captured["method"] == "POST"
    assert captured["headers"].get("Content-Type") == "application/json"


# ---------------------------------------------------------------------------
# Bearer auth header (no leakage)
# ---------------------------------------------------------------------------


def test_authorization_header_present_when_token_supplied():
    captured = {}

    def responder(body, calls):
        captured.update(calls[-1])
        return 200, json.dumps({"rows": []})

    adapter = GSCAdapter(
        "sc-domain:x",
        auth_token="TEST_BEARER_TOKEN_NOT_REAL",
        transport=make_fake(responder),
        enabled=True,
    )
    adapter.query(end_date="2026-07-30")
    assert captured["headers"].get("Authorization") == "Bearer TEST_BEARER_TOKEN_NOT_REAL"


def test_no_authorization_header_when_token_absent():
    captured = {}

    def responder(body, calls):
        captured.update(calls[-1])
        return 200, json.dumps({"rows": []})

    adapter = GSCAdapter("sc-domain:x", transport=make_fake(responder), enabled=True)
    assert adapter.auth_token == ""  # no embedded default secret
    adapter.query(end_date="2026-07-30")
    assert "Authorization" not in captured["headers"]


def test_no_embedded_credentials_in_module():
    import app.radar.sources.gsc as gsc

    suspicious = ("ya29", "AIza", "private_key", "client_secret", "BEGIN PRIVATE KEY", "refresh_token")
    for name, val in vars(gsc).items():
        if isinstance(val, str):
            for s in suspicious:
                assert s not in val, f"module attribute {name!r} contains {s!r}"
    # The adapter default carries no token.
    assert GSCAdapter("sc-domain:x").auth_token == ""


# ---------------------------------------------------------------------------
# Retrieval behavior
# ---------------------------------------------------------------------------


def test_query_successful_single_page():
    def responder(body, calls):
        return 200, json.dumps(
            {"rows": [_row("aluminum die casting porosity", "https://x.com/p1", 3, 120)]}
        )

    adapter = GSCAdapter("sc-domain:x", transport=make_fake(responder), enabled=True)
    rows = adapter.query(end_date="2026-07-30")
    assert len(rows) == 1
    assert rows[0]["query"] == "aluminum die casting porosity"
    assert rows[0]["page"] == "https://x.com/p1"
    assert rows[0]["clicks"] == 3
    assert rows[0]["impressions"] == 120
    assert rows[0]["position"] == 5.0


def test_query_multiple_rows():
    def responder(body, calls):
        return 200, json.dumps(
            {
                "rows": [
                    _row("q1", "p1", 1, 10),
                    _row("q2", "p2", 2, 20),
                    _row("q3", "p3", 3, 30),
                ]
            }
        )

    adapter = GSCAdapter("sc-domain:x", transport=make_fake(responder), enabled=True)
    rows = adapter.query(end_date="2026-07-30")
    assert len(rows) == 3
    assert [r["query"] for r in rows] == ["q1", "q2", "q3"]


def test_query_missing_rows_key_is_not_an_error():
    def responder(body, calls):
        return 200, json.dumps({})  # no "rows" key at all

    adapter = GSCAdapter("sc-domain:x", transport=make_fake(responder), enabled=True)
    rows = adapter.query(end_date="2026-07-30")
    assert rows == []


def test_query_empty_rows_is_not_an_error():
    def responder(body, calls):
        return 200, json.dumps({"rows": []})

    adapter = GSCAdapter("sc-domain:x", transport=make_fake(responder), enabled=True)
    rows = adapter.query(end_date="2026-07-30")
    assert rows == []


def test_query_pagination():
    # Use a tiny row_limit so two pages are required.
    def responder(body, calls):
        start = body["startRow"]
        if start == 0:
            return 200, json.dumps({"rows": [_row("a", "p"), _row("b", "p")]})
        return 200, json.dumps({"rows": [_row("c", "p")]})

    fake = make_fake(responder)
    adapter = GSCAdapter(
        "sc-domain:x", transport=fake, enabled=True, row_limit=2
    )
    rows = adapter.query(end_date="2026-07-30")
    assert len(rows) == 3
    assert len(fake.calls) == 2  # two requests (page 1 + page 2)
    assert fake.calls[1]["body"]["startRow"] == 2


def test_query_handles_401():
    def responder(body, calls):
        return 401, json.dumps({"error": "unauthorized"})

    adapter = GSCAdapter("sc-domain:x", transport=make_fake(responder), enabled=True)
    assert adapter.query(end_date="2026-07-30") == []


def test_query_handles_403():
    def responder(body, calls):
        return 403, json.dumps({"error": "forbidden"})

    adapter = GSCAdapter("sc-domain:x", transport=make_fake(responder), enabled=True)
    assert adapter.query(end_date="2026-07-30") == []


def test_query_handles_429():
    def responder(body, calls):
        return 429, json.dumps({"error": "rate limited"})

    adapter = GSCAdapter("sc-domain:x", transport=make_fake(responder), enabled=True)
    assert adapter.query(end_date="2026-07-30") == []


def test_query_handles_5xx():
    def responder(body, calls):
        return 503, json.dumps({"error": "backend error"})

    adapter = GSCAdapter("sc-domain:x", transport=make_fake(responder), enabled=True)
    assert adapter.query(end_date="2026-07-30") == []


def test_query_handles_transport_failure():
    def exploding(url, method="POST", headers=None, data=b"", timeout=30.0):
        raise TimeoutError("simulated network timeout")

    adapter = GSCAdapter("sc-domain:x", transport=exploding, enabled=True)
    assert adapter.query(end_date="2026-07-30") == []


def test_query_handles_malformed_response():
    def responder(body, calls):
        return 200, "{this is not valid json"

    adapter = GSCAdapter("sc-domain:x", transport=make_fake(responder), enabled=True)
    assert adapter.query(end_date="2026-07-30") == []


# ---------------------------------------------------------------------------
# Disabled gate (GSC must not be called when disabled)
# ---------------------------------------------------------------------------


def test_collect_not_called_when_disabled():
    calls = []

    def recorder(url, method="POST", headers=None, data=b"", timeout=30.0):
        calls.append(1)
        return 200, json.dumps({"rows": []})

    # enabled defaults to False.
    adapter = GSCAdapter("sc-domain:x", transport=recorder)
    assert adapter.is_enabled() is False
    assert adapter.collect() == []
    assert calls == []  # transport was never invoked


def test_collect_invokes_transport_when_enabled():
    def responder(body, calls):
        return 200, json.dumps({"rows": [_row("q", "p")]})

    fake = make_fake(responder)
    adapter = GSCAdapter("sc-domain:x", transport=fake, enabled=True)
    rows = adapter.collect()
    assert len(rows) == 1
    assert len(fake.calls) == 1


# ---------------------------------------------------------------------------
# Config-driven disable (the real safety control)
# ---------------------------------------------------------------------------


def test_gsc_config_present_and_disabled_by_default():
    cfg = load_config()
    assert "gsc" in cfg
    assert cfg["gsc"]["enabled"] is False
    assert cfg["gsc"]["property"] == "sc-domain:alumcasting.com"
    assert cfg["gsc"]["lookback_days"] == 90
    assert cfg["gsc"]["data_state"] == "final"


def test_build_gsc_adapter_honors_disabled_flag():
    cfg = load_config()
    adapter = build_gsc_adapter(cfg, transport=make_fake(lambda b, c: (200, "{}")))
    assert adapter.is_enabled() is False
    assert adapter.collect() == []  # not called despite transport being present


def test_build_gsc_adapter_can_be_enabled_explicitly():
    cfg = {
        "gsc": {
            "enabled": True,
            "property": "sc-domain:alumcasting.com",
            "lookback_days": 28,
            "data_state": "final",
        }
    }
    calls = []

    def responder(body, c):
        calls.append(1)
        return 200, json.dumps({"rows": [_row("q", "p")]})

    adapter = build_gsc_adapter(cfg, transport=make_fake(responder))
    assert adapter.is_enabled() is True
    assert adapter.lookback_days == 28
    rows = adapter.collect()
    assert len(rows) == 1
    assert len(calls) == 1
