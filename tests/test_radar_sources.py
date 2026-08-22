"""Tests for source adapters (Reddit JSON + RSS/Atom). Network-free."""

from pathlib import Path

from app.radar.models import RawSignal
from app.radar.sources.reddit import RedditSource
from app.radar.sources.rss import RSSSource

FIXTURES = Path(__file__).resolve().parent.parent / "app" / "radar" / "fixtures"


def _read(name: str) -> str:
    return (FIXTURES / name).read_text(encoding="utf-8")


def test_reddit_parse_json_from_fixture():
    src = RedditSource("manufacturing")
    sigs = src.parse_json(_read("reddit_hot.json"))
    assert len(sigs) == 5
    assert all(isinstance(s, RawSignal) for s in sigs)
    first = sigs[0]
    assert "porosity" in first.title.lower()
    assert first.url.startswith("https://www.reddit.com/r/manufacturing/")
    assert first.author == "caster_joe"
    assert first.published_at is not None
    assert first.published_at.year == 2023


def test_reddit_collect_uses_injected_transport():
    # No network: transport returns the fixture body.
    content = _read("reddit_hot.json")
    src = RedditSource("manufacturing", transport=lambda u: content)
    sigs = src.collect()
    assert len(sigs) == 5
    # The same transport is invoked with the expected URL.
    captured = {}

    def t(url):
        captured["url"] = url
        return content

    src2 = RedditSource("CNC", limit=10, transport=t)
    src2.collect()
    assert "reddit.com/r/CNC/hot.json" in captured["url"]
    assert "limit=10" in captured["url"]


def test_reddit_collect_handles_transport_failure():
    src = RedditSource("manufacturing", transport=lambda u: (_ for _ in ()).throw(RuntimeError("boom")))
    assert src.collect() == []


def test_rss_parse_rss_feed():
    src = RSSSource("https://example.com/feed")
    sigs = src.parse_feed(_read("sample_feed.xml"))
    assert len(sigs) == 2
    titles = [s.title for s in sigs]
    assert any("CNC milling chatter" in t for t in titles)
    # HTML in description is stripped.
    cnc = next(s for s in sigs if "CNC milling chatter" in s.title)
    assert "<p>" not in cnc.body
    assert "CNC machining" in cnc.body


def test_rss_parse_atom_feed():
    src = RSSSource("https://example.com/atom")
    sigs = src.parse_feed(_read("sample_atom.xml"))
    assert len(sigs) == 2
    shrink = next(s for s in sigs if "shrinkage" in s.title.lower())
    assert shrink.url == "https://example.com/shrinkage"
    assert shrink.published_at is not None


def test_rss_collect_handles_transport_failure():
    src = RSSSource("https://example.com/feed", transport=lambda u: (_ for _ in ()).throw(RuntimeError("boom")))
    assert src.collect() == []
