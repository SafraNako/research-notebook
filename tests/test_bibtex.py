import random
import unicodedata

import pytest

from arastirma_defteri import bibtex
from arastirma_defteri import library as libmod
from arastirma_defteri.model import Paper
from arastirma_defteri.textutil import clean_text

REAL_WORLD = r'''
% a comment outside any entry
@preamble{ "\newcommand{\noopsort}[1]{}" }
@string{ijrobp = "Int J Radiat Oncol Biol Phys"}
@string{medphys = {Medical Physics}}
@comment{ @article{ghost, title = {Not an entry}} }

@article{Smith2024,
  author  = {Smith, John A. and M{\"u}ller, Jos{\'e} and {The ATLAS Collaboration} and de la Cruz, Mar{\'\i}a},
  title   = {Monte Carlo of {$^{192}$Ir} sources \& {TG}-43 at 5\% accuracy},
  journal = ijrobp,
  year    = 2024,
  doi     = "10.1016/J.IJROBP.2024.01.001",
  eprint  = {arXiv:2401.01234v2}, archivePrefix = {arXiv}, pmid = {123456},
  abstract = {A "quoted" word, {braced} words, and a \textit{styled} one.},
}

@inproceedings(Doe2020conf,
  AUTHOR = "Doe, Jane and van der Berg, Pieter",
  Title = "Beam " # "data " # {modelling},
  booktitle = medphys # " Annual Meeting",
  year = {2020},
  url = {https://example.org/a_b?c=1&d=2}
)

@misc{nofields}
@article{broken-but-skipped title = {x}}
@book{Knuth1984, author = {Donald E. Knuth}, title = {The {\TeX}book}, publisher = {Addison-Wesley}, year = {1984}, date = {1984-01-01}}
@misc{preprint, title={A preprint}, author={One, A.}, year={2025}, eprint={hep-th/9901001}, eprinttype={arxiv}}
@misc{other, title={Not arXiv}, author={One, A.}, year={2025}, eprint={12345}, archivePrefix={SSRN}}
'''


def test_reads_a_real_world_bib_file():
    entries = bibtex.parse(REAL_WORLD)
    assert [e["key"] for e in entries] == ["Smith2024", "Doe2020conf", "Knuth1984", "preprint", "other"]        # @string/@comment/@preamble, a field-less and a malformed entry are skipped
    papers = {p.source_id: p for p in bibtex.loads(REAL_WORLD)}
    s = papers["Smith2024"]
    assert s.title == "Monte Carlo of ¹⁹²Ir sources & TG-43 at 5% accuracy" and s.venue == "Int J Radiat Oncol Biol Phys" and s.year == 2024
    assert s.authors == ["Smith, John A.", "Müller, José", "The ATLAS Collaboration", "de la Cruz, María"]
    assert s.ids == {"doi": "10.1016/j.ijrobp.2024.01.001", "arxiv": "2401.01234", "pmid": "123456"}
    assert s.abstract == 'A "quoted" word, braced words, and a styled one.'
    d = papers["Doe2020conf"]
    assert d.title == "Beam data modelling" and d.venue == "Medical Physics Annual Meeting" and d.authors == ["Doe, Jane", "van der Berg, Pieter"] and d.url == "https://example.org/a_b?c=1&d=2"
    assert papers["Knuth1984"].title == "The TeXbook" and papers["Knuth1984"].venue == "Addison-Wesley"
    assert papers["preprint"].ids == {"arxiv": "hep-th/9901001"} and papers["other"].ids == {}              # an eprint of another archive is not an arXiv id


def test_unbalanced_input_raises_a_clear_error():
    with pytest.raises(ValueError):
        bibtex.parse("@article{k, title = {unclosed")


def test_author_splitting_respects_braces():
    assert bibtex.split_authors("Smith, J. and {Barnes and Noble Research Group} and Doe, Jane") == ["Smith, J.", "Barnes and Noble Research Group", "Doe, Jane"]
    assert bibtex.split_authors("A One AND B Two and\n  C Three") == ["A One", "B Two", "C Three"] and bibtex.split_authors("") == []


# --- writing and the round trip ----------------------------------------------------------------------------------------------------------------
def sample(i=0, **kw):
    base = dict(title="Dosimetry of ¹⁹²Ir sources: a study", authors=["José Müller", "Anna van der Berg"], year=2026, venue="Med Phys", ids={"doi": "10.1/a"})
    base.update(kw)
    return Paper(**base)


def test_export_format_and_escaping():
    text = bibtex.dumps([sample(title="Cost & benefit of 5% dose_rate #1 {x} ~ ^ \\", ids={"doi": "10.1/a", "arxiv": "2301.00001", "pmid": "7"}, url="https://e.org/x")])
    assert text.startswith("@article{muller2026cost,\n") and "title = {{Cost \\& benefit of 5\\% dose\\_rate \\#1 \\{x\\} \\textasciitilde{} \\textasciicircum{} \\textbackslash{}}}," in text
    assert "author = {José Müller and Anna van der Berg}," in text and "eprint = {2301.00001}," in text and "archiveprefix = {arXiv}," in text and "pmid = {7}," in text
    assert text.endswith("}\n")
    assert bibtex.dumps([sample(venue="")]).startswith("@misc{") and bibtex.dumps([]) == ""
    assert "abstract" not in text and "abstract = {" in bibtex.dumps([sample(abstract="An abstract & more.")], with_abstract=True)


def random_paper(rng):
    pool = "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789 .,:;-()[]{}&%$#_~^\\'\"éüÅøßΩ中"
    title = "".join(rng.choice(pool) for _ in range(rng.randint(5, 80)))
    surnames = ["Müller", "van der Berg", "O'Neill", "Søndergaard", "Zhang", "Al-Hassan", "de la Cruz", "Smith"]
    authors = [f"{rng.choice(['A', 'Jos', 'Marie-Claire', 'J. R.', 'Åsa'])} {rng.choice(surnames)}" for _ in range(rng.randint(0, 4))]
    ids = {}
    if rng.random() < 0.7:
        ids["doi"] = f"10.{rng.randint(1000, 99999)}/{rng.choice(['a', 'b-c', 'x_y', 's0140-6736(20)30183-5'])}{rng.randint(0, 999)}"        # papers carry normalised (lower-case) DOIs
    if rng.random() < 0.4:
        ids["arxiv"] = rng.choice([f"{rng.randint(1000, 2612):04d}.{rng.randint(0, 99999):05d}", "hep-th/9901001", "cond-mat/0011267"])
    if rng.random() < 0.4:
        ids["pmid"] = str(rng.randint(1, 99999999))
    return Paper(title=title, authors=authors, year=rng.choice([None, 1999, 2024, 2026]), venue=rng.choice(["", "Med Phys", "Phys. Med. Biol. & Co"]), ids=ids,
                 abstract=rng.choice(["", "An abstract with 5% & $ signs and {braces}."]))


def same(a, b):
    nfc = lambda s: unicodedata.normalize("NFC", s)  # noqa: E731
    return (clean_text(nfc(a.title)) == nfc(b.title) and [nfc(x) for x in a.authors] == b.authors and a.year == b.year and clean_text(a.venue) == b.venue and a.ids == b.ids)


def test_round_trip_of_random_papers():
    rng = random.Random(42)
    checked = 0
    for _ in range(400):
        p = random_paper(rng)
        if not clean_text(p.title):
            continue
        back = bibtex.loads(bibtex.dumps([p], with_abstract=True))
        assert len(back) == 1, p.title
        assert same(p, back[0]), (p, back[0])
        assert clean_text(p.abstract) == back[0].abstract
        checked += 1
    assert checked > 380


def test_round_trip_of_many_papers_in_one_file_and_keys_are_unique():
    rng = random.Random(7)
    papers = [random_paper(rng) for _ in range(150)]
    papers = [p for p in papers if clean_text(p.title)]
    keys = bibtex.keys_for(papers)
    assert len(set(keys)) == len(keys) and all(k.isascii() and " " not in k for k in keys)
    back = bibtex.loads(bibtex.dumps(papers))
    assert len(back) == len(papers) and all(same(a, b) for a, b in zip(papers, back))
    assert [e["key"] for e in bibtex.parse(bibtex.dumps(papers))] == keys


def test_citation_keys():
    p = lambda title, authors, year: Paper(title=title, authors=authors, year=year)  # noqa: E731
    same_key = [p("Monte Carlo of dose", ["John Smith"], 2024)] * 3
    assert bibtex.keys_for(same_key) == ["smith2024monte", "smith2024montea", "smith2024monteb"]
    assert bibtex.keys_for([p("The Dose of Beams", ["José Müller"], 2020)]) == ["muller2020dose"]
    assert bibtex.keys_for([p("A study", [], None)]) == ["anonstudy"] and bibtex.keys_for([p("The of in", ["X Y"], 2020)]) == ["y2020paper"]
    many = [p("Monte Carlo of dose", ["John Smith"], 2024)] * 30
    assert len(set(bibtex.keys_for(many))) == 30


def test_exporting_a_library_and_importing_it_again_adds_nothing(parsed):
    lib = libmod.Library()
    for p in parsed.values():
        lib.ingest(p)
    before = len(lib.papers())
    exported = bibtex.dumps([lib.as_paper(p["id"]) for p in lib.papers()])
    other = libmod.Library()
    for p in bibtex.loads(exported):
        other.ingest(p, status="okunacak")
    assert len(other.papers()) == before                                           # the same papers come back (identifiers and titles survive the round trip) ...
    for p in bibtex.loads(exported):
        assert not lib.ingest(p).created                                           # ... and importing them into the original library creates nothing new
    assert len(lib.papers()) == before
