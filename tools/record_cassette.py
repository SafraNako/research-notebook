"""Record a live session as a cassette for the real-data tests:  python tools/record_cassette.py src/arastirma_defteri/data/medphys.cassette.json.gz

Runs two watches (medical-physics topics over arXiv, PubMed and OpenAlex) and adds three papers by identifier. Uses the network (about 15 requests, a minute or two: arXiv is
polite-throttled and may answer 429 for a while, which the client retries). The result is public bibliographic metadata."""
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from arastirma_defteri import cassette, watch  # noqa: E402
from arastirma_defteri.library import Library  # noqa: E402
from arastirma_defteri.sources import Endpoints  # noqa: E402


def main(out):
    client = cassette.RecordingClient(min_interval=watch.HOST_INTERVALS, retries=5, backoff=8.0)
    now = datetime.now(timezone.utc)
    lib = Library(":memory:", clock=lambda: now)
    ep = Endpoints()
    lib.add_watch("medphys-mc", "Monte Carlo dose", ["arxiv", "pubmed", "openalex"], category="physics.med-ph", days=21, max_results=25)
    lib.add_watch("brachy", "brachytherapy dosimetry", ["pubmed", "openalex"], days=30, max_results=25)
    for run in watch.run_all(lib, client, ep, now=now):
        print(run.watch["name"], [(s.source, s.state, s.fetched, s.detail) for s in run.sources])
    ids = {}
    for p in lib.papers():
        for s in p["sources"]:
            ids.setdefault(s["source"], p)
    extra = {}
    if "arxiv" in ids:
        arx = next(s for s in ids["arxiv"]["sources"] if s["source"] == "arxiv")["source_id"]
        extra["arxiv"] = arx
        watch.fetch_one(client, ep, "arxiv", arx)
    if "pubmed" in ids:
        pm = next(s for s in ids["pubmed"]["sources"] if s["source"] == "pubmed")["source_id"]
        extra["pmid"] = pm
        watch.fetch_one(client, ep, "pmid", pm)
    if "openalex" in ids and ids["openalex"]["ids"].get("doi"):
        extra["doi"] = ids["openalex"]["ids"]["doi"][0]
        watch.fetch_one(client, ep, "doi", extra["doi"])
    cassette.save(out, client.tape, {"recorded_at": now.strftime("%Y-%m-%dT%H:%M:%SZ"), "requests": len(client.tape), "extra": extra})
    print(f"{len(client.tape)} responses recorded to {out}")


if __name__ == "__main__":
    main(sys.argv[1])
