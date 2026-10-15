"""The whole pipeline on genuine responses: a cassette recorded from a live session on 2026-10-15 (two medical-physics watches over arXiv, PubMed and OpenAlex, three papers added
by identifier), replayed with no network. Counts asserted here are frozen in the recording; the cross-checks hold on whatever the real services returned."""
import itertools
import re
from datetime import datetime, timezone
from pathlib import Path

import pytest

from arastirma_defteri import bibtex, cassette, dedup, export
from arastirma_defteri import library as libmod
from arastirma_defteri import watch as watchmod
from arastirma_defteri.sources import Endpoints, arxiv, openalex, pubmed
from test_library import brute_force

FIXTURE = Path(__file__).resolve().parents[1] / "src" / "arastirma_defteri" / "data" / "medphys.cassette.json.gz"
META, TAPE = cassette.load(FIXTURE)
NOW = datetime.strptime(META["recorded_at"], "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)


def replay():
    lib = libmod.Library(clock=lambda: NOW)
    lib.add_watch("medphys-mc", "Monte Carlo dose", ["arxiv", "pubmed", "openalex"], category="physics.med-ph", days=21, max_results=25)
    lib.add_watch("brachy", "brachytherapy dosimetry", ["pubmed", "openalex"], days=30, max_results=25)
    runs = watchmod.run_all(lib, cassette.ReplayClient(TAPE, retries=0), Endpoints(), now=NOW)
    return lib, runs


@pytest.fixture(scope="module")
def real():
    return replay()


def test_the_cassette_is_what_it_claims():
    assert META["requests"] == len(TAPE) == 10 and META["recorded_at"] == "2026-10-15T16:25:33Z"
    assert any("export.arxiv.org" in u for u in TAPE) and any("eutils.ncbi" in u and "efetch" in u for u in TAPE) and any("api.openalex.org" in u for u in TAPE)


def test_every_source_worked_on_the_real_responses(real):
    lib, runs = real
    got = {r.watch["name"]: {s.source: (s.state, s.fetched, s.new) for s in r.sources} for r in runs}
    assert got == {"medphys-mc": {"arxiv": ("ok", 3, 3), "pubmed": ("ok", 25, 25), "openalex": ("ok", 25, 20)}, "brachy": {"pubmed": ("ok", 12, 12), "openalex": ("ok", 13, 8)}}
    assert sum(len(r.new_ids) for r in runs) == len(lib.papers()) == 68 and sum(r.merged for r in runs) == 10       # 78 records, 68 papers


def test_pubmed_and_openalex_records_of_one_paper_were_merged_and_their_independent_identifiers_agree(real):
    lib, _ = real
    both = [p for p in lib.papers() if {s["source"] for s in p["sources"]} >= {"pubmed", "openalex"}]
    assert len(both) == 10
    for p in both:
        assert len(p["ids"]["doi"]) == 1 and len(p["ids"]["pmid"]) == 1 and len(p["ids"]["openalex"]) == 1
    # OpenAlex states the PubMed id of its own record, independently of the DOI the merge was decided by: the two must agree for every merged pair
    oa_records = {}
    for resp_url, entry in TAPE.items():
        if "openalex" in resp_url and "filter" in resp_url:
            for w in openalex.parse_works(__import__("json").loads(entry["body"])):
                oa_records[w.ids["openalex"]] = w
    agreeing = sum(1 for p in both if oa_records[p["ids"]["openalex"][0]].ids.get("pmid") == p["ids"]["pmid"][0])
    assert agreeing == 10
    # and the PubMed records carry the very DOI OpenAlex has
    pm_dois = {}
    for url, entry in TAPE.items():
        if "efetch" in url and "id=" in url:
            for w in pubmed.parse_efetch(entry["body"].encode()):
                pm_dois[w.ids["pmid"]] = w.ids.get("doi")
    assert all(pm_dois[p["ids"]["pmid"][0]] == p["ids"]["doi"][0] == oa_records[p["ids"]["openalex"][0]].ids["doi"] for p in both)


def test_no_paper_holds_conflicting_identifiers_and_no_two_papers_should_have_been_one(real):
    lib, _ = real
    papers = lib.papers()
    for p in papers:
        for kind in ("doi", "pmid", "arxiv"):
            assert len(p["ids"].get(kind, [])) <= 1
    seen = {}
    for p in papers:
        for kind, values in p["ids"].items():
            for v in values:
                assert (kind, v) not in seen, (kind, v)                                  # an identifier belongs to one paper only
                seen[(kind, v)] = p["id"]
    as_records = [lib.as_paper(p["id"]) for p in papers]
    for a, b in itertools.combinations(as_records, 2):
        assert not dedup.same_paper(a, b), (a.title, b.title)                             # the library is closed under its own rules: nothing left that should have merged


def test_every_real_record_parses_to_usable_metadata(real):
    lib, _ = real
    papers = lib.papers()
    assert all(p["title"].strip() and p["ids"] for p in papers)
    assert all(re.fullmatch(r"\d{4}(-\d{2}(-\d{2})?)?", p["date"]) and p["year"] == int(p["date"][:4]) for p in papers)
    with_authors = sum(1 for p in papers if p["authors"])
    with_abstract = sum(1 for p in papers if p["abstract"])
    assert with_authors >= 0.95 * len(papers) and with_abstract >= 0.7 * len(papers)
    assert not [p for p in papers if re.search(r"</?(i|b|sup|sub|em|strong|u|mml:\w+)\b", p["title"] + p["abstract"])]            # no inline markup left in any real record
    # ... while text that merely contains a less-than sign is intact (real abstracts: "P < 0.001", "(<6 h)")
    assert any("P < 0.001" in p["abstract"] for p in papers) and any("(<6 h" in p["abstract"] for p in papers)


def test_a_second_replay_finds_nothing_new_and_adding_by_identifier_finds_known_papers():
    lib, runs = replay()
    again = watchmod.run_all(lib, cassette.ReplayClient(TAPE, retries=0), Endpoints(), now=NOW)
    assert sum(len(r.new_ids) for r in again) == 0 and len(lib.papers()) == 68
    client = cassette.ReplayClient(TAPE, retries=0)
    extra = META["extra"]
    before = len(lib.papers())
    for kind, value in extra.items():
        p = watchmod.fetch_one(client, Endpoints(), kind, value)
        assert p is not None and p.title
        assert not lib.ingest(p, status="okunacak").created, kind                          # each was already in the library from the watches
    assert len(lib.papers()) == before and sum(1 for p in lib.papers() if p["status"] == "okunacak") == 3


def test_search_on_real_data_agrees_with_brute_force(real):
    lib, _ = real
    for q in ("brachytherapy", "monte carlo", "dose", "tg-43", "radiotherapy proton", "192ir", "phantom", "xyzzy", "calculation of the"):
        assert {r["ref"] for r in lib.search(q, limit=1000) if r["kind"] == "paper"} == brute_force(lib, q), q
    assert lib.search("brachytherapy")


def test_bibtex_round_trip_of_the_real_library_creates_no_new_papers(real):
    lib, _ = real
    text = bibtex.dumps([lib.as_paper(p["id"]) for p in lib.papers()])
    back = bibtex.loads(text)
    assert len(back) == 68 and len({k for k in bibtex.keys_for(back)}) == 68
    other = libmod.Library(clock=lambda: NOW)
    for p in back:
        other.ingest(p)
    assert len(other.papers()) == 68
    for p in back:
        assert not lib.ingest(p).created                                                  # every exported record maps back onto its own paper


def test_digest_of_the_real_library(real):
    lib, _ = real
    text = export.digest(lib, now=NOW, days=7)
    assert "Inbox: **68** new" in text and "## New in the inbox this period (68)" in text and "... and 53 more" in text and "**medphys-mc** (arxiv, pubmed, openalex)" in text


def test_arxiv_parser_on_the_recorded_feed():
    feed = next(e["body"] for u, e in TAPE.items() if "export.arxiv.org" in u and "search_query" in u)
    total, papers = arxiv.parse_feed(feed.encode())
    assert len(papers) == 3 and all(p.ids["arxiv"] and p.categories for p in papers) and total >= 3
