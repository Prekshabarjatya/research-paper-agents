"""Typed payloads passed between agents. Stored in graph state as plain dicts
(model_dump) so checkpoints stay JSON-clean; nodes re-validate on read."""

from pydantic import BaseModel, Field


class Constraints(BaseModel):
    min_words: int = 0
    max_words: int = 0  # 0 = no upper bound
    citation_style: str = "APA"
    required_sections: list[str] = Field(default_factory=list)
    goals: list[str] = Field(default_factory=list)
    notes: str = ""


class Source(BaseModel):
    id: str = ""  # "S1", "S2"... assigned by the scout, stable across searches
    title: str
    authors: list[str] = Field(default_factory=list)
    year: int | None = None
    doi: str = ""
    abstract: str = ""
    url: str = ""
    venue: str = ""
    verified: bool = False  # DOI resolved and metadata matched; only these may be cited


class TopicProposal(BaseModel):
    topic: str
    rationale: str = ""
    search_queries: list[str] = Field(default_factory=list)


class SectionPlan(BaseModel):
    title: str
    goal: str
    source_ids: list[str] = Field(default_factory=list)
    target_words: int = 300


class Outline(BaseModel):
    sections: list[SectionPlan]


class Critique(BaseModel):
    approved: bool
    issues: list[str] = Field(default_factory=list)
    sections_to_fix: list[str] = Field(default_factory=list)
    needs_more_sources: bool = False
    search_hint: str = ""  # what evidence is missing, if needs_more_sources
