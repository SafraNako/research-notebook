import random
import re

import pytest

from arastirma_defteri import library as libmod
from arastirma_defteri.model import Paper
from arastirma_defteri.textutil import strip_accents


def rec(title="A study of dose in radiotherapy planning", authors=("John Smith",), year=2026, source="pubmed", sid="1", **kw):
    ids = kw.pop("ids", {})
    return Paper(title=title, authors=list(authors), year=year, date=kw.pop("date", f"{year}-09-01"), source=source, source_id=sid, ids=ids, **kw)


def test_ingest_creates_and_describes_a_paper(lib):
    r = lib.ingest(rec(ids={"doi": "10.1/a", "pmid": "5"}, abstract="Some abstract.", venue="Med Phys"))
    assert r.created and r.merged == []
    p = lib.get(r.paper_id)
    assert p["status"] == "yeni" and p["title"] == "A study of dose in radiotherapy planning" and p["authors"] == ["John Smith"] and p["year"] == 2026
    assert p["ids"] == {"doi": ["10.1/a"], "pmid": ["5"]} and p["sources"][0]["source"] == "pubmed" and p["added_at"] == "2026-10-15T12:00:00Z"


def test_merging_fills_gaps_and_keeps_the_better_data(lib):
    a = lib.ingest(rec(ids={"doi": "10.1/a"}, abstract="Short.", authors=("J. Smith",), source="arxiv", sid="x")).paper_id
    lib.ingest(rec(ids={"doi": "10.1/a", "pmid": "9"}, abstract="A much longer abstract of the paper.", authors=("John Smith", "Jane Doe"), venue="Med Phys",
                   url="https://example.org/p", title="A different Title form", source="pubmed", sid="9"))
    p = lib.get(a)
    assert p["abstract"] == "A much longer abstract of the paper." and p["authors"] == ["John Smith", "Jane Doe"] and p["venue"] == "Med Phys" and p["url"] == "https://example.org/p"
    assert p["title"] == "A study of dose in radiotherapy planning"              # the title first seen is kept
    assert p["ids"] == {"doi": ["10.1/a"], "pmid": ["9"]} and {s["source"] for s in p["sources"]} == {"arxiv", "pubmed"}
    assert len(lib.papers()) == 1


def test_a_shorter_abstract_or_author_list_never_overwrites_a_longer_one(lib):
    a = lib.ingest(rec(ids={"doi": "10.1/a"}, abstract="A long and informative abstract.", authors=("A One", "B Two", "C Three"))).paper_id
    lib.ingest(rec(ids={"doi": "10.1/a"}, abstract="", authors=("A One",), source="openalex", sid="W1"))
    p = lib.get(a)
    assert p["abstract"] == "A long and informative abstract." and len(p["authors"]) == 3


@pytest.mark.parametrize("existing,incoming,expected", [("yeni", "yeni", "yeni"), ("okundu", "yeni", "okundu"), ("atildi", "yeni", "atildi"), ("okunacak", "yeni", "okunacak"),
                                                         ("yeni", "okunacak", "okunacak"), ("okunuyor", "yeni", "okunuyor")])
def test_what_the_user_decided_beats_what_a_watch_found(lib, existing, incoming, expected):
    a = lib.ingest(rec(ids={"doi": "10.1/a"})).paper_id
    lib.set_status(a, existing)
    lib.ingest(rec(ids={"doi": "10.1/a"}, source="openalex", sid="W1"), status=incoming)
    assert lib.get(a)["status"] == expected


def test_a_dismissed_paper_does_not_come_back_as_new(lib):
    a = lib.ingest(rec(ids={"doi": "10.1/a"})).paper_id
    lib.set_status(a, "atildi")
    again = lib.ingest(rec(ids={"doi": "10.1/a"}, source="openalex", sid="W9"))
    assert not again.created and lib.get(a)["status"] == "atildi" and lib.papers(status="yeni") == []


def test_manual_merge_moves_everything(lib):
    a = lib.ingest(rec("First paper with a long enough title", ids={"doi": "10.1/a"})).paper_id
    b = lib.ingest(rec("Second paper with other words entirely", ids={"doi": "10.1/b"}, sid="2")).paper_id
    lib.tag(b, add=["Monte Carlo", "#dose"])
    lib.add_note("a thought about b", pid=b)
    lib.set_status(b, "okundu", rating=4)
    lib.set_status(a, "okunacak", rating=2)
    lib.merge(a, b)
    p = lib.get(a)
    assert lib.get(b) is None and p["status"] == "okundu" and p["rating"] == 4 and p["tags"] == ["dose", "monte-carlo"]
    assert {"10.1/a", "10.1/b"} == set(p["ids"]["doi"]) and [n["body"] for n in lib.notes(a)] == ["a thought about b"]
    assert lib.search("thought") and lib.search("thought")[0]["ref"] == a
    with pytest.raises(ValueError):
        lib.merge(a, a)
    with pytest.raises(ValueError):
        lib.merge(a, 999)


def test_status_and_rating_validation(lib):
    a = lib.ingest(rec()).paper_id
    for bad in ("done", "", "NEW"):
        with pytest.raises(ValueError):
            lib.set_status(a, bad)
    for bad in (0, 6, -1):
        with pytest.raises(ValueError):
            lib.set_status(a, "okundu", rating=bad)
    with pytest.raises(ValueError):
        lib.set_status(999, "okundu")
    lib.set_status(a, "okundu", rating=5)
    lib.set_status(a, "okunuyor")                                                # a status change without a rating keeps the rating
    assert lib.get(a)["rating"] == 5 and lib.get(a)["status"] == "okunuyor"


def test_tags_are_normalised_deduplicated_and_removable(lib):
    a = lib.ingest(rec()).paper_id
    lib.tag(a, add=["Monte Carlo", "monte  carlo", "#Brachy", "  "])
    assert lib.get(a)["tags"] == ["brachy", "monte-carlo"]
    lib.tag(a, remove=["Brachy"])
    assert lib.get(a)["tags"] == ["monte-carlo"] and [p["id"] for p in lib.papers(tag="monte-carlo")] == [a] and lib.papers(tag="brachy") == []


def test_notes_lifecycle(lib):
    a = lib.ingest(rec()).paper_id
    n1 = lib.add_note("  First thought.  ", pid=a)
    lib.add_note("Second.", pid=a, title="Reply")
    free = lib.add_note("A thought about nothing in particular", title="Idea")
    assert [n["body"] for n in lib.notes(a)] == ["First thought.", "Second."] and [n["title"] for n in lib.notes(None)] == ["Idea"] and free > n1
    assert lib.search("particular")[0]["kind"] == "note"
    lib.delete_note(n1)
    assert len(lib.notes(a)) == 1 and lib.search("First") == []
    for bad in ("", "   "):
        with pytest.raises(ValueError):
            lib.add_note(bad, pid=a)
    with pytest.raises(ValueError):
        lib.add_note("x", pid=999)
    with pytest.raises(ValueError):
        lib.delete_note(999)


def test_listing_filters_and_orders(lib):
    ids = []
    for i, (st, d) in enumerate([("yeni", "2026-01-05"), ("okundu", "2026-03-01"), ("okunacak", "2026-02-01")]):
        r = lib.ingest(rec(f"Paper number {i} with a long enough title", sid=str(i), date=d, ids={"pmid": str(i + 1)}))
        lib.set_status(r.paper_id, st)
        ids.append(r.paper_id)
        lib.clock_box[0] = lib.clock_box[0].replace(day=lib.clock_box[0].day + 1)
    assert [p["id"] for p in lib.papers(status="okundu")] == [ids[1]]
    assert [p["id"] for p in lib.papers(order="date")] == [ids[1], ids[2], ids[0]] and [p["id"] for p in lib.papers()] == ids[::-1]
    assert [p["id"] for p in lib.papers(limit=1)] == [ids[2]] and lib.count() == {"yeni": 1, "okundu": 1, "okunacak": 1}
    assert [p["id"] for p in lib.papers(since_added="2026-10-17T00:00:00Z")] == [ids[2]]


# --- full-text search --------------------------------------------------------------------------------------------------------------------------
def brute_force(lib, query):
    """The papers whose indexed text contains every query word as the start of some word: computed from the stored data, independently of FTS5."""
    import unicodedata

    def words(text):
        return re.findall(r"[^\W_]+", strip_accents(unicodedata.normalize("NFKC", text)).casefold())
    want = words(query)
    hits = set()
    for p in lib.papers():
        text = " ".join([p["title"], p["abstract"], " ".join(p["authors"]), " ".join(p["tags"]), " ".join(n["title"] + " " + n["body"] for n in lib.notes(p["id"]))])
        tokens = words(text)
        if want and all(any(t.startswith(w) for t in tokens) for w in want):
            hits.add(p["id"])
    return hits


def test_search_agrees_with_brute_force_on_the_planted_literature(parsed):
    lib = libmod.Library()
    for p in parsed.values():
        lib.ingest(p)
    rng = random.Random(3)
    papers = lib.papers()
    words_of = lambda p: sorted(set(re.findall(r"[^\W_]{3,}", strip_accents(p["title"] + " " + p["abstract"]).casefold())))  # noqa: E731
    vocabulary = sorted({w for p in papers for w in words_of(p)})
    checked = 0
    for _ in range(250):
        if rng.random() < 0.7:                                                      # words from one paper: a guaranteed hit
            query = " ".join(rng.sample(words_of(rng.choice(papers)), rng.randint(1, 3)))
        else:                                                                        # words from anywhere: often no hit, and then both must agree on nothing
            query = " ".join(rng.sample(vocabulary, rng.randint(1, 3)))
        if rng.random() < 0.4:
            query = " ".join(w[: max(2, len(w) - rng.randint(0, 3))] for w in query.split())      # prefixes
        got = {r["ref"] for r in lib.search(query, limit=1000) if r["kind"] == "paper"}
        assert got == brute_force(lib, query), query
        checked += bool(got)
    assert checked > 150                                                          # most queries find something: the test is not vacuous


def test_search_folds_accents_superscripts_and_case(lib):
    a = lib.ingest(rec("Dosimetry of ¹⁹²Ir sources", ["José Müller"], ids={"doi": "10.1/a"}, abstract="Straße and Ångström units.")).paper_id
    for q in ("192ir", "jose muller", "JOSÉ MÜLLER", "angstrom", "dosimetr", "muller dosimetry"):
        assert [r["ref"] for r in lib.search(q)] == [a], q
    assert lib.search("neutron") == []


def test_search_covers_notes_and_tags_and_follows_changes(lib):
    a = lib.ingest(rec(ids={"doi": "10.1/a"})).paper_id
    assert lib.search("magnetron") == []
    lib.add_note("The magnetron model is wrong here", pid=a)
    lib.tag(a, add=["phantom-study"])
    assert [r["ref"] for r in lib.search("magnetron")] == [a] and [r["ref"] for r in lib.search("phantom")] == [a]
    lib.tag(a, remove=["phantom-study"])
    assert lib.search("phantom") == []


def test_title_matches_rank_above_abstract_only_matches(lib):
    only_abstract = lib.ingest(rec("Something about measurements in a clinic setting", ids={"doi": "10.1/a"}, abstract="We study cherenkov light in depth.", sid="1")).paper_id
    in_title = lib.ingest(rec("Cherenkov imaging in a clinic setting", ids={"doi": "10.1/b"}, sid="2")).paper_id
    assert [r["ref"] for r in lib.search("cherenkov")] == [in_title, only_abstract]


@pytest.mark.parametrize("hostile", ['"', "(", ")", "*", "AND", "NOT", "OR", "NEAR(", "a OR", "'; DROP TABLE papers;--", "title:dose", "-dose", "^", "\\", "dose*", "   ", "!!!", "\x00dose"])
def test_search_never_breaks_on_hostile_input(lib, hostile):
    lib.ingest(rec(ids={"doi": "10.1/a"}))
    assert isinstance(lib.search(hostile), list)
    assert lib.db.execute("SELECT COUNT(*) FROM papers").fetchone()[0] == 1


def test_reindex_after_merge_leaves_no_ghost_entries(lib):
    a = lib.ingest(rec("Alpha beta gamma delta epsilon words", ids={"doi": "10.1/a"}, sid="1")).paper_id
    b = lib.ingest(rec("Zeta eta theta iota kappa lambda", ids={"doi": "10.1/b"}, sid="2")).paper_id
    lib.ingest(Paper("Whatever", [], 2026, ids={"doi": "10.1/a"}, source="x", source_id="3"))
    lib.merge(a, b)
    assert lib.search("zeta")[0]["ref"] == a and lib.get(a)["alt_titles"] == ["Whatever", "Zeta eta theta iota kappa lambda"]       # found under the title it had in the other source
    assert lib.db.execute("SELECT COUNT(*) FROM search WHERE kind = 'paper'").fetchone()[0] == 1
    lib.reindex_all()
    assert lib.db.execute("SELECT COUNT(*) FROM search").fetchone()[0] == 1


# --- watches and settings ----------------------------------------------------------------------------------------------------------------------
def test_watch_crud(lib):
    w = lib.add_watch("brachy", "brachytherapy dose", ["arxiv", "pubmed"], category="physics.med-ph", days=14, max_results=20)
    assert lib.watches() == [{"id": w, "name": "brachy", "query": "brachytherapy dose", "sources": ["arxiv", "pubmed"], "category": "physics.med-ph", "days": 14, "max_results": 20,
                              "created_at": "2026-10-15T12:00:00Z", "last_run": None}]
    for name, query in (("brachy", "x"), ("", "x"), ("n", "")):
        with pytest.raises(ValueError):
            lib.add_watch(name, query, ["arxiv"])
    assert lib.add_watch("cat only", "", ["arxiv"], category="physics.med-ph")
    lib.mark_ok(w, "arxiv", "2026-10-14T10:00:00Z")
    assert lib.last_ok(w, "arxiv") == "2026-10-14T10:00:00Z" and lib.last_ok(w, "pubmed") is None
    lib.remove_watch(w)
    assert [x["name"] for x in lib.watches()] == ["cat only"] and lib.last_ok(w, "arxiv") is None
    with pytest.raises(ValueError):
        lib.remove_watch(w)


def test_settings(lib):
    assert lib.setting("email") is None and lib.setting("email", "x") == "x"
    lib.set_setting("email", "a@b.org")
    lib.set_setting("email", "c@d.org")
    assert lib.setting("email") == "c@d.org"


def test_the_library_is_one_file_that_survives_reopening(tmp_path, parsed):
    path = str(tmp_path / "lib.db")
    a = libmod.Library(path)
    for p in list(parsed.values())[:15]:
        a.ingest(p)
    first = a.papers()[0]["id"]
    a.add_note("kept", pid=first)
    n = len(a.papers())
    a.close()
    b = libmod.Library(path)
    assert len(b.papers()) == n and b.notes(first)[0]["body"] == "kept" and b.search("kept")[0]["ref"] == first


def test_alternate_titles_are_kept_when_substance_differs_and_not_for_formatting(lib):
    a = lib.ingest(rec("Dosimetry of <sup>192</sup>Ir sources: a Monte Carlo study", ids={"doi": "10.1/a"}, sid="1")).paper_id
    lib.ingest(rec("Dosimetry of ¹⁹²Ir Sources: A Monte Carlo Study.", ids={"doi": "10.1/a"}, source="openalex", sid="W1"))        # formatting only
    assert lib.get(a)["alt_titles"] == []
    lib.ingest(rec("Brachytherapy source dose rates by simulation", ids={"doi": "10.1/a"}, source="arxiv", sid="2"))               # a real second title
    lib.ingest(rec("Brachytherapy Source Dose Rates by Simulation", ids={"doi": "10.1/a"}, source="arxiv", sid="3"))                # the same, differently capitalised
    assert lib.get(a)["alt_titles"] == ["Brachytherapy source dose rates by simulation"]
    assert [r["ref"] for r in lib.search("simulation")] == [a]
