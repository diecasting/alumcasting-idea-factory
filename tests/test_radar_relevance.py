"""Tests for relevance filtering -- we want real problems, not market news."""

from app.radar.models import NormalizedSignal, Priority, RawSignal
from app.radar.normalize import normalize_signal
from app.radar.relevance import classify, is_relevant


def _norm(title, body=""):
    raw = RawSignal(
        source="s", source_type="reddit", external_id="1", title=title, body=body, url="https://x.com/1"
    )
    return normalize_signal(raw)


def test_die_casting_porosity_question_is_high():
    n = classify(_norm("Why am I getting porosity in my aluminum die casting?"))
    assert n.topic in ("die_casting", "casting")
    assert n.priority == Priority.HIGH
    assert is_relevant(n)


def test_powder_coating_surface_problem_is_high():
    n = classify(_norm("Powder coating orange peel / rough finish problem", "curing temperature issue?"))
    assert n.topic == "powder_coating"
    assert n.priority == Priority.HIGH
    assert is_relevant(n)


def test_cnc_comparison_is_relevant():
    n = classify(_norm("Haas vs Mazak for 5-axis CNC machining center?"))
    assert n.topic == "cnc_machining"
    assert is_relevant(n)


def test_market_news_is_dropped():
    n = classify(_norm("Die casting market expected to grow to $40 billion by 2030"))
    assert not is_relevant(n)
    assert n.priority == Priority.LOW


def test_off_topic_question_is_dropped():
    n = classify(_norm("How do I fix my laptop not turning on?", "help!"))
    assert n.topic is None
    assert not is_relevant(n)


def test_promotional_content_is_dropped():
    n = classify(_norm("Sign up for our webinar on casting innovations", "Contact us to subscribe."))
    assert not is_relevant(n)


def test_laptop_turning_on_not_classified_as_cnc():
    # Regression for the ambiguous-word false positive: "turning on" (a laptop
    # power issue) must NOT match CNC "turning"/"lathe".
    n = classify(_norm("How do I fix my laptop not turning on?", "it just won't power on"))
    assert n.topic != "cnc_machining"
    assert n.topic is None
    assert not is_relevant(n)
    assert "turning" not in n.matched_keywords
    assert "lathe" not in n.matched_keywords


def test_matched_keywords_are_literal_not_regex_patterns():
    # CNC lathe signal -> literal "lathe" must appear, never a regex pattern.
    n_cnc = classify(_norm("CNC lathe chatter at high spindle speed"))
    assert "lathe" in n_cnc.matched_keywords
    # Casting signal -> literal cast-related term must appear.
    n_cast = classify(_norm("Porosity in aluminum die cast part"))
    assert any("cast" in kw for kw in n_cast.matched_keywords)
    # No recorded keyword may be a raw regex pattern string.
    for kw in n_cnc.matched_keywords + n_cast.matched_keywords:
        assert "\\" not in kw, f"matched_keyword leaked a regex metachar: {kw!r}"


def test_lathe_signal_still_classified_as_cnc():
    # Legitimate CNC lathe content must still be classified as cnc_machining.
    n = classify(_norm("Best CNC lathe for small batch titanium machining?"))
    assert n.topic == "cnc_machining"
    assert is_relevant(n)
    assert "lathe" in n.matched_keywords


def test_cast_word_boundary_still_classified():
    # A genuine "cast" phrase must still be picked up as a casting topic.
    n = classify(_norm("Shrinkage defect in sand cast housing"))
    assert n.topic in ("casting", "die_casting")
    assert is_relevant(n)
