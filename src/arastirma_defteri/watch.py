"""Saved searches. A watch is a query over one or more sources; running it asks each source for the newest matching papers and folds them into the library, where
only the ones not seen before (under any identifier, from any source) arrive in the inbox as ``yeni``.

Each source keeps its own "last successful run": a run in which arXiv was throttled leaves arXiv's window open, so the next run asks it again from where it last succeeded,
while the sources that answered move on. Every run overlaps the previous one by two days (indexes lag), which is harmless because known papers are never new twice.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone

from .http import HttpError
from .model import Paper
from .sources import SourceError, arxiv, openalex, pubmed

SOURCE_NAMES = ["arxiv", "pubmed", "openalex"]
OVERLAP_DAYS = 2
HOST_INTERVALS = {"export.arxiv.org": arxiv.HOST_INTERVAL, "eutils.ncbi.nlm.nih.gov": pubmed.HOST_INTERVAL}


@dataclass
class SourceRun:
    source: str
    state: str                    # ok | error | skipped
    detail: str = ""
    fetched: int = 0
    new: int = 0


@dataclass
class WatchRun:
    watch: dict
    started_at: str
    sources: list = field(default_factory=list)
    new_ids: list = field(default_factory=list)       # library ids of the papers this run added to the inbox
    merged: int = 0                                    # records that turned out to be known papers (including via another source)

    @property
    def ok(self):
        return all(s.state != "error" for s in self.sources)


def _since(lib, watch, source, now):
    last = lib.last_ok(watch["id"], source)
    if last:
        return (datetime.strptime(last, "%Y-%m-%dT%H:%M:%SZ").date() - timedelta(days=OVERLAP_DAYS))
    return now.date() - timedelta(days=watch["days"])


def fetch(source, client, endpoints, watch, since, email=None, keys=None):
    keys = keys or {}
    n = watch["max_results"]
    if source == "arxiv":
        return arxiv.search(client, endpoints, watch["query"], category=watch["category"], since=since, max_results=n)
    if not watch["query"]:
        raise SourceError(f"{source} needs a query (this watch only has a category)")
    if source == "pubmed":
        return pubmed.search(client, endpoints, watch["query"], since=since, max_results=n, email=email, api_key=keys.get("pubmed"))
    if source == "openalex":
        return openalex.search(client, endpoints, watch["query"], since=since, max_results=n, email=email, api_key=keys.get("openalex"))
    raise SourceError(f"unknown source {source!r}")


def run_watch(lib, client, endpoints, watch, now=None, keys=None):
    now = now or datetime.now(timezone.utc)
    stamp = now.strftime("%Y-%m-%dT%H:%M:%SZ")
    run = WatchRun(watch=watch, started_at=stamp)
    email = lib.setting("email")
    for source in watch["sources"]:
        since = _since(lib, watch, source, now)
        try:
            papers = fetch(source, client, endpoints, watch, since, email=email, keys=keys)
        except (SourceError, HttpError) as exc:
            run.sources.append(SourceRun(source, "error", str(exc)))
            continue
        except Exception as exc:                                  # a parser surprised by a source must not stop the other sources
            run.sources.append(SourceRun(source, "error", f"{type(exc).__name__}: {exc}"))
            continue
        sr = SourceRun(source, "ok", f"since {since.isoformat()}", fetched=len(papers))
        for p in papers:
            result = lib.ingest(p, watch_id=watch["id"], status="yeni")
            if result.created:
                run.new_ids.append(result.paper_id)
                sr.new += 1
            else:
                run.merged += 1
            for merged_away in result.merged:                     # a bridge folded two inbox papers into one: the dropped id is no longer new
                if merged_away in run.new_ids:
                    run.new_ids.remove(merged_away)
        lib.mark_ok(watch["id"], source, stamp)
        run.sources.append(sr)
    run.new_ids = [i for i in dict.fromkeys(run.new_ids) if lib.get(i) is not None]
    lib.record_run(watch["id"], {"sources": [s.__dict__ for s in run.sources], "new": run.new_ids, "merged": run.merged}, stamp)
    return run


def run_all(lib, client, endpoints, only=None, now=None, keys=None):
    return [run_watch(lib, client, endpoints, w, now=now, keys=keys) for w in lib.watches() if only is None or w["id"] == only]


def fetch_one(client, endpoints, kind, value, email=None, keys=None):
    """A single paper by identifier, for adding it by hand: an arXiv id, a DOI (through OpenAlex) or a PubMed id."""
    keys = keys or {}
    if kind == "arxiv":
        return arxiv.fetch(client, endpoints, value)
    if kind == "doi":
        return openalex.fetch_by_doi(client, endpoints, value, email=email, api_key=keys.get("openalex"))
    if kind == "pmid":
        found = pubmed.fetch_ids(client, endpoints, [value], email=email, api_key=keys.get("pubmed"))
        return found[0] if found else None
    raise SourceError(f"cannot fetch by {kind}")


__all__ = ["SOURCE_NAMES", "SourceRun", "WatchRun", "run_watch", "run_all", "fetch_one", "Paper", "date"]
