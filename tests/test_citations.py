import httpx

from app.citations import (
    bibliography,
    citation_issues,
    cited_ids,
    clean_doi,
    titles_match,
    verify_source,
)
from app.models import Source


def crossref(title="Deep Learning for Logistics", year=2022, status=200):
    def handler(request):
        if status != 200:
            return httpx.Response(status)
        return httpx.Response(200, json={"message": {"title": [title], "issued": {"date-parts": [[year]]}}})
    return httpx.Client(transport=httpx.MockTransport(handler))


def src(**kw):
    base = {"id": "S1", "title": "Deep Learning for Logistics", "year": 2022, "doi": "https://doi.org/10.1/x"}
    return Source(**{**base, **kw})


def test_clean_doi_strips_resolver_prefix():
    assert clean_doi("https://doi.org/10.1/x") == "10.1/x"


def test_verified_when_title_and_year_match():
    assert verify_source(src(), crossref()).verified


def test_year_off_by_one_is_tolerated_but_not_two():
    assert verify_source(src(year=2023), crossref(year=2022)).verified
    assert not verify_source(src(year=2025), crossref(year=2022)).verified


def test_title_mismatch_is_unverified():
    assert not verify_source(src(), crossref(title="Completely Different Paper")).verified


def test_unresolvable_or_missing_doi_is_unverified():
    assert not verify_source(src(), crossref(status=404)).verified
    assert not verify_source(src(doi=""), crossref()).verified


def test_network_failure_does_not_raise():
    def boom(request):
        raise httpx.ConnectError("down")
    assert not verify_source(src(), httpx.Client(transport=httpx.MockTransport(boom))).verified


def test_titles_match_ignores_case_and_punctuation():
    assert titles_match("Deep Learning: For Logistics!", "deep learning for logistics")


def test_citation_issues_flag_unknown_and_unverified(sources):
    text = "A [S1] and B [S5] and C [S9]."
    for i, s in enumerate(sources, 1):
        s.id = f"S{i}"
    issues = citation_issues(text, sources)
    assert any("S5" in i and "not a verified" in i for i in issues)
    assert any("S9" in i and "does not exist" in i for i in issues)
    assert not any("S1" in i for i in issues)


def test_bibliography_only_lists_cited_verified_sources(sources):
    for i, s in enumerate(sources, 1):
        s.id = f"S{i}"
    refs = bibliography("x [S1] y [S2] z [S5] [S1]", sources)
    assert refs.count("[S1]") == 1 and "[S2]" in refs
    assert "[S5]" not in refs and "[S3]" not in refs
    assert cited_ids("[S2] [S1] [S2]") == ["S2", "S1"]


def test_render_final_replaces_markers_with_apa_and_sorts_references():
    a = Source(id="S1", title="Zebra Routing", authors=["Nadia Giuffrida", "Bo Li"], year=2022,
               doi="10.1/z", venue="Sustainability", verified=True)
    b = Source(id="S2", title="Alpha Study", authors=["Ann Ames"], year=2020, doi="10.1/a", verified=True)
    c = Source(id="S3", title="Junk", authors=["X Y"], year=2020, doi="10.1/j", verified=False)
    from app.citations import render_final
    out = render_final("One claim [S1]. Another [S2][S3].\n\n## References\n\n[S1] old junk", [a, b, c])
    assert "[S" not in out
    assert "(Giuffrida & Li, 2022)" in out and "(Ames, 2020)" in out
    assert "Junk" not in out
    refs = out.split("## References")[1]
    assert refs.index("Ames, A.") < refs.index("Giuffrida, N.")
    assert "https://doi.org/10.1/z" in refs and "old junk" not in out


def test_intext_forms():
    from app.citations import intext
    mk = lambda names: Source(id="S1", title="T", authors=names, year=2021)
    assert intext(mk(["A B"])) == "(B, 2021)"
    assert intext(mk(["A B", "C D", "E F"])) == "(B et al., 2021)"
