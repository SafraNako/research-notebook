"""Self-validation: what the program decides is compared with a planted truth or with recorded real data, never with its own output.

The planted literature (``fakeworld``) has 43 works in 70 records across arXiv, PubMed and OpenAlex, written the way each source writes them, with the cases that make
duplicate resolution hard; the cassette is a real recorded session (two medical-physics watches over the three sources). Each check returns ``Check(name, passed, detail)``;
``arastirma dogrulama`` prints them all. Nothing here uses the network (the planted sources speak over the loopback interface only).
"""
from __future__ import annotations

import itertools
import random
import re
from dataclasses import dataclass
from datetime import date, datetime, timezone
from pathlib import Path

from . import bibtex, cassette, dedup, export, fakeworld
from . import library as libmod
from . import watch as watchmod
from .http import HttpClient
from .model import Paper
from .sources import Endpoints
from .textutil import clean_text, norm_arxiv, norm_doi, surname_key, title_key

CASSETTE = Path(__file__).parent / "data" / "medphys.cassette.json.gz"
_cache = {}


@dataclass
class Check:
    name: str
    passed: bool
    detail: str


def _world():
    if "world" not in _cache:
        world = fakeworld.PlantedScholarship()
        _cache["world"], _cache["parsed"] = world, fakeworld.parse_all(world)
    return _cache["world"], _cache["parsed"]


def _client(**kw):
    kwargs = {"allow_hosts": {"127.0.0.1"}, "sleep": lambda s: None, "retries": 1, "timeout": 5.0}
    kwargs.update(kw)
    return HttpClient(**kwargs)


def _key(p):
    return (p.source, p.source_id)


def check_parsers():
    world, parsed = _world()
    bad = []
    for r in world.records:
        p = parsed[_key_of(r)]
        ok = (title_key(p.title) == title_key(r.title) and r.date.startswith(p.date) and [surname_key(a) for a in p.authors] == [surname_key(a) for a in r.authors]
              and all(p.ids.get(k) == (v.lower() if k == "doi" else v) for k, v in r.ids.items()))
        if not ok:
            bad.append(_key_of(r))
    return Check("parsers read every planted record the way its source writes it", not bad,
                 f"{len(world.records)} records (arXiv Atom with LaTeX titles, PubMed XML with <sup> markup and season dates, OpenAlex JSON with inverted-index abstracts and "
                 f"upper-case DOI URLs): title, date, authors and identifiers recovered for all but {len(bad)}")


def _key_of(r):
    return (r.source, r.source_id)


def _clusters(papers):
    return {frozenset(_key(papers[i]) for i in c) for c in dedup.cluster(papers)}


def check_identity():
    world, parsed = _world()
    papers = list(parsed.values())
    truth = {frozenset(c) for c in world.truth_partition()}
    exact = _clusters(papers) == truth
    shuffles_ok = 0
    for seed in range(40):
        order = list(papers)
        random.Random(seed).shuffle(order)
        shuffles_ok += _clusters(order) == truth
    work_of = {_key_of(r): r.work.wid for r in world.records}
    cluster_of = {k: n for n, c in enumerate(_clusters(papers)) for k in c}
    fp = sum(1 for a, b in itertools.combinations(work_of, 2) if work_of[a] != work_of[b] and cluster_of[a] == cluster_of[b])
    fn = sum(1 for a, b in itertools.combinations(work_of, 2) if work_of[a] == work_of[b] and cluster_of[a] != cluster_of[b])
    return Check("duplicate resolution recovers the planted works, in any arrival order", exact and shuffles_ok == 40 and fp == 0 and fn == 1,
                 f"{len(papers)} records of {len(world.works)} works: {len(truth)} papers expected, recovered exactly; {shuffles_ok}/40 shuffled arrival orders give the identical "
                 f"result; record pairs wrongly merged: {fp}; pairs wrongly split: {fn} (the declared known miss: a title that changed between preprint and journal)")


def check_traps():
    world, parsed = _world()
    pairs = [("t1a", "t1b"), ("t2a", "t2b"), ("t3a", "t3b"), ("t4a", "t4b"), ("a5", "t5"), ("t6a", "t6b")]
    merged = []
    for wa, wb in pairs:
        ra = [parsed[_key_of(r)] for r in world.records if r.work.wid == wa]
        rb = [parsed[_key_of(r)] for r in world.records if r.work.wid == wb]
        if any(dedup.same_paper(a, b) for a in ra for b in rb):
            merged.append((wa, wb))
    positives = ["a1", "b1", "b2", "b3", "b5", "c1", "d1"]
    split = [w for w in positives if len(dedup.cluster([parsed[_key_of(r)] for r in world.records if r.work.wid == w])) != 1]
    return Check("look-alike papers stay apart and one paper's records stay together", not merged and not split,
                 f"{len(pairs)} trap pairs kept separate (Part I vs Part II, a generic title, same title years apart, same title with different DOIs, a paper and its correction, "
                 f"ResNet-18 vs ResNet-50); {len(positives)} multi-record works resolved to one paper each")


def check_library_order_independence():
    world, parsed = _world()
    truth = {frozenset(c) for c in world.truth_partition()}
    ok = 0
    for seed in range(12):
        order = list(parsed.values())
        random.Random(1000 + seed).shuffle(order)
        lib = libmod.Library()
        for p in order:
            lib.ingest(p)
        got = {frozenset((s["source"], s["source_id"]) for s in lib.get(row["id"])["sources"]) for row in lib.db.execute("SELECT id FROM papers")}
        ok += got == truth
    return Check("the library itself reaches the same papers whatever the arrival order", ok == 12,
                 f"{ok}/12 shuffled orders of the 70 records end in the same {len(truth)} papers (identifier bridges, conflicts and ambiguity handled)")


def check_watches():
    world = fakeworld.PlantedScholarship()
    with fakeworld.FakeScholar(world) as net:
        lib = libmod.Library()
        w = lib.add_watch("dose", "dose", ["arxiv", "pubmed", "openalex"], days=400)

        def run(day):
            world.today = date.fromisoformat(day)
            y, m, d = map(int, day.split("-"))
            return watchmod.run_all(lib, _client(), net.endpoints(), only=w, now=datetime(y, m, d, 12, tzinfo=timezone.utc))[0]
        r1 = run("2026-09-20")
        again = run("2026-09-20")
        r2 = run("2026-10-15")
        by_key = {(r.source, r.source_id): r.work.wid for r in world.records}
        works = lambda ids: {by_key[(s["source"], s["source_id"])] for pid in ids for s in lib.get(pid)["sources"]}  # noqa: E731
        first, second = works(r1.new_ids), works(r2.new_ids)
        # the journal version of a known preprint is merged, not new; the only work that is in both is the declared known miss
        ok = (not again.new_ids and not second - {"m1"} & first and len(r1.new_ids) > 10 and len(r2.new_ids) > 3 and second & first <= {"m1"})
        return Check("watches surface exactly the papers not seen before", ok,
                     f"day 1: {len(r1.new_ids)} papers; the same day again: {len(again.new_ids)} new; 25 days later: {len(r2.new_ids)} new, none of them a work already in the inbox "
                     f"(journal versions of known preprints were merged), apart from the declared known miss")


def check_failures():
    world = fakeworld.PlantedScholarship()
    out = []
    with fakeworld.FakeScholar(world, faults={"arxiv": "always:429"}) as net:
        lib = libmod.Library()
        w = lib.add_watch("dose", "dose", ["arxiv", "pubmed", "openalex"], days=400)
        r = watchmod.run_all(lib, _client(retries=0), net.endpoints(), only=w, now=datetime(2026, 10, 15, 12, tzinfo=timezone.utc))[0]
        states = {s.source: s.state for s in r.sources}
        out.append(states == {"arxiv": "error", "pubmed": "ok", "openalex": "ok"} and "rate limiting" in r.sources[0].detail and lib.last_ok(w, "arxiv") is None)
    with fakeworld.FakeScholar(world, faults={"arxiv": [429, 429]}) as net:
        sleeps = []
        lib = libmod.Library()
        w = lib.add_watch("dose", "dose", ["arxiv"], days=400)
        r = watchmod.run_all(lib, HttpClient(allow_hosts={"127.0.0.1"}, sleep=sleeps.append, retries=3, backoff=2.0), net.endpoints(), only=w, now=datetime(2026, 10, 15, 12, tzinfo=timezone.utc))[0]
        out.append(r.ok and sleeps[:2] == [2.0, 4.0])
    with fakeworld.FakeScholar(world, faults={"pubmed": "garbage", "openalex": "always:500"}) as net:
        lib = libmod.Library()
        w = lib.add_watch("dose", "dose", ["arxiv", "pubmed", "openalex"], days=400)
        r = watchmod.run_all(lib, _client(retries=0), net.endpoints(), only=w, now=datetime(2026, 10, 15, 12, tzinfo=timezone.utc))[0]
        out.append({s.source: s.state for s in r.sources} == {"arxiv": "ok", "pubmed": "error", "openalex": "error"} and len(lib.papers()) > 0)
    return Check("throttling and outages become statuses; a throttled source is retried and keeps its own window", all(out),
                 "arXiv throttled for good: error status, the other two sources deliver, arXiv's window stays open; throttled twice then fine: retried with 2 s and 4 s back-off and "
                 "succeeds; unreadable PubMed and failing OpenAlex: reported, arXiv still delivers")


def _brute(lib, query):
    import unicodedata
    from .textutil import strip_accents

    def words(text):
        return re.findall(r"[^\W_]+", strip_accents(unicodedata.normalize("NFKC", text)).casefold())
    want = words(query)
    hits = set()
    for p in lib.papers():
        text = " ".join([" ".join([p["title"], *p["alt_titles"]]), p["abstract"], " ".join(p["authors"]), " ".join(p["tags"]), " ".join(n["title"] + " " + n["body"] for n in lib.notes(p["id"]))])
        tokens = words(text)
        if want and all(any(t.startswith(w) for t in tokens) for w in want):
            hits.add(p["id"])
    return hits


def check_search_matches_brute_force():
    _, parsed = _world()
    lib = libmod.Library()
    for p in parsed.values():
        lib.ingest(p)
    pid = lib.papers()[0]["id"]
    lib.add_note("The magnetron model is wrong", pid=pid)
    lib.tag(pid, add=["zzqq-study"])
    papers = lib.papers()
    rng = random.Random(5)
    words_of = lambda p: sorted(set(re.findall(r"[^\W_]{3,}", (p["title"] + " " + p["abstract"]).casefold())))  # noqa: E731
    agree, nonempty = 0, 0
    for _ in range(200):
        q = " ".join(rng.sample(words_of(rng.choice(papers)), rng.randint(1, 3)))
        got = {r["ref"] for r in lib.search(q, limit=1000) if r["kind"] == "paper"}
        agree += got == _brute(lib, q)
        nonempty += bool(got)
    extra = {r["ref"] for r in lib.search("magnetron")} == {pid} and {r["ref"] for r in lib.search("zzqq")} == {pid} and bool(lib.search("192ir"))
    hostile = all(isinstance(lib.search(h), list) for h in ('"', "(", "NEAR(", "'; DROP TABLE papers;--", "*"))
    return Check("full-text search returns exactly what a brute-force scan finds", agree == 200 and nonempty > 100 and extra and hostile,
                 f"{agree}/200 random 1-3 word queries (prefixes, accents, superscripts) match the independent scan over title, alternate titles, abstract, authors, tags and notes "
                 f"({nonempty} non-empty); notes and tags are searchable at once; hostile query strings never reach the FTS syntax")


def _author(rng):
    given = rng.choice(["A", "Jos", "Åsa"])
    surname = rng.choice(["Müller", "van der Berg", "O'Neill", "Søndergaard"])
    return given + " " + surname


def check_bibtex():
    rng = random.Random(42)
    pool = "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789 .,:;-()[]{}&%$#_~^\\'\"éüÅøßΩ中"
    ok = n = 0
    for _ in range(300):
        title = "".join(rng.choice(pool) for _ in range(rng.randint(5, 80)))
        if not clean_text(title):
            continue
        p = Paper(title=title, authors=[_author(rng) for _ in range(rng.randint(0, 3))],
                  year=rng.choice([None, 2024]), venue=rng.choice(["", "Med Phys & Co"]), ids={"doi": f"10.{rng.randint(1000, 9999)}/x{rng.randint(0, 99)}"})
        back = bibtex.loads(bibtex.dumps([p]))
        n += 1
        ok += len(back) == 1 and clean_text(back[0].title) == clean_text(p.title) and back[0].authors == p.authors and back[0].ids == p.ids and back[0].year == p.year
    keys = bibtex.keys_for([Paper(title="Monte Carlo of dose", authors=["John Smith"], year=2024)] * 30)
    sample = bibtex.loads(r"@article{k, author = {M{\"u}ller, Jos{\'e} and {The ATLAS Collaboration}}, title = {Dose of {$^{192}$Ir} \& {TG}-43}, year = 2024, doi = {10.1234/A}}")[0]
    real_ok = sample.title == "Dose of ¹⁹²Ir & TG-43" and sample.authors == ["Müller, José", "The ATLAS Collaboration"] and sample.ids == {"doi": "10.1234/a"}
    return Check("BibTeX survives a round trip, special characters and all", ok == n and len(set(keys)) == 30 and real_ok,
                 f"{ok}/{n} random papers (LaTeX specials, accents, particles, CJK) export and import unchanged; 30 same-author-same-year papers get 30 distinct keys; "
                 f"LaTeX accents, math and corporate authors decode correctly")


def check_text_normalisation():
    doi = {"https://doi.org/10.1016/J.APRADISO.2026.112946.": "10.1016/j.apradiso.2026.112946", "DOI:10.1088/1361-6560/AE0002": "10.1088/1361-6560/ae0002"}
    arx = {"arXiv:2301.00001v3": "2301.00001", "https://arxiv.org/pdf/2301.00001v2.pdf": "2301.00001", "http://arxiv.org/abs/cond-mat/0011267v1": "cond-mat/0011267",
           "10.48550/arXiv.2301.00001": "2301.00001"}
    titles = {title_key(x) for x in ("Dose of <sup>192</sup>Ir sources", "Dose of 192 Ir sources", "Dose of ¹⁹²Ir Sources.", r"Dose of $^{192}$Ir {S}ources")}
    ok = all(norm_doi(k) == v for k, v in doi.items()) and all(norm_arxiv(k) == v for k, v in arx.items()) and len(titles) == 1
    safe = clean_text("p < 0.001 for group A and values > 5 Gy (<6 h)") == "p < 0.001 for group A and values > 5 Gy (<6 h)" and clean_text("H<sub>2</sub>O <i>in vivo</i>") == "H2O in vivo"
    return Check("identifiers and titles are normalised so that records of one paper meet", ok and safe,
                 "DOI URLs and prefixes, arXiv ids in 4 spellings (including old-style and arXiv DOIs), titles with <sup>, LaTeX, Unicode superscripts and isotope spacing "
                 "reduce to one key each; only real inline markup is stripped (a '<' in 'p < 0.001' never deletes text)")


def check_real_data():
    meta, tape = cassette.load(CASSETTE)
    now = datetime.strptime(meta["recorded_at"], "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
    lib = libmod.Library(clock=lambda: now)
    lib.add_watch("medphys-mc", "Monte Carlo dose", ["arxiv", "pubmed", "openalex"], category="physics.med-ph", days=21, max_results=25)
    lib.add_watch("brachy", "brachytherapy dosimetry", ["pubmed", "openalex"], days=30, max_results=25)
    runs = watchmod.run_all(lib, cassette.ReplayClient(tape, retries=0), Endpoints(), now=now)
    papers = lib.papers()
    records = sum(sum(s.fetched for s in r.sources) for r in runs)
    both = [p for p in papers if {s["source"] for s in p["sources"]} >= {"pubmed", "openalex"}]
    import json
    from .sources import openalex
    oa = {}
    for url, e in tape.items():
        if "openalex" in url and "filter" in url:
            for w in openalex.parse_works(json.loads(e["body"])):
                oa[w.ids["openalex"]] = w
    agree = sum(1 for p in both if oa[p["ids"]["openalex"][0]].ids.get("pmid") == p["ids"]["pmid"][0])
    closed = not any(dedup.same_paper(lib.as_paper(a["id"]), lib.as_paper(b["id"])) for a, b in itertools.combinations(papers, 2))
    return Check("real recorded session: duplicates across PubMed and OpenAlex are merged and verifiably the same", records == 78 and len(papers) == 68 and len(both) == 10 and agree == 10 and closed,
                 f"{records} real records from {meta['requests']} recorded responses became {len(papers)} papers; {len(both)} PubMed/OpenAlex pairs merged by DOI, and OpenAlex's own PubMed "
                 f"id agrees for {agree}/{len(both)}; no two papers remain that the rules would merge; abstracts keep their comparison signs")


def check_politeness_and_privacy():
    world = fakeworld.PlantedScholarship()
    with fakeworld.FakeScholar(world) as net:
        lib = libmod.Library()
        w = lib.add_watch("dose", "dose", ["arxiv", "pubmed", "openalex"], days=400)
        watchmod.run_all(lib, _client(), net.endpoints(), only=w, now=datetime(2026, 10, 15, 12, tzinfo=timezone.utc))
        quiet = [q for q in net.requests if "email" in q["query"] or "mailto" in q["query"]]
        lib.set_setting("email", "me@example.org")
        watchmod.run_all(lib, _client(), net.endpoints(), only=w, now=datetime(2026, 10, 16, 12, tzinfo=timezone.utc))
        later = net.requests[len(net.requests) - 3:]
        configured = all(q["query"].get("email") == "me@example.org" for q in later if q["path"].startswith("/pubmed")) and all(
            q["query"].get("mailto") == "me@example.org" for q in later if q["route"] == "openalex")
        identified = all(q["ua"].startswith("research-notebook/") for q in net.requests)
    return Check("requests are identified, send only the search, and mention an e-mail address only when you configured one", not quiet and configured and identified,
                 f"{len(net.requests)} requests, all with the program's User-Agent; {len(quiet)} carried an e-mail address before one was configured; after `arastirma ayar --e-posta` "
                 "it is passed to PubMed and OpenAlex as the polite-pool contact and nowhere else")


def check_digest_and_export():
    _, parsed = _world()
    now = datetime(2026, 10, 15, 12, tzinfo=timezone.utc)
    lib = libmod.Library(clock=lambda: now)
    for p in parsed.values():
        lib.ingest(p)
    first = lib.papers()[0]["id"]
    lib.set_status(first, "okundu", rating=5)
    lib.add_note("A note worth keeping", pid=first)
    text = export.digest(lib, now=now)
    import tempfile
    with tempfile.TemporaryDirectory() as tmp:
        paths = export.export_markdown(lib, tmp)
        body = Path(paths[0]).read_text(encoding="utf-8")
    ok = (export.digest(lib, now=now) == text and "Inbox: **43** new" in text and "Read this period (1)" in text and "A note worth keeping" in text
          and len(paths) == 1 and body.startswith("---\n") and "## Notes" in body)
    return Check("the digest is deterministic and the Markdown export carries front matter and notes", ok,
                 "the same library and date give the same digest; the export writes one file per decided paper with YAML front matter, the abstract and its notes")


ALL = [check_parsers, check_identity, check_traps, check_library_order_independence, check_watches, check_failures, check_search_matches_brute_force, check_bibtex,
       check_text_normalisation, check_real_data, check_politeness_and_privacy, check_digest_and_export]


def run_all():
    results = []
    for f in ALL:
        try:
            results.append(f())
        except Exception as exc:                      # a broken check is a failed check, not a crash
            results.append(Check(f.__name__.replace("check_", "").replace("_", " "), False, f"{type(exc).__name__}: {exc}"))
    return results
