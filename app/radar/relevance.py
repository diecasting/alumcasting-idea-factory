"""Relevance filtering: find real problems, not market news.

This is keyword/lexicon based ONLY -- no LLM, no paid API. The goal is to keep
signals that look like a real person asking, troubleshooting, or reporting a
defect/quality/process problem, and to drop generic company news, stock talk,
press releases, ads, jobs, and promotional content.
"""

from __future__ import annotations

import re

from app.radar.models import NormalizedSignal, Priority, SignalType, TOPICS

# Topic lexicons (case-insensitive regex; word-ish boundaries where useful).
TOPIC_KEYWORDS: dict[str, list[str]] = {
    "die_casting": [
        r"die[\s-]?cast",
        r"diecasting",
        r"hpdc",
        r"high[\s-]?pressure die",
        r"aluminium? die",
        r"aluminum die",
    ],
    "casting": [
        r"die[\s-]?cast",
        r"diecasting",
        r"sand cast",
        r"investment cast",
        r"permanent[\s-]?mou?ld",
        r"gravity cast",
        r"low[\s-]?pressure cast",
        r"metal casting",
        r"casting defect",
        r"casting porosity",
        r"cast part",
        r"\bcast(?:ing|ed)?\b",
    ],
    "cnc_machining": [
        r"cnc",
        r"cnc machin",
        r"cnc mill",
        r"cnc lathe",
        r"cnc router",
        r"5[\s-]?axis",
        r"machin(?:ing|e)? center",
        r"machined part",
        r"milling",
        r"lathe",
        r"feed rate",
        r"spindle",
        r"g[\s-]?code",
    ],
    "powder_coating": [
        r"powder[\s-]?coat",
        r"powdercoat",
        r"powder finish",
        r"electrostatic coat",
        r"powder cure",
        r"powder booth",
    ],
}

# HIGH-value signal types (real problems people need help with).
HIGH_INDICATORS: dict[SignalType, list[str]] = {
    SignalType.QUESTION: [
        r"\?",
        r"\bwhy\b",
        r"\bhow\b",
        r"\bwhat\b",
        r"\bcan.?t\b",
        r"\bwon.?t\b",
        r"\bhelp\b",
        r"\badvice\b",
        r"\bquestion\b",
    ],
    SignalType.TROUBLESHOOTING: [
        r"\bproblem\b",
        r"\bissue\b",
        r"\btroubleshoot",
        r"\bnot working\b",
        r"\bdoesn.?t\b",
        r"\berror\b",
        r"\bstuck\b",
    ],
    SignalType.FAILURE: [
        r"\bfail",
        r"\bfailure\b",
        r"\bbroke\b",
        r"\bcrack",
        r"\bburn",
        r"\bmelt",
        r"\brupture",
    ],
    SignalType.DEFECT_PROBLEM: [
        r"\bporosit",
        r"\bvoid",
        r"\bshrinkage",
        r"\bdefect",
        r"\bwarping?",
        r"\bflash\b",
        r"\bcold shut",
        r"\binclusion",
        r"\bblister",
        r"\bmisrun",
    ],
    SignalType.SURFACE_PROBLEM: [
        r"\bsurface\b",
        r"\broughness\b",
        r"\bfinish",
        r"\bcosmetic",
        r"\btexture\b",
        r"\bscratch",
        r"\bpitting",
        r"\borange peel",
    ],
    SignalType.DIMENSIONAL_PROBLEM: [
        r"\bdimension",
        r"\btoleran",
        r"\bout of spec",
        r"\bwarp",
        r"\bshrink",
        r"\bflatness",
        r"\bconcentric",
        r"\bout of round",
    ],
    SignalType.MATERIAL_PROBLEM: [
        r"\bmaterial\b",
        r"\balloy\b",
        r"\btemper\b",
        r"\bcomposition\b",
        r"\bgrade\b",
        r"\bspecif",
    ],
    SignalType.TOOLING_PROBLEM: [
        r"\btool",
        r"\bmold",
        r"\bdie (?:tool|steel|wear|life)",
        r"\beject",
        r"\bcooling",
        r"\bgating",
        r"\bsprue",
        r"\brunner",
    ],
    SignalType.QUALITY_PROBLEM: [
        r"\bquality\b",
        r"\binspect",
        r"\bnd\b",
        r"\bx-?ray\b",
        r"\bct scan",
        r"\bleak",
        r"\bseal\b",
        r"\bctq\b",
    ],
    SignalType.PROCESS_PROBLEM: [
        r"\bprocess\b",
        r"\bparameter",
        r"\btemperature\b",
        r"\binjection",
        r"\bshot\b",
        r"\bcycle time",
        r"\bvent",
        r"\bfilling",
        r"\bpressure\b",
    ],
}

# Comparison / recommendation / how-to (MEDIUM value).
MEDIUM_INDICATORS: dict[SignalType, list[str]] = {
    SignalType.COMPARISON: [
        r"\bvs\.?\b",
        r"\bversus\b",
        r"\bcompar",
        r"\bbetter\b",
        r"\bdifference between\b",
    ],
    SignalType.RECOMMENDATION: [
        r"\brecommend",
        r"\bbest\b",
        r"\bwhich\b",
        r"\bsuggest",
        r"\bbrand\b",
    ],
    SignalType.HOW_TO: [
        r"\bhow to\b",
        r"\btutorial\b",
        r"\bguide\b",
        r"\bstep by step\b",
        r"\bsetup\b",
    ],
}

# LOW-value noise (promotional / news / jobs / market).
LOW_INDICATORS: list[str] = [
    r"\bstock\b",
    r"\bshare price\b",
    r"\bmarket (?:size|growth|forecast|report|share)\b",
    r"\bbillion\b",
    r"\bpress release\b",
    r"\bannounc",
    r"\bwebinar\b",
    r"\bsale\b",
    r"\bdiscount\b",
    r"\bjob (?:opening|posting|vacanc)",
    r"\bhiring\b",
    r"\bsponsored\b",
    r"\badvertis",
    r"\bnow hiring\b",
    r"\bsign up\b",
    r"\bcontact us\b",
    r"\bsubscribe\b",
]

_HIGH_TYPES = set(HIGH_INDICATORS.keys())
_MEDIUM_TYPES = set(MEDIUM_INDICATORS.keys())


def _match(text: str, patterns: list[str]):
    hits = 0
    matched: list[str] = []
    for p in patterns:
        m = re.search(p, text)
        if m:
            hits += 1
            matched.append(m.group(0))
    return hits, matched


def _best_topic(text: str):
    best = None
    best_hits = 0
    best_matched: list[str] = []
    for topic, patterns in TOPIC_KEYWORDS.items():
        hits, matched = _match(text, patterns)
        if hits > best_hits:
            best, best_hits, best_matched = topic, hits, matched
    return best, best_hits, best_matched


def _best_signal_type(text: str):
    best = SignalType.OTHER
    best_hits = 0
    best_matched: list[str] = []
    for stype, patterns in HIGH_INDICATORS.items():
        hits, matched = _match(text, patterns)
        if hits > best_hits:
            best, best_hits, best_matched = stype, hits, matched
    if best_hits == 0:
        for stype, patterns in MEDIUM_INDICATORS.items():
            hits, matched = _match(text, patterns)
            if hits > best_hits:
                best, best_hits, best_matched = stype, hits, matched
    return best, best_hits, best_matched


def classify(norm: NormalizedSignal) -> NormalizedSignal:
    """Attach topic, signal_type, priority and relevance_score in place."""
    text = f"{norm.title} {norm.text}".lower()
    topic, topic_hits, topic_matched = _best_topic(text)
    sig_type, type_hits, type_matched = _best_signal_type(text)

    matched_keywords = sorted(set(topic_matched + type_matched))

    if topic is None:
        # No manufacturing topic -> not relevant to us.
        norm.topic = None
        norm.signal_type = sig_type
        norm.priority = Priority.LOW
        norm.relevance_score = 0.0
        norm.matched_keywords = matched_keywords
        return norm

    score = min(1.0, 0.4 + 0.06 * topic_hits + 0.05 * type_hits)

    if sig_type in _HIGH_TYPES:
        priority = Priority.HIGH
    elif sig_type in _MEDIUM_TYPES and type_hits >= 1:
        priority = Priority.MEDIUM
    else:
        priority = Priority.MEDIUM

    # Promotional / news / jobs demote the signal.
    low = any(re.search(p, text) for p in LOW_INDICATORS)
    if low:
        if sig_type in (
            SignalType.QUESTION,
            SignalType.TROUBLESHOOTING,
            SignalType.FAILURE,
            SignalType.DEFECT_PROBLEM,
        ):
            # Still a genuine problem -- keep it, just one notch lower.
            priority = Priority.MEDIUM
        else:
            priority = Priority.LOW
        score *= 0.5

    norm.topic = topic
    norm.signal_type = sig_type
    norm.priority = priority
    norm.relevance_score = round(score, 3)
    norm.matched_keywords = matched_keywords
    return norm


def is_relevant(norm: NormalizedSignal) -> bool:
    """Keep only signals with a manufacturing topic and priority above LOW."""
    return norm.topic is not None and norm.priority != Priority.LOW
