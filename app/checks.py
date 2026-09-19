"""Deterministic rubric checks. These run before the LLM critic: they are
cheap, exact, and never disagree with themselves."""

import re

from app.citations import citation_issues, render_final
from app.models import Constraints, Source


def word_count(text: str) -> int:
    return len(re.findall(r"\b\w+\b", text))


_TYPO = {"\u2010": "-", "\u2011": "-", "\u2012": "-", "\u00a0": " ", "\u202f": " ", "\u2009": " ", "\u200b": ""}


def clean_typography(text: str) -> str:
    """Models emit non-breaking hyphens and thin spaces. They cost extra tokens, break search and
    copy-paste, and defeat exact matching, so normalise them to plain ASCII equivalents."""
    return text.translate(str.maketrans(_TYPO))


def body_text(draft: str) -> str:
    """The paper without its reference list: word limits apply to the prose."""
    return draft.split("\n## References", 1)[0]


def length_target(c: Constraints) -> int | None:
    """Words to aim for: the middle of the range, leaving headroom below a hard maximum."""
    if c.min_words and c.max_words:
        return (c.min_words + c.max_words) // 2
    if c.max_words:
        return int(c.max_words * 0.9)
    if c.min_words:
        return int(c.min_words * 1.08)
    return None


def scale_outline(sections: list, c: Constraints) -> list:
    """Rescale planner word targets so they sum to the paper's target length. LLM planners
    routinely ignore the total; this makes the budget arithmetic deterministic."""
    target = length_target(c)
    total = sum(sec.target_words for sec in sections)
    if not target or not total:
        return sections
    factor = target / total
    return [sec.model_copy(update={"target_words": max(60, round(sec.target_words * factor))}) for sec in sections]


MIN_SECTION_WORDS = 40


def hard_findings(draft: str, sections: dict[str, str], c: Constraints, sources: list[Source]):
    """(issue, sections_to_rewrite) pairs. Rewriting only the affected sections keeps a fix for one
    problem from destabilising sections that were already fine."""
    findings: list[tuple[str, list[str]]] = []
    everything = list(sections)
    # Limits apply to what the reader gets: rendered text, with (Author, Year) citations counted.
    n = word_count(body_text(render_final(draft, sources)))
    if c.min_words and n < c.min_words:
        findings.append((f"Draft body is {n} words; minimum is {c.min_words}. Lengthen sections.", everything))
    if c.max_words and n > c.max_words:
        findings.append((f"Draft body is {n} words; maximum is {c.max_words}. Shorten every section.", everything))
    titles = {t.lower() for t in sections}
    for required in c.required_sections:
        if required.lower() not in titles:
            findings.append((f"Required section missing: {required}.", everything))
    for title, text in sections.items():
        if word_count(text) < MIN_SECTION_WORDS:
            findings.append((f"Section '{title}' is nearly empty ({word_count(text)} words).", [title]))
    for issue in citation_issues(draft, sources):
        bad = issue[1 : issue.index("]")]
        where = [t for t, text in sections.items() if f"[{bad}]" in text]
        findings.append((issue, where or everything))
    return findings


def hard_issues(draft: str, sections: dict[str, str], c: Constraints, sources: list[Source]) -> list[str]:
    return [issue for issue, _ in hard_findings(draft, sections, c, sources)]
