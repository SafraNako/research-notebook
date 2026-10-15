"""Watches over time: what is new, what is not, and what happens when sources fail, against a world that evolves day by day."""
from datetime import date, datetime, timezone

import pytest

from conftest import make_client
from arastirma_defteri import fakeworld
from arastirma_defteri import library as libmod
from arastirma_defteri import watch as watchmod
from arastirma_defteri.http import HttpClient


def at(day):
    y, m, d = map(int, day.split("-"))
    return datetime(y, m, d, 12, 0, tzinfo=timezone.utc)


@pytest.fixture
def setup():
    world = fakeworld.PlantedScholarship()
    net = fakeworld.FakeScholar(world, delay=0.4)
    net.__enter__()
    lib = libmod.Library()
    yield world, net, lib
    net.__exit__(None, None, None)


def run(lib, net, world, day, wid=None, client=None, keys=None):
    world.today = date.fromisoformat(day)
    return watchmod.run_all(lib, client or make_client(), net.endpoints(), only=wid, now=at(day), keys=keys)


def work_ids(lib, ids):
    """The planted works behind the library papers with these ids."""
    out = set()
    for pid in ids:
        for s in lib.get(pid)["sources"]:
            out.add((s["source"], s["source_id"]))
    return out


def inbox_works(world, lib):
    by_key = {(r.source, r.source_id): r.work.wid for r in world.records}
    return {pid: {by_key[(s["source"], s["source_id"])] for s in lib.get(pid)["sources"]} for pid in [p["id"] for p in lib.papers(status="yeni")]}


def test_first_run_collects_exactly_the_window_and_merges_across_sources(setup):
    world, net, lib = setup
    w = lib.add_watch("dose", "dose", ["arxiv", "pubmed", "openalex"], days=60)
    (r,) = run(lib, net, world, "2026-10-15", w)
    assert r.ok and [s.state for s in r.sources] == ["ok", "ok", "ok"]
    # the records the three sources could legitimately return: visible today, dated within the 60-day window, mentioning the query word
    expected_records = [rec for rec in world.visible() if rec.date >= "2026-08-16" and fakeworld._matches(rec, ["dose"])]
    assert sum(s.fetched for s in r.sources) == len(expected_records) > 30
    got = {frozenset((s["source"], s["source_id"]) for s in lib.get(p["id"])["sources"]) for p in lib.papers(status="yeni")}
    assert got == {frozenset(c) for c in world.truth_partition(expected_records)}              # the inbox is exactly the papers behind those records
    assert len(r.new_ids) == len(got) and r.merged == len(expected_records) - len(got)         # every record that was not a new paper was merged into one


def test_a_second_run_with_nothing_new_finds_nothing_new(setup):
    world, net, lib = setup
    w = lib.add_watch("dose", "dose", ["arxiv", "pubmed", "openalex"], days=60)
    run(lib, net, world, "2026-10-15", w)
    before = lib.count()
    (r,) = run(lib, net, world, "2026-10-15", w)
    assert r.new_ids == [] and lib.count() == before


def test_forgetting_the_window_and_fetching_everything_again_adds_nothing(setup):
    """Even with the run history lost, every record fetched a second time is recognised: no duplicate papers, no new ones."""
    world, net, lib = setup
    w = lib.add_watch("dose", "dose", ["arxiv", "pubmed", "openalex"], days=60)
    (first,) = run(lib, net, world, "2026-10-15", w)
    papers_before = len(lib.papers())
    lib.db.execute("DELETE FROM watch_state")
    lib.db.commit()
    (again,) = run(lib, net, world, "2026-10-15", w)
    fetched = sum(s.fetched for s in again.sources)
    assert fetched == sum(s.fetched for s in first.sources) > 30
    assert again.new_ids == [] and again.merged == fetched and len(lib.papers()) == papers_before


def test_only_papers_that_appear_later_are_new_on_the_next_run(setup):
    world, net, lib = setup
    w = lib.add_watch("dose", "dose", ["arxiv", "pubmed", "openalex"], days=400)
    first_day = "2026-09-20"
    (r1,) = run(lib, net, world, first_day, w)
    seen = {k for pid in [p["id"] for p in lib.papers()] for k in work_ids(lib, [pid])}
    hidden_then = {(r.source, r.source_id) for r in world.records if r.appears > first_day}
    assert hidden_then and not (seen & hidden_then)                                      # records that did not exist yet were not returned
    (r2,) = run(lib, net, world, "2026-10-15", w)
    now_seen = {k for pid in [p["id"] for p in lib.papers()] for k in work_ids(lib, [pid])}
    assert hidden_then & {(r.source, r.source_id) for r in world.records if "dose" in (r.title + r.work.abstract).lower()} <= now_seen
    new_works = {wid for pid in r2.new_ids for wid in inbox_works(world, lib).get(pid, set())}
    first_works = {wid for pid in r1.new_ids for wid in inbox_works(world, lib).get(pid, set())}
    assert new_works and new_works & first_works == {"m1"}                                # the new papers are works not in the first inbox, except the declared known miss:
    #                                                                                      # its preprint came first, and its journal version (renamed) is, by design, a separate paper


def test_a_journal_version_of_a_known_preprint_is_not_new(setup):
    world, net, lib = setup
    w = lib.add_watch("gpu", "Monte Carlo dose calculation GPU proton", ["arxiv", "pubmed"], days=400)
    run(lib, net, world, "2026-09-01", w)                                                # only the preprint (b1, 2026-08-20) exists
    assert len(lib.papers()) == 1 and lib.papers()[0]["sources"][0]["source"] == "arxiv"
    (r,) = run(lib, net, world, "2026-10-15", w)                                         # the journal version appears (2026-10-05)
    assert r.new_ids == [] and len(lib.papers()) == 1
    assert {s["source"] for s in lib.papers()[0]["sources"]} == {"arxiv", "pubmed"} and "pmid" in lib.papers()[0]["ids"]


def test_a_dismissed_paper_and_a_paper_in_the_library_never_return_to_the_inbox(setup):
    world, net, lib = setup
    w = lib.add_watch("dose", "dose", ["pubmed"], days=400)
    run(lib, net, world, "2026-10-15", w)
    ids = [p["id"] for p in lib.papers(status="yeni")]
    lib.set_status(ids[0], "atildi")
    lib.set_status(ids[1], "okunacak")
    (r,) = run(lib, net, world, "2026-10-15", w)
    assert r.new_ids == [] and {p["id"] for p in lib.papers(status="yeni")} == set(ids[2:])
    assert lib.get(ids[0])["status"] == "atildi" and lib.get(ids[1])["status"] == "okunacak"


def test_per_source_windows_a_failed_source_is_asked_again_from_where_it_last_succeeded(setup):
    world, net, lib = setup
    w = lib.add_watch("dose", "dose", ["arxiv", "openalex"], days=400)
    run(lib, net, world, "2026-09-10", w)
    last_arxiv, last_oa = lib.last_ok(w, "arxiv"), lib.last_ok(w, "openalex")
    net.faults.spec["arxiv"] = "always:429"
    (r,) = run(lib, net, world, "2026-10-01", w)
    states = {s.source: s.state for s in r.sources}
    assert states == {"arxiv": "error", "openalex": "ok"} and not r.ok
    assert "rate limiting" in next(s for s in r.sources if s.source == "arxiv").detail
    assert lib.last_ok(w, "arxiv") == last_arxiv and lib.last_ok(w, "openalex") != last_oa            # arXiv's window stayed open, OpenAlex's moved on
    net.faults.spec.pop("arxiv")
    (r2,) = run(lib, net, world, "2026-10-15", w)
    assert [s.state for s in r2.sources] == ["ok", "ok"]
    arxiv_requests = [q for q in net.requests if q["route"] == "arxiv"]
    assert "submittedDate:[20260908" in arxiv_requests[-1]["query"]["search_query"]                       # since = the last success (09-10) minus the two-day overlap


def test_every_failure_mode_is_a_status_and_the_other_sources_still_deliver(setup):
    world, net, lib = setup
    w = lib.add_watch("dose", "dose", ["arxiv", "pubmed", "openalex"], days=400)
    for fault, expect in (("always:500", "HTTP 500"), ("always:429", "rate limiting"), ("garbage", "not Atom XML")):
        net.faults.spec = {"arxiv": fault}
        (r,) = run(lib, net, world, "2026-10-15", w, client=make_client(retries=0))
        arx = next(s for s in r.sources if s.source == "arxiv")
        assert arx.state == "error" and expect in arx.detail, fault
        assert all(s.state == "ok" for s in r.sources if s.source != "arxiv")


def test_a_throttled_source_is_retried_with_backoff_and_succeeds(setup):
    world, net, lib = setup
    net.faults.spec = {"arxiv": [429, 429]}
    w = lib.add_watch("dose", "dose", ["arxiv"], days=400)
    sleeps = []
    client = HttpClient(allow_hosts={"127.0.0.1"}, sleep=sleeps.append, retries=3, backoff=2.0)
    (r,) = run(lib, net, world, "2026-10-15", w, client=client)
    assert r.ok and r.sources[0].fetched > 0 and sleeps[:2] == [2.0, 4.0]


def test_a_timeout_is_a_status(setup):
    world, net, lib = setup
    net.faults.spec = {"openalex": "slow"}
    w = lib.add_watch("dose", "dose", ["openalex", "pubmed"], days=400)
    (r,) = run(lib, net, world, "2026-10-15", w, client=make_client(retries=0, timeout=0.15))
    assert {s.source: s.state for s in r.sources} == {"openalex": "error", "pubmed": "ok"}


def test_category_only_watches_work_for_arxiv_and_are_skipped_elsewhere(setup):
    world, net, lib = setup
    w = lib.add_watch("medph", "", ["arxiv", "pubmed"], category="physics.med-ph", days=400)
    (r,) = run(lib, net, world, "2026-10-15", w)
    states = {s.source: s.state for s in r.sources}
    assert states["arxiv"] == "ok" and states["pubmed"] == "error" and "needs a query" in r.sources[1].detail and len(lib.papers()) > 10
    assert {s["source"] for p in lib.papers() for s in p["sources"]} == {"arxiv"}


def test_runs_are_recorded_and_the_last_run_time_moves(setup):
    world, net, lib = setup
    w = lib.add_watch("dose", "dose", ["pubmed"], days=400)
    run(lib, net, world, "2026-10-15", w)
    runs = lib.runs(w)
    assert len(runs) == 1 and runs[0]["summary"]["sources"][0]["source"] == "pubmed" and lib.watches()[0]["last_run"] == "2026-10-15T12:00:00Z"


def test_requests_are_identified_and_target_the_right_routes(setup):
    world, net, lib = setup
    lib.set_setting("email", "me@example.org")
    w = lib.add_watch("dose", "dose", ["arxiv", "pubmed", "openalex"], days=400)
    run(lib, net, world, "2026-10-15", w)
    assert all(q["ua"].startswith("research-notebook/") for q in net.requests)
    assert {q["route"] for q in net.requests} == {"arxiv", "pubmed", "openalex"}
    assert all(q["query"].get("email") == "me@example.org" for q in net.requests if q["path"].startswith("/pubmed"))
    assert all(q["query"].get("mailto") == "me@example.org" for q in net.requests if q["route"] == "openalex")


def test_adding_a_paper_by_identifier(setup):
    world, net, lib = setup
    ep, c = net.endpoints(), make_client()
    arx = watchmod.fetch_one(c, ep, "arxiv", "2609.10105")
    pub = watchmod.fetch_one(c, ep, "pmid", "50000105")
    oa = watchmod.fetch_one(c, ep, "doi", "10.1002/mp.17105")
    assert arx.ids["arxiv"] == "2609.10105" and pub.ids["pmid"] == "50000105" and oa.ids["doi"] == "10.1002/mp.17105"
    for p in (arx, pub, oa):
        lib.ingest(p, status="okunacak")
    assert len(lib.papers()) == 1 and lib.papers()[0]["status"] == "okunacak"             # the same paper by three routes is one paper
    assert watchmod.fetch_one(c, ep, "doi", "10.9999/nothing") is None
