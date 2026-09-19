"""Citation integrity. A source is citable only if its DOI resolves in Crossref
and the returned title (and year, when both sides have one) match what the
search API told us. This is enforced in code, never by prompting."""

import difflib
import re

import httpx

from app.config import settings
from app.models import Source

CROSSREF = "https://api.crossref.org/works/"
MARKER = re.compile(r"\[(S\d+)\]")


def _norm(text: str) -> str:
    return re.sub(r"[^a-z0-9 ]", "", text.lower()).strip()


def titles_match(a: str, b: str, threshold: float = 0.85) -> bool:
    return difflib.SequenceMatcher(None, _norm(a), _norm(b)).ratio() >= threshold


def clean_doi(doi: str) -> str:
    return re.sub(r"^https?://(dx\.)?doi\.org/", "", doi.strip(), flags=re.IGNORECASE)


def verify_source(source: Source, client: httpx.Client) -> Source:
    """Return a copy with `verified` set. Never raises on lookup failures:
    an unverifiable source is simply not citable."""
    doi = clean_doi(source.doi)
    if not doi:
        return source.model_copy(update={"verified": False})
    params = {"mailto": settings.crossref_mailto} if settings.crossref_mailto else None
    try:
        resp = client.get(CROSSREF + doi, params=params, timeout=15)
        if resp.status_code != 200:
            return source.model_copy(update={"verified": False, "doi": doi})
        msg = resp.json().get("message", {})
    except (httpx.HTTPError, ValueError):
        return source.model_copy(update={"verified": False, "doi": doi})

    cr_title = " ".join(msg.get("title") or [])
    year = None
    for key in ("issued", "published-print", "published-online"):
        parts = (msg.get(key) or {}).get("date-parts") or [[None]]
        if parts and parts[0] and parts[0][0]:
            year = parts[0][0]
            break

    ok = bool(cr_title) and titles_match(source.title, cr_title)
    if ok and source.year and year:
        ok = abs(source.year - year) <= 1
    return source.model_copy(update={"verified": ok, "doi": doi})


def cited_ids(text: str) -> list[str]:
    seen: list[str] = []
    for m in MARKER.findall(text):
        if m not in seen:
            seen.append(m)
    return seen


def citation_issues(text: str, sources: list[Source]) -> list[str]:
    """Every [S#] marker must map to a verified source."""
    by_id = {s.id: s for s in sources}
    issues = []
    for sid in cited_ids(text):
        src = by_id.get(sid)
        if src is None:
            issues.append(f"[{sid}] is cited but does not exist in the source list.")
        elif not src.verified:
            issues.append(f"[{sid}] is cited but is not a verified source.")
    return issues


def format_reference(s: Source) -> str:
    authors = ", ".join(s.authors[:6]) + (" et al." if len(s.authors) > 6 else "") if s.authors else "Unknown"
    year = s.year or "n.d."
    tail = f" https://doi.org/{s.doi}" if s.doi else ""
    venue = f" {s.venue}." if s.venue else ""
    return f"[{s.id}] {authors} ({year}). {s.title}.{venue}{tail}"


def bibliography(text: str, sources: list[Source]) -> str:
    """Built by code from the sources actually cited, never written by the model."""
    by_id = {s.id: s for s in sources}
    lines = [format_reference(by_id[sid]) for sid in cited_ids(text) if sid in by_id and by_id[sid].verified]
    return "\n".join(lines)


def _split_name(full: str) -> tuple[str, str]:
    parts = full.replace("\u2010", "-").split()
    if not parts:
        return "Unknown", ""
    last = parts[-1]
    initials = " ".join(f"{p[0]}." for p in parts[:-1] if p)
    return last, initials


def intext(s: Source) -> str:
    last = [_split_name(a)[0] for a in s.authors]
    year = s.year or "n.d."
    if not last:
        return f"({s.title[:30]}, {year})"
    who = last[0] if len(last) == 1 else f"{last[0]} & {last[1]}" if len(last) == 2 else f"{last[0]} et al."
    return f"({who}, {year})"


def apa_reference(s: Source) -> str:
    names = [f"{last}, {ini}".strip().rstrip(",") for last, ini in map(_split_name, s.authors[:20])]
    who = names[0] if len(names) == 1 else ", ".join(names[:-1]) + ", & " + names[-1] if names else s.title
    venue = f" *{s.venue}*." if s.venue else ""
    doi = f" https://doi.org/{s.doi}" if s.doi else ""
    return f"{who} ({s.year or 'n.d.'}). {s.title}.{venue}{doi}"


def render_final(draft: str, sources: list[Source]) -> str:
    """Turn the internal [S#] marker draft into reader-facing text: (Author, Year) in-text
    citations and an alphabetical APA reference list. Markers never reach the user."""
    by_id = {s.id: s for s in sources}
    body = draft.split("\n## References", 1)[0]
    ids = cited_ids(body)

    def sub(m):
        src = by_id.get(m.group(1))
        return f" {intext(src)}" if src and src.verified else ""

    text = re.sub(r"\s*\[(S\d+)\]", sub, body)
    refs = sorted((apa_reference(by_id[i]) for i in ids if i in by_id and by_id[i].verified), key=str.lower)
    return f"{text}\n\n## References\n\n" + "\n\n".join(refs) if refs else text
