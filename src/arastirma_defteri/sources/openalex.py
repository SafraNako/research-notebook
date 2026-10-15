"""OpenAlex, a broad open index of scholarly works. Abstracts come as an inverted index (word -> positions) and are rebuilt; the DOI may be an arXiv DOI, which
this program treats as the arXiv identifier it is. Without an API key requests share a small daily budget per IP address; ``OPENALEX_API_KEY`` raises it."""
from __future__ import annotations

import json

from ..model import Paper, year_of
from ..textutil import clean_text, ids_from
from . import SourceError, check

NAME = "openalex"
SELECT = "id,doi,title,publication_date,publication_year,ids,authorships,abstract_inverted_index,primary_location,type"


def abstract_from_index(index):
    """Rebuild the abstract text from OpenAlex's ``{word: [positions]}`` form."""
    if not index:
        return ""
    slots = {}
    for word, positions in index.items():
        for p in positions:
            slots[p] = word
    return " ".join(slots[i] for i in sorted(slots))


def parse_works(data):
    papers = []
    for w in data.get("results", []):
        title = clean_text(w.get("title") or w.get("display_name") or "")
        if not title:
            continue
        ids_obj = w.get("ids") or {}
        loc = w.get("primary_location") or {}
        source = loc.get("source") or {}
        date = w.get("publication_date") or None
        papers.append(Paper(
            title=title, authors=[(a.get("author") or {}).get("display_name") or a.get("raw_author_name") or "" for a in (w.get("authorships") or [])],
            year=w.get("publication_year") or year_of(date), date=date, venue=source.get("display_name") or "", abstract=clean_text(abstract_from_index(w.get("abstract_inverted_index"))),
            url=loc.get("landing_page_url") or w.get("doi") or w.get("id") or "",
            ids=ids_from(doi=w.get("doi"), pmid=ids_obj.get("pmid"), openalex=w.get("id")), source=NAME, source_id=(w.get("id") or "").rsplit("/", 1)[-1]))
    for p in papers:
        p.authors = [a for a in p.authors if a]
    return papers


def _params(extra, email, api_key):
    params = dict(extra)
    if email:
        params["mailto"] = email
    if api_key:
        params["api_key"] = api_key
    return params


def search(client, endpoints, query, since=None, max_results=50, email=None, api_key=None):
    """The newest works whose title or abstract match the query, newest first; ``since`` is a ``datetime.date``."""
    terms = " ".join(query.replace(",", " ").replace("|", " ").split())
    if not terms:
        raise SourceError("OpenAlex needs a query")
    flt = f"title_and_abstract.search:{terms}"
    if since:
        flt += f",from_publication_date:{since.isoformat()}"
    response = client.get(endpoints.openalex, params=_params({"filter": flt, "sort": "publication_date:desc", "per-page": str(min(max_results, 200)), "select": SELECT}, email, api_key))
    check(response, "OpenAlex")
    try:
        return parse_works(response.json())
    except (ValueError, AttributeError) as exc:
        raise SourceError(f"OpenAlex answered something unreadable ({exc})")


def fetch_by_doi(client, endpoints, doi, email=None, api_key=None):
    response = client.get(endpoints.openalex.rstrip("/") + "/https://doi.org/" + doi, params=_params({"select": SELECT}, email, api_key))
    if response.status == 404:
        return None
    check(response, "OpenAlex")
    try:
        found = parse_works({"results": [response.json()]})
    except (ValueError, AttributeError) as exc:
        raise SourceError(f"OpenAlex answered something unreadable ({exc})")
    return found[0] if found else None


__all__ = ["NAME", "abstract_from_index", "parse_works", "search", "fetch_by_doi", "json"]
