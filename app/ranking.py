"""Cheap, deterministic relevance ranking. Search APIs return topical neighbours (an "edge AI"
paper for a last-mile query); ranking before verifying keeps prompts small and on-topic."""

import re

from app.models import Source

_STOP = {"the", "and", "for", "with", "that", "this", "from", "are", "was", "not", "but", "can",
         "its", "into", "how", "their", "than", "between", "using", "based", "study", "analysis"}


def terms(text: str) -> set[str]:
    return {w for w in re.findall(r"[a-z0-9]{3,}", text.lower()) if w not in _STOP}


def relevance(src: Source, topic: str, queries: list[str]) -> float:
    q = terms(topic + " " + " ".join(queries))
    if not q:
        return 0.0
    return (2 * len(q & terms(src.title)) + len(q & terms(src.abstract))) / len(q)


def rank(sources: list[Source], topic: str, queries: list[str]) -> list[Source]:
    return sorted(sources, key=lambda s: relevance(s, topic, queries), reverse=True)
