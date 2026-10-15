"""The three parsers on genuine responses (captured from the live services) and on the planted formats."""
import json
from datetime import date
from pathlib import Path

import pytest

from arastirma_defteri.sources import SourceError, arxiv, openalex, pubmed

REAL = Path(__file__).parent / "real"


def test_real_arxiv_atom_entry():
    total, papers = arxiv.parse_feed((REAL / "arxiv_atom.xml").read_bytes())
    assert total == 187609 and len(papers) == 1
    p = papers[0]
    assert p.ids == {"arxiv": "cond-mat/0011267"}                                   # an old-style identifier with its category, version removed
    assert p.title == "The electronic structure of cuprates from high energy spectroscopy"
    assert p.date == "2000-11-15" and p.year == 2000 and p.venue == "J. Electron Spectr. Relat. Phenom. 117-118, 203 (2001)"
    assert p.authors[0] == "Mark S. Golden" and len(p.authors) == 8 and p.categories == ["cond-mat.supr-con", "cond-mat.str-el"]
    assert p.abstract.startswith("We report studies of the electronic structure") and "\n" not in p.abstract and "1D->2D" in p.abstract
    assert p.url == "https://arxiv.org/abs/cond-mat/0011267" and p.source == "arxiv" and p.source_id == "cond-mat/0011267"


def test_real_pubmed_efetch_records():
    papers = pubmed.parse_efetch((REAL / "pubmed_efetch.xml").read_bytes())
    assert [p.ids["pmid"] for p in papers] == ["42767111", "42288289", "42150707"]
    p = papers[0]
    assert p.ids["doi"] == "10.1016/j.apradiso.2026.112946" and p.venue == "Appl Radiat Isot"
    assert p.title == "Investigation of TG-43 dose calculation algorithm for volumetric HDR Brachytherapy using Monte Carlo simulation in TOPAS"      # the trailing period is dropped
    assert p.date == "2026-09-19"                                                      # the electronic date, not the December issue date
    assert "192Ir" in p.abstract and "<sup>" not in p.abstract                         # inline markup is flattened
    assert p.authors[:2] == ["Ravindra Shende", "S J Dhoble"]
    assert papers[1].abstract.startswith("PURPOSE:") and "METHODS AND MATERIALS:" in papers[1].abstract and "CONCLUSIONS:" in papers[1].abstract    # section labels are kept


def test_real_openalex_works():
    papers = openalex.parse_works(json.loads((REAL / "openalex_works.json").read_text()))
    assert len(papers) == 5 and all(p.source == "openalex" and p.ids["openalex"].startswith("W") for p in papers)
    p = papers[1]
    assert p.ids["doi"] == "10.5281/zenodo.23369027" and p.date == "2026-10-14" and p.year == 2026 and len(p.authors) == 3 and p.abstract.startswith("This")
    assert papers[3].ids["pmid"] == "42862461" and papers[3].abstract == ""            # a work without an abstract index: an empty abstract, not a crash
    assert papers[0].url.startswith("https://doi.org/") or papers[0].url.startswith("http")


def test_abstract_reconstruction_from_the_inverted_index():
    assert openalex.abstract_from_index({"the": [0, 3], "dose": [1], "was": [2], "high": [4]}) == "the dose was the high"
    assert openalex.abstract_from_index(None) == "" and openalex.abstract_from_index({}) == ""


def test_pubmed_dates():
    assert pubmed._date(None) is None
    import xml.etree.ElementTree as ET
    for xml, expected in [("<D><Year>2026</Year><Month>Dec</Month></D>", "2026-12"), ("<D><Year>2026</Year><Month>03</Month><Day>7</Day></D>", "2026-03-07"),
                          ("<D><MedlineDate>2026 Oct-Dec</MedlineDate></D>", "2026"), ("<D><Year>2026</Year></D>", "2026"), ("<D><Year>x</Year></D>", None)]:
        assert pubmed._date(ET.fromstring(xml)) == expected


def test_arxiv_query_building():
    d = date(2026, 10, 1)
    assert arxiv.build_query("dose Monte Carlo", None, None) == "all:dose AND all:Monte AND all:Carlo"
    assert arxiv.build_query('dose "Monte Carlo"', "physics.med-ph", d) == 'all:dose AND all:"Monte Carlo" AND cat:physics.med-ph AND submittedDate:[202610010000 TO 209912312359]'
    assert arxiv.build_query("", "physics.med-ph") == "cat:physics.med-ph"
    assert arxiv.build_query("au:smith AND ti:dose") == "(au:smith AND ti:dose)"
    with pytest.raises(SourceError):
        arxiv.build_query("", None)
    assert arxiv.valid_category("physics.med-ph") and arxiv.valid_category("hep-th") and not arxiv.valid_category("physics med")


def test_unreadable_responses_raise_source_errors():
    with pytest.raises(SourceError):
        arxiv.parse_feed(b"Rate exceeded.")
    with pytest.raises(SourceError):
        pubmed.parse_efetch(b"{not xml")
    assert arxiv.parse_feed(b'<feed xmlns="http://www.w3.org/2005/Atom"><entry><id>http://arxiv.org/api/errors</id><title>Error</title></entry></feed>')[1] == []


def test_planted_formats_parse_to_the_planted_metadata(parsed, world):
    """Every planted record, rendered the way its source writes it and read back, keeps its title, authors, date and identifiers."""
    from arastirma_defteri.textutil import norm_title, surname_key
    assert len(parsed) == len(world.records)
    for r in world.records:
        p = parsed[(r.source, r.source_id)]
        assert norm_title(p.title) == norm_title(r.title) and r.date.startswith(p.date) and p.year == int(r.date[:4])         # a PubMed record without an electronic date is only as precise as its issue
        assert [surname_key(a) for a in p.authors] == [surname_key(a) for a in r.authors], r.source_id
        for kind, value in r.ids.items():
            assert p.ids.get(kind) == (value.lower() if kind == "doi" else value), (r.source, r.source_id, kind)
        if r.source == "arxiv":
            assert "\n" not in p.title and p.categories == ["physics.med-ph"]
        if r.source == "pubmed":
            assert not p.title.endswith(".") and "<" not in p.title


def test_planted_quirks_survive_the_parsers(parsed, world):
    pm = {r.work.wid: parsed[(r.source, r.source_id)] for r in world.records if r.source == "pubmed"}
    assert "192Ir" in pm["a1"].title and pm["a1"].title.endswith("Monte Carlo study")      # <sup> flattened
    assert any(p.abstract.startswith("BACKGROUND:") for p in pm.values()) and any(not p.abstract.startswith("BACKGROUND:") for p in pm.values())
    oa = {r.work.wid: parsed[(r.source, r.source_id)] for r in world.records if r.source == "openalex"}
    assert oa["a1"].title.startswith("Dosimetry of ¹⁹²Ir") and oa["a1"].ids["doi"] == "10.1016/j.apradiso.2026.100101"      # upper-case DOI URL normalised
    assert oa["d1"].ids == {"arxiv": "2609.10301", "openalex": oa["d1"].ids["openalex"]}                              # the arXiv DOI became an arXiv id
    ax = {r.work.wid: parsed[(r.source, r.source_id)] for r in world.records if r.source == "arxiv"}
    assert ax["c1"].ids["doi"] == "10.1088/1361-6560/ae0201" and ax["c1"].venue.startswith("Phys Med Biol") and "doi" not in ax["b1"].ids
    assert ax["b5"].title == "{C}herenkov imaging for in vivo dosimetry"
