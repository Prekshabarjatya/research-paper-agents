"""Literature search clients. OpenAlex is primary (free, no key); Semantic
Scholar is a secondary source. Both return unverified Sources; the citation
verifier decides what may be cited."""

import httpx

from app.config import settings
from app.models import Source


def _openalex_abstract(inv: dict | None) -> str:
    if not inv:
        return ""
    positions: dict[int, str] = {}
    for word, idxs in inv.items():
        for i in idxs:
            positions[i] = word
    return " ".join(positions[i] for i in sorted(positions))


def search_openalex(query: str, client: httpx.Client, limit: int = 10) -> list[Source]:
    params = {
        "search": query,
        "per-page": limit,
        "filter": "type:article,has_doi:true",
        "select": "title,doi,publication_year,authorships,abstract_inverted_index,primary_location",
    }
    if settings.openalex_mailto:
        params["mailto"] = settings.openalex_mailto
    resp = client.get("https://api.openalex.org/works", params=params, timeout=20)
    resp.raise_for_status()
    out = []
    for w in resp.json().get("results", []):
        loc = (w.get("primary_location") or {}).get("source") or {}
        out.append(
            Source(
                title=w.get("title") or "",
                authors=[a["author"]["display_name"] for a in w.get("authorships", []) if a.get("author")],
                year=w.get("publication_year"),
                doi=w.get("doi") or "",
                abstract=_openalex_abstract(w.get("abstract_inverted_index")),
                venue=loc.get("display_name") or "",
                url=w.get("doi") or "",
            )
        )
    return [s for s in out if s.title]


def search_semantic_scholar(query: str, client: httpx.Client, limit: int = 10) -> list[Source]:
    headers = {"x-api-key": settings.semantic_scholar_api_key} if settings.semantic_scholar_api_key else {}
    resp = client.get(
        "https://api.semanticscholar.org/graph/v1/paper/search",
        params={"query": query, "limit": limit, "fields": "title,authors,year,abstract,venue,externalIds,url"},
        headers=headers,
        timeout=20,
    )
    resp.raise_for_status()
    out = []
    for p in resp.json().get("data", []):
        out.append(
            Source(
                title=p.get("title") or "",
                authors=[a["name"] for a in p.get("authors", [])],
                year=p.get("year"),
                doi=(p.get("externalIds") or {}).get("DOI", ""),
                abstract=p.get("abstract") or "",
                venue=p.get("venue") or "",
                url=p.get("url") or "",
            )
        )
    return [s for s in out if s.title]


def search_all(query: str, client: httpx.Client, limit: int = 10) -> list[Source]:
    """Primary then secondary; one source failing must not fail the search."""
    results: list[Source] = []
    for fn in (search_openalex, search_semantic_scholar):
        try:
            results.extend(fn(query, client, limit))
        except httpx.HTTPError:
            continue
    return results
