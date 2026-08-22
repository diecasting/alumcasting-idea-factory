"""Tests for normalization."""

from app.radar.models import RawSignal
from app.radar.normalize import normalize_many, normalize_signal


def _raw(title="", body="", url="https://x.com/a"):
    return RawSignal(
        source="s", source_type="reddit", external_id="1", title=title, body=body, url=url
    )


def test_html_and_whitespace_cleaned():
    raw = _raw(title="  <b>Porosity</b>  in  aluminum ", body="<p>why?</p>   \n  lots of   spaces")
    n = normalize_signal(raw)
    assert n.title == "Porosity in aluminum"
    assert "<p>" not in n.text
    assert "why?" in n.text
    assert "  " not in n.text


def test_signal_id_stable_and_url_based():
    a = normalize_signal(_raw(url="https://x.com/post/1"))
    b = normalize_signal(_raw(url="https://x.com/post/1"))
    c = normalize_signal(_raw(url="https://x.com/post/2"))
    assert a.id == b.id
    assert a.id != c.id
    assert len(a.id) == 16


def test_normalize_many_preserves_order_count():
    raws = [_raw(title=f"t{i}", url=f"https://x.com/{i}") for i in range(4)]
    out = normalize_many(raws)
    assert len(out) == 4
    assert [o.title for o in out] == [f"t{i}" for i in range(4)]
