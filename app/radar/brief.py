"""Content Opportunity Brief generator (Phase 1.3).

Deterministic, template-driven, fully testable. No AI, no LLM, no embeddings,
no paid API, no database, no Web UI. Converts a high-quality *problem signal*
(a signal already marked ``is_problem_signal == True``) into a structured
``ContentBrief`` that a human writer -- or a future AI writing layer -- can use.

Nothing here writes article prose. It only:
  * classifies the audience from topic / signal type / terminology,
  * maps the existing signal type to a search intent,
  * normalizes the observed problem into a core question,
  * builds reusable supporting questions from topic/problem templates,
  * picks a content angle and a deterministic recommended title,
  * suggests a reusable outline,
  * derives a priority from the existing opportunity_score.

All logic is pure and deterministic: the same input always yields the same
brief.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Optional

from app.radar.models import NormalizedSignal


# --------------------------------------------------------------------------- #
# Reference tables (deterministic; no external lookup)                        #
# --------------------------------------------------------------------------- #

_TOPIC_LABEL = {
    "die_casting": "Die Casting",
    "casting": "Casting",
    "cnc_machining": "CNC Machining",
    "powder_coating": "Powder Coating",
}

# material keyword -> display word (used to enrich the title label)
_MATERIAL_WORD = {
    "aluminum": "Aluminum",
    "aluminium": "Aluminium",
    "steel": "Steel",
    "magnesium": "Magnesium",
    "zinc": "Zinc",
    "titanium": "Titanium",
}

# defect regex -> human display term. Order = priority of first match.
_DEFECT_DISPLAY = [
    (r"\bporosit", "Porosity"),
    (r"\bshrinkage", "Shrinkage"),
    (r"\bwarping?\b", "Warping"),
    (r"\bflash\b", "Flash"),
    (r"\bcold shut", "Cold Shut"),
    (r"\bblister", "Blister"),
    (r"\bcracking\b", "Cracking"),
    (r"\bcrack", "Cracking"),
    (r"\bchatter", "Chatter"),
    (r"\bpeeling\b", "Peeling"),
    (r"\borange peel", "Orange Peel"),
    (r"\bbubbles?\b", "Bubbles"),
    (r"\bsurface finish", "Surface Finish"),
    (r"\broughness\b", "Roughness"),
    (r"\bdimensional\b", "Dimensional Error"),
    (r"\btoleran", "Tolerance Issue"),
    (r"\bdefect", "Defect"),
    (r"\bfail", "Failure"),
]

_TITLE_SUFFIX = {
    "causes_and_solutions": "Causes, Prevention and Solutions",
    "troubleshooting_guide": "Causes, Effects and How to Reduce It",
    "defect_prevention": "Causes, Prevention and Solutions",
    "process_optimization": "How to Optimize the Process",
    "quality_control": "How to Control Quality",
    "technical_comparison": "A Practical Comparison",
    "maintenance_guide": "A Maintenance Guide",
}

_INTENT_MAP = {
    "question": "informational",
    "how_to": "how_to",
    "troubleshooting": "troubleshooting",
    "failure": "troubleshooting",
    "defect_problem": "troubleshooting",
    "process_problem": "troubleshooting",
    "quality_problem": "troubleshooting",
    "material_problem": "troubleshooting",
    "tooling_problem": "troubleshooting",
    "surface_problem": "troubleshooting",
    "dimensional_problem": "troubleshooting",
    "comparison": "comparison",
    "recommendation": "comparison",
    "generic": "informational",
    "news": "informational",
    "promotional": "informational",
    "other": "informational",
}

# Reusable suggested outlines, keyed by content angle.
_OUTLINE = {
    "causes_and_solutions": [
        "Problem overview",
        "Common causes",
        "Process-related causes",
        "Equipment/tooling factors",
        "How to diagnose the problem",
        "Prevention methods",
        "Corrective actions",
        "Inspection checklist",
        "Conclusion",
    ],
    "troubleshooting_guide": [
        "What the problem is",
        "Common causes",
        "Tooling and setup factors",
        "Cutting parameter factors",
        "Workholding and machine factors",
        "Troubleshooting procedure",
        "Prevention checklist",
        "Conclusion",
    ],
    "defect_prevention": [
        "Problem overview",
        "Why the defect forms",
        "Process-related causes",
        "Material and application factors",
        "How to diagnose the defect",
        "Prevention methods",
        "Corrective actions",
        "Inspection checklist",
        "Conclusion",
    ],
    "process_optimization": [
        "Process overview",
        "Key parameters",
        "Common problems",
        "Optimization levers",
        "Trade-offs to consider",
        "How to measure improvement",
        "Conclusion",
    ],
    "quality_control": [
        "What to control",
        "Acceptance criteria",
        "Inspection methods",
        "Common defects",
        "Corrective actions",
        "Conclusion",
    ],
    "technical_comparison": [
        "Options compared",
        "Evaluation criteria",
        "Pros and cons",
        "Selection guidance",
        "Conclusion",
    ],
    "maintenance_guide": [
        "Scope",
        "Common failures",
        "Inspection routine",
        "Preventive maintenance",
        "Repair steps",
        "Conclusion",
    ],
}


# --------------------------------------------------------------------------- #
# Detection helpers                                                           #
# --------------------------------------------------------------------------- #

def _text(sig: NormalizedSignal) -> str:
    return f"{sig.title} {sig.text}".lower()


def _detected_term(text: str) -> Optional[str]:
    for pat, disp in _DEFECT_DISPLAY:
        if re.search(pat, text):
            return disp
    return None


def _material_label(text: str) -> str:
    for mat, word in _MATERIAL_WORD.items():
        if mat in text:
            return word
    return ""


def detect_audience(sig: NormalizedSignal) -> str:
    """Deterministic audience classification from topic + signal type + terms.

    Falls back to ``general_manufacturing`` when evidence is weak (no topic or
    no specific role signal). Never invents a role from thin evidence.
    """
    topic = sig.topic or ""
    st = sig.signal_type.value
    text = _text(sig)

    if topic == "powder_coating":
        return "coating_engineer"

    if topic == "cnc_machining":
        if any(k in text for k in ("maintenance", "repair", "breakdown", "lubricat", "spindle fail", "machine fail")):
            return "maintenance_engineer"
        if st in ("process_problem", "quality_problem"):
            return "process_engineer"
        return "cnc_machinist"

    if topic in ("die_casting", "casting"):
        if any(k in text for k in ("maintenance", "repair", "breakdown", "lubricat", "machine fail")):
            return "maintenance_engineer"
        if st in ("defect_problem", "quality_problem", "surface_problem", "dimensional_problem") or any(
            k in text for k in ("porosit", "shrinkage", "crack", "defect", "flash", "void", "blister")
        ):
            return "quality_engineer"
        if st in ("process_problem", "tooling_problem") or any(
            k in text for k in ("injection", "gating", "process", "parameter", "cycle")
        ):
            return "process_engineer"
        return "casting_engineer"

    return "general_manufacturing"


def map_search_intent(sig: NormalizedSignal) -> str:
    """Map the existing signal type to a deterministic search intent."""
    return _INTENT_MAP.get(sig.signal_type.value, "informational")


def detect_angle(sig: NormalizedSignal) -> str:
    """Pick a content angle from topic + signal type + detected problem terms."""
    st = sig.signal_type.value
    text = _text(sig)

    if st in ("comparison", "recommendation") or " vs " in text or " versus " in text:
        return "technical_comparison"
    if any(k in text for k in ("maintenance", "repair", "breakdown", "lubricat")):
        return "maintenance_guide"
    # chatter is specific and must win over the generic surface rule below
    if "chatter" in text:
        return "troubleshooting_guide"
    if any(k in text for k in ("orange peel", "peeling", "surface finish", "roughness")) or st == "surface_problem":
        return "defect_prevention"
    if st == "troubleshooting":
        return "troubleshooting_guide"
    if any(
        k in text
        for k in ("porosit", "shrinkage", "crack", "flash", "void", "blister", "warp", "dimensional", "toleran")
    ) or st in ("defect_problem", "dimensional_problem", "quality_problem"):
        return "causes_and_solutions"
    if st in ("process_problem", "tooling_problem"):
        return "process_optimization"
    if st == "quality_problem":
        return "quality_control"
    return "causes_and_solutions"


def _normalize_question(title: str) -> str:
    """Lightly normalize a question title (remove 1st/2nd person filler).

    Preserves the actual problem -- it never rewrites the question into an
    answer.
    """
    t = title.strip().rstrip("?").strip()
    m = re.match(r"^(why|how)\s+am\s+i\s+(getting|seeing|having|experiencing|dealing with)\s+(.+)$", t, re.I)
    if m:
        lead = m.group(1).lower()
        subj = m.group(3)
        subj = re.sub(r"\b(my|our|the)\b", " ", subj, flags=re.I)
        subj = re.sub(r"\s+", " ", subj).strip()
        parts = re.split(r"\s+(in|on|for|during|with|of|from)\s+", subj, maxsplit=1, flags=re.I)
        if len(parts) == 3:
            head, prep, tail = parts
            if lead == "why":
                return f"Why does {head} occur {prep} {tail}?"
            return f"How does {head} form {prep} {tail}?"
        if lead == "why":
            return f"Why does {subj} occur?"
        return f"How does {subj} form?"
    # generic personal-phrasing strip
    t2 = re.sub(r"\b(am i|i am|i'm|my|i)\b", " ", t, flags=re.I)
    t2 = re.sub(r"\s+", " ", t2).strip()
    if not t2.endswith("?"):
        t2 += "?"
    return t2 or title


def _statement_to_question(problem: str, text: str) -> str:
    """Wrap a statement-style problem into a deterministic core question."""
    p = problem.strip()
    if not p:
        p = "this manufacturing problem"
    # Drop leading signal-label words so the core question stays about the
    # actual problem, not the poster's framing.
    p = re.sub(
        r"^(troubleshooting|fixing|fix|debugging|root cause of|help with|problem:)\s+",
        "",
        p,
        flags=re.I,
    ).strip()
    if "chatter" in text:
        verb = "reduced"
    elif any(k in text for k in ("porosit", "shrinkage", "crack", "flash", "void", "blister", "defect", "warp")):
        verb = "prevented"
    elif any(k in text for k in ("surface", "orange peel", "peeling", "roughness", "finish")):
        verb = "corrected"
    else:
        verb = "addressed"
    return f"How can {p} be {verb}?"


def core_question(sig: NormalizedSignal, angle: str) -> str:
    """Extract/normalize the main question from the signal, preserving meaning."""
    title = (sig.title or "").strip()
    if title.endswith("?"):
        return _normalize_question(title)
    return _statement_to_question(title, _text(sig))


def recommended_title(sig: NormalizedSignal, angle: str) -> str:
    """Deterministic title candidate built from topic label + problem term."""
    topic = sig.topic or ""
    text = _text(sig)
    label = _TOPIC_LABEL.get(topic, "Manufacturing")
    mat = _material_label(text)
    if mat:
        label = f"{mat} {label}"
    term = _detected_term(text)
    subject = f"{label} {term}" if term else f"{label} Problem"
    suffix = _TITLE_SUFFIX.get(angle, "A Practical Guide")
    return f"{subject}: {suffix}"


def _supporting_questions(sig: NormalizedSignal, term: Optional[str], angle: str) -> list:
    """Reusable supporting-question templates keyed by topic + problem term."""
    topic = sig.topic or ""
    text = _text(sig)
    mat = _material_label(text)
    casting = "die casting" if topic == "die_casting" else "casting"

    if topic in ("die_casting", "casting"):
        lead = f"What causes {term} in {mat} {casting}?" if mat else f"What causes {term} in {casting}?"
        return [
            lead,
            f"How does trapped gas form during {casting}?",
            f"How does injection speed affect {term}?",
            f"Can gating design contribute to {term}?",
            f"How can {casting} {term} be prevented?",
            f"How can internal {term} be detected?",
        ]

    if topic == "cnc_machining":
        last = (
            f"How does {term} affect surface finish?"
            if ("surface" in text or "finish" in text)
            else f"How is {term} measured?"
        )
        return [
            f"What causes {term} during CNC machining?",
            f"How does tool overhang affect {term}?",
            f"How does cutting speed affect {term}?",
            f"Can workholding cause {term}?",
            f"How can {term} be reduced?",
            last,
        ]

    if topic == "powder_coating":
        return [
            f"What causes {term} in powder coating?",
            f"How does powder application affect {term}?",
            "Does curing temperature affect surface finish?",
            f"How can powder coating {term} be prevented?",
            f"How can an existing {term} defect be corrected?",
        ]

    # generic fallback (works for any topic/term)
    t = term or "the problem"
    return [
        f"What causes {t}?",
        f"How can {t} be diagnosed?",
        f"How can {t} be prevented?",
        f"How is {t} detected or inspected?",
        f"What process factors influence {t}?",
        f"How can {t} be corrected?",
    ]


def suggested_outline(angle: str) -> list:
    return list(_OUTLINE.get(angle, _OUTLINE["causes_and_solutions"]))


def priority_from_score(score: float, cfg: Optional[dict] = None) -> str:
    """Derive a priority band from the existing opportunity_score.

    Thresholds are configurable (config [priority]); defaults: >=50 high,
    >=30 medium, else low. Does NOT modify opportunity_score.
    """
    p = (cfg or {}).get("priority", {}) if cfg else {}
    high = float(p.get("high_threshold", 50))
    med = float(p.get("medium_threshold", 30))
    if score >= high:
        return "high"
    if score >= med:
        return "medium"
    return "low"


# --------------------------------------------------------------------------- #
# Content brief model                                                        #
# --------------------------------------------------------------------------- #

@dataclass
class ContentBrief:
    problem: str
    topic: Optional[str]
    signal_type: str
    audience: str
    search_intent: str
    recommended_title: str
    core_question: str
    supporting_questions: list = field(default_factory=list)
    content_angle: str = ""
    suggested_outline: list = field(default_factory=list)
    priority: str = "low"
    source_signal: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "problem": self.problem,
            "topic": self.topic,
            "signal_type": self.signal_type,
            "audience": self.audience,
            "search_intent": self.search_intent,
            "recommended_title": self.recommended_title,
            "core_question": self.core_question,
            "supporting_questions": self.supporting_questions,
            "content_angle": self.content_angle,
            "suggested_outline": self.suggested_outline,
            "priority": self.priority,
            "source_signal": self.source_signal,
        }


def generate_content_brief(sig: NormalizedSignal, cfg: Optional[dict] = None) -> ContentBrief:
    """Build a ContentBrief for a single problem signal (deterministic)."""
    angle = detect_angle(sig)
    audience = detect_audience(sig)
    intent = map_search_intent(sig)
    cq = core_question(sig, angle)
    text = _text(sig)
    term = _detected_term(text)
    title = recommended_title(sig, angle)
    supporting = _supporting_questions(sig, term, angle)
    outline = suggested_outline(angle)
    priority = priority_from_score(sig.opportunity_score, cfg)
    src = {"id": sig.id, "title": sig.title, "url": sig.url, "source": sig.source}
    return ContentBrief(
        problem=sig.title,
        topic=sig.topic,
        signal_type=sig.signal_type.value,
        audience=audience,
        search_intent=intent,
        recommended_title=title,
        core_question=cq,
        supporting_questions=supporting,
        content_angle=angle,
        suggested_outline=outline,
        priority=priority,
        source_signal=src,
    )


def generate_content_briefs(signals: list[NormalizedSignal], cfg: Optional[dict] = None) -> list[NormalizedSignal]:
    """Attach a ContentBrief to every problem signal; ``None`` otherwise.

    Returns the same list (mutates in place) for convenience in the pipeline.
    """
    for sig in signals:
        if getattr(sig, "is_problem_signal", False):
            sig.content_brief = generate_content_brief(sig, cfg)
        else:
            sig.content_brief = None
    return signals
