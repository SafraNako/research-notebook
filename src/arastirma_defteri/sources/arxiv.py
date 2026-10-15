"""arXiv's Atom API. Entries arrive newest first when sorted by submission date; ``id`` carries the arXiv identifier and version, ``arxiv:doi`` the journal DOI
when the authors have added one, ``arxiv:journal_ref`` the venue."""
from __future__ import annotations

import re
import shlex
import xml.etree.ElementTree as ET
from datetime import date

from ..model import Paper, year_of
from ..textutil import clean_text, ids_from, norm_arxiv
from . import SourceError, check

NAME = "arxiv"
NS = {"a": "http://www.w3.org/2005/Atom", "o": "http://a9.com/-/spec/opensearch/1.1/", "x": "http://arxiv.org/schemas/atom"}
HOST_INTERVAL = 3.0           # arXiv asks for at most one request every three seconds


def build_query(query, category=None, since=None):
    """An arXiv ``search_query``: every word of the query must occur anywhere (quoted phrases are kept whole), optionally within a category and since a date."""
    parts = []
    if query and ":" in query:
        parts.append(f"({query})")                                  # a query that already uses arXiv field syntax is passed through
    elif query:
        try:
            words = shlex.split(query)
        except ValueError:
            words = query.split()
        parts += ['all:"%s"' % w.replace('"', "") if " " in w else f"all:{w}" for w in words]
    if category:
        parts.append(f"cat:{category}")
    if since:
        parts.append("submittedDate:[%s0000 TO 209912312359]" % since.strftime("%Y%m%d"))
    if not parts:
        raise SourceError("arXiv needs a query or a category")
    return " AND ".join(parts)


def parse_feed(body):
    """``(total_results, [Paper])`` from an Atom response."""
    try:
        root = ET.fromstring(body)
    except ET.ParseError as exc:
        raise SourceError(f"arXiv answered something that is not Atom XML ({exc})")
    total = root.findtext("o:totalResults", default="0", namespaces=NS)
    papers = []
    for e in root.findall("a:entry", NS):
        entry_id = e.findtext("a:id", default="", namespaces=NS)
        aid = norm_arxiv(entry_id)
        title = clean_text(e.findtext("a:title", default="", namespaces=NS))
        if not aid or not title or title == "Error":
            continue
        published = e.findtext("a:published", default="", namespaces=NS)[:10] or None
        papers.append(Paper(
            title=title, authors=[clean_text(a.findtext("a:name", default="", namespaces=NS)) for a in e.findall("a:author", NS)],
            year=year_of(published), date=published, venue=clean_text(e.findtext("x:journal_ref", default="", namespaces=NS)),
            abstract=clean_text(e.findtext("a:summary", default="", namespaces=NS)), url=f"https://arxiv.org/abs/{aid}",
            ids=ids_from(arxiv=aid, doi=e.findtext("x:doi", default="", namespaces=NS) or None), source=NAME, source_id=aid,
            categories=[c.get("term") for c in e.findall("a:category", NS) if c.get("term")]))
    return int(total) if total.isdigit() else len(papers), papers


def search(client, endpoints, query, category=None, since=None, max_results=50):
    """The newest papers matching the query, newest first; ``since`` is a ``datetime.date``."""
    params = {"search_query": build_query(query, category, since), "max_results": str(max_results), "sortBy": "submittedDate", "sortOrder": "descending"}
    response = client.get(endpoints.arxiv, params=params)
    check(response, "arXiv")
    _, papers = parse_feed(response.body)
    if since:
        papers = [p for p in papers if p.date and p.date >= since.isoformat()]
    return papers


def fetch(client, endpoints, arxiv_id):
    response = client.get(endpoints.arxiv, params={"id_list": arxiv_id, "max_results": "1"})
    check(response, "arXiv")
    papers = parse_feed(response.body)[1]
    return papers[0] if papers else None


def valid_category(text):
    return bool(re.fullmatch(r"[A-Za-z\-]+(\.[A-Za-z\-]+)?", text or ""))


__all__ = ["NAME", "HOST_INTERVAL", "build_query", "parse_feed", "search", "fetch", "valid_category", "date"]
