from operator import add
from typing import Annotated, TypedDict


class ResearchState(TypedDict, total=False):
    """The single shared object. No agent keeps private state."""

    prompt: str
    constraints: dict
    topic: str
    topic_feedback: str          # human feedback when a topic was rejected
    search_queries: list[str]
    sources: list[dict]          # Source dicts, verified and unverified
    thesis: str
    thesis_feedback: str
    outline: dict
    sections: dict[str, str]     # section title -> prose
    draft: str
    critique: dict
    revision_count: int
    best: dict                   # latest draft that met every mechanical requirement
    revisions: Annotated[list[str], add]   # every draft version, for audit
    tokens_used: Annotated[int, add]       # reducer: nodes report deltas
    log: Annotated[list[str], add]
