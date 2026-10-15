"""Identity resolution against the planted truth: the same literature arriving in any order must always resolve to the same papers."""
import itertools
import random

import pytest

from arastirma_defteri import dedup
from arastirma_defteri import library as libmod
from arastirma_defteri.fakeworld import KNOWN_MISSES
from arastirma_defteri.model import Paper


def key(p):
    return (p.source, p.source_id)


def partition_of_clusters(order, clusters):
    return {frozenset(key(order[i]) for i in c) for c in clusters}


def partition_of_library(lib):
    return {frozenset((s["source"], s["source_id"]) for s in lib.get(row["id"])["sources"]) for row in lib.db.execute("SELECT id FROM papers")}


def truth(world):
    return {frozenset(c) for c in world.truth_partition()}


def test_the_corpus_is_what_the_tests_assume(world):
    assert len(world.works) == 43 and len(world.records) == 70
    assert {r.source for r in world.records} == {"arxiv", "pubmed", "openalex"}
    assert len(truth(world)) == 43 + 1                                         # 43 works, one of which (the known miss) is expected to stay in two pieces


def test_clustering_recovers_the_truth_in_the_original_and_in_many_shuffled_orders(parsed, world):
    papers = list(parsed.values())
    expected = truth(world)
    assert partition_of_clusters(papers, dedup.cluster(papers)) == expected
    for seed in range(60):
        order = list(papers)
        random.Random(seed).shuffle(order)
        assert partition_of_clusters(order, dedup.cluster(order)) == expected, seed


def test_the_library_recovers_the_same_partition_in_shuffled_orders(parsed, world):
    expected = truth(world)
    papers = list(parsed.values())
    for seed in range(25):
        order = list(papers)
        random.Random(100 + seed).shuffle(order)
        lib = libmod.Library()
        for p in order:
            lib.ingest(p)
        assert partition_of_library(lib) == expected, seed
        assert lib.db.execute("SELECT COUNT(*) FROM papers").fetchone()[0] == len(expected)


def test_pairwise_precision_and_recall(parsed, world):
    """Over every pair of records: no pair of different works is ever merged (precision 1.0); every same-work pair is merged except the declared miss."""
    papers = list(parsed.values())
    clusters = dedup.cluster(papers)
    cluster_of = {}
    for n, c in enumerate(clusters):
        for i in c:
            cluster_of[key(papers[i])] = n
    work_of = {(r.source, r.source_id): r.work.wid for r in world.records}
    tp = fp = fn = 0
    for a, b in itertools.combinations(work_of, 2):
        same_work, same_cluster = work_of[a] == work_of[b], cluster_of[a] == cluster_of[b]
        tp += same_work and same_cluster
        fp += (not same_work) and same_cluster
        fn += same_work and not same_cluster
    same_work_pairs = sum(1 for a, b in itertools.combinations(work_of, 2) if work_of[a] == work_of[b])
    assert fp == 0                                                              # a false merge would put two different papers under one note
    assert fn == 1 and tp == same_work_pairs - 1 == 31                         # every same-work pair but the single declared miss (the two records of the 'm1' work)


def test_the_known_miss_is_a_documented_limitation_not_a_surprise(parsed, world):
    assert KNOWN_MISSES == [{"m1"}]
    arx = parsed[("arxiv", "2608.11001")]
    pub = next(p for k, p in parsed.items() if k[0] == "pubmed" and p.ids.get("pmid") == "50001001")
    assert arx.title != pub.title and dedup.same_paper(arx, pub) is False      # "A comparison of ... engines" against "... engines: a comparison"
    assert dedup.strong_match(dedup.id_sets(arx.ids), dedup.id_sets(pub.ids)) is None       # no identifier links them either


TRAPS = [                                                                       # pairs of records of DIFFERENT works that must stay apart
    ("t1a", "t1b", "Part I and Part II of a series"), ("t2a", "t2b", "the same generic title"), ("t3a", "t3b", "same title and author, years apart"),
    ("t4a", "t4b", "same title and first author, different DOIs"), ("a5", "t5", "an original and its correction"), ("t6a", "t6b", "titles that differ by a number"),
]


@pytest.mark.parametrize("wa,wb,why", TRAPS)
def test_trap_pairs_stay_separate(parsed, world, wa, wb, why):
    ra = [parsed[key_of(r)] for r in world.records if r.work.wid == wa]
    rb = [parsed[key_of(r)] for r in world.records if r.work.wid == wb]
    assert ra and rb
    for a in ra:
        for b in rb:
            assert dedup.same_paper(a, b) is False, why


def key_of(r):
    return (r.source, r.source_id)


POSITIVES = ["a1", "a2", "b1", "b2", "b3", "b4", "b5", "c1", "d1", "d2"]


@pytest.mark.parametrize("wid", POSITIVES)
def test_records_of_one_work_resolve_together(parsed, world, wid):
    recs = [parsed[key_of(r)] for r in world.records if r.work.wid == wid]
    assert len(recs) >= 2
    assert len(dedup.cluster(recs)) == 1


def P(title="A reasonably long title about dose", authors=("John Smith",), year=2026, **ids):
    return Paper(title=title, authors=list(authors), year=year, ids=ids, source="x", source_id=str(random.random()))


def test_identifier_rules():
    sm = dedup.same_paper
    assert sm(P(doi="10.1/a"), P("Other", ("Zed",), 1999, doi="10.1/a")) is True                      # a shared DOI is enough, whatever the titles
    assert sm(P(pmid="1"), P("Other", ("Zed",), 1999, pmid="1")) is True
    assert sm(P(openalex="W1"), P("Other", ("Zed",), 1999, openalex="W1")) is True
    assert sm(P(doi="10.1/a"), P(doi="10.1/b")) is False                                                # same everything else, different DOIs: different papers
    assert sm(P(pmid="1"), P(pmid="2")) is False and sm(P(arxiv="2301.00001"), P(arxiv="2301.00002")) is False
    assert sm(P(arxiv="2301.00001"), P("Other", ("Zed",), 1999, arxiv="2301.00001")) is True
    assert sm(P(arxiv="2301.00001", doi="10.1/a"), P("Other", ("Zed",), 1999, arxiv="2301.00001", doi="10.1/b")) is False      # a shared arXiv id does not override conflicting DOIs
    assert sm(P(doi="10.1/a", pmid="1"), P(doi="10.1/a", pmid="2")) is True                                # the DOI decides


def test_title_rules_apply_only_when_identifiers_say_nothing():
    sm = dedup.same_paper
    long_title = "Monte Carlo dose calculation for proton therapy"
    assert sm(P(long_title, arxiv="2301.00001"), P(long_title + ".", doi="10.1/a")) is True
    assert sm(P(long_title, ("Smith, J.",)), P(long_title, ("John A. Smith",), doi="10.1/a")) is True        # surname forms agree
    assert sm(P(long_title, ("John Smith",)), P(long_title, ("John Smyth",))) is False                    # another first author
    assert sm(P(long_title, year=2024), P(long_title, year=2026)) is False and sm(P(long_title, year=2025), P(long_title, year=2026)) is True
    assert sm(P("Introduction"), P("Introduction")) is False and sm(P("Dose of beams"), P("Dose of beams")) is False      # generic or short: never on the title alone
    assert sm(P("Monte Carlo dose for protons", ()), P("Monte Carlo dose for protons", ("John Smith",))) is False      # no authors on one side and a title of only five words
    assert sm(P(long_title, ()), P(long_title, ("John Smith",))) is True                                    # ... but seven words and the same year are enough
    assert sm(P("A really quite long title about dosimetry of beams", (), 2026), P("A really quite long title about dosimetry of beams", (), 2026)) is True
    assert sm(P(long_title, ("John Smith",), None), P(long_title, ("John Smith",), 2026)) is True         # an unknown year does not block


def test_ingesting_the_same_record_twice_changes_nothing(parsed):
    lib = libmod.Library()
    p = next(iter(parsed.values()))
    first = lib.ingest(p)
    again = lib.ingest(p)
    assert first.created and not again.created and again.paper_id == first.paper_id
    assert lib.db.execute("SELECT COUNT(*) FROM papers").fetchone()[0] == 1 and lib.db.execute("SELECT COUNT(*) FROM sightings").fetchone()[0] == 1


def test_a_record_that_bridges_two_papers_merges_them():
    lib = libmod.Library()
    a = lib.ingest(Paper("Alpha title of some length here", ["A B"], 2026, ids={"arxiv": "2301.00001"}, source="arxiv", source_id="2301.00001")).paper_id
    b = lib.ingest(Paper("Beta completely different words here", ["C D"], 2026, ids={"doi": "10.1/x"}, source="pubmed", source_id="1")).paper_id
    assert a != b
    r = lib.ingest(Paper("Whatever", ["E F"], 2026, ids={"arxiv": "2301.00001", "doi": "10.1/x"}, source="openalex", source_id="W1"))
    assert r.merged == [max(a, b)] and r.paper_id == min(a, b) and lib.db.execute("SELECT COUNT(*) FROM papers").fetchone()[0] == 1
    assert lib.get(r.paper_id)["ids"] == {"arxiv": ["2301.00001"], "doi": ["10.1/x"], "openalex": ["W1"]} or lib.get(r.paper_id)["ids"]["doi"] == ["10.1/x"]


def test_a_record_that_would_bridge_papers_with_conflicting_dois_does_not():
    lib = libmod.Library()
    a = lib.ingest(Paper("Same words in the title of the work", ["A B"], 2026, ids={"doi": "10.1/a"}, source="pubmed", source_id="1")).paper_id
    b = lib.ingest(Paper("Same words in the title of the work", ["A B"], 2026, ids={"doi": "10.1/b"}, source="openalex", source_id="W2")).paper_id
    assert a != b
    r = lib.ingest(Paper("x", [], 2026, ids={"pmid": "7"}, source="pubmed", source_id="7"))
    assert lib.db.execute("SELECT COUNT(*) FROM papers").fetchone()[0] == 3 and r.created


def test_a_preprint_that_fits_two_different_papers_is_kept_separate_rather_than_guessed():
    lib = libmod.Library()
    a = lib.ingest(Paper("Machine learning for dosimetry in practice", ["John Smith"], 2026, ids={"doi": "10.1/a"}, source="pubmed", source_id="1")).paper_id
    b = lib.ingest(Paper("Machine learning for dosimetry in practice", ["John Smith"], 2026, ids={"doi": "10.1/b"}, source="openalex", source_id="W2")).paper_id
    pre = lib.ingest(Paper("Machine learning for dosimetry in practice", ["John Smith"], 2026, ids={"arxiv": "2601.00001"}, source="arxiv", source_id="2601.00001"))
    assert pre.created and pre.paper_id not in (a, b)                           # either could be its journal version; the library does not pick one


def test_cluster_and_library_agree_on_random_subsets_and_orders(parsed):
    papers = list(parsed.values())
    rng = random.Random(7)
    for _ in range(40):
        subset = rng.sample(papers, rng.randint(5, len(papers)))
        lib = libmod.Library()
        for p in subset:
            lib.ingest(p)
        assert partition_of_library(lib) == partition_of_clusters(subset, dedup.cluster(subset))
