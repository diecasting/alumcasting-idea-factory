"""Tests for deduplication (exact URL + near-duplicate title)."""

from app.radar.models import NormalizedSignal
from app.radar.dedupe import dedupe


def _sig(topic, title, url, text=None):
    return NormalizedSignal(
        id=url[-8:],
        source="s",
        source_type="reddit",
        topic=topic,
        title=title,
        text=text or title,
        url=url,
    )


def test_exact_url_dedupe():
    a = _sig("die_casting", "Porosity in die casting", "https://x.com/1")
    b = _sig("die_casting", "Porosity in die casting", "https://x.com/1")
    c = _sig("die_casting", "Different title", "https://x.com/2")
    out = dedupe([a, b, c])
    assert len(out) == 2
    assert sum(1 for s in out if s.url == "https://x.com/1") == 1


def test_near_duplicate_title_same_topic_dropped():
    # Token-identical titles (only punctuation differs) are near-duplicates.
    a = _sig("cnc_machining", "Troubleshooting CNC milling chatter", "https://x.com/a")
    b = _sig("cnc_machining", "Troubleshooting CNC milling chatter.", "https://x.com/b")
    out = dedupe([a, b])
    assert len(out) == 1


def test_distinct_titles_same_topic_kept():
    # Different words ("mill" vs "lathe") must NOT be merged at 0.9 threshold.
    a = _sig("cnc_machining", "How to fix CNC chatter on a mill", "https://x.com/a")
    b = _sig("cnc_machining", "How to fix CNC chatter on a lathe", "https://x.com/b")
    out = dedupe([a, b])
    assert len(out) == 2


def test_different_topic_same_title_kept():
    a = _sig("cnc_machining", "Surface finish question", "https://x.com/a")
    b = _sig("powder_coating", "Surface finish question", "https://x.com/b")
    out = dedupe([a, b])
    assert len(out) == 2


def test_unrelated_titles_kept():
    out = dedupe([
        _sig("casting", "Porosity near gate", "https://x.com/1"),
        _sig("casting", "Shrinkage in riser", "https://x.com/2"),
    ])
    assert len(out) == 2
