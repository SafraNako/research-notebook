"""PubMed through NCBI's E-utilities: ``esearch`` returns identifiers (newest first), ``efetch`` returns the records as XML with titles, abstracts and authors.

Titles and abstracts can contain inline markup (``<sup>192</sup>Ir``), which is flattened to text; the publication date is the electronic date when there is one,
else the journal's issue date (which may be only a year, or a season such as ``Oct-Dec``)."""
from __future__ import annotations

import re
import xml.etree.ElementTree as ET

from ..model import Paper, year_of
from ..textutil import clean_text, ids_from
from . import SourceError, check

NAME = "pubmed"
HOST_INTERVAL = 0.4           # NCBI allows three requests per second without an API key
_MONTHS = {m: i for i, m in enumerate(["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"], 1)}


def _text(el):
    return clean_text("".join(el.itertext())) if el is not None else ""


def _date(parent):
    """``YYYY-MM-DD`` (or ``YYYY-MM``, ``YYYY``) from a ``Year``/``Month``/``Day`` element group, or ``None``."""
    if parent is None:
        return None
    year = parent.findtext("Year")
    if not year or not year.isdigit():
        medline = parent.findtext("MedlineDate") or ""
        m = re.match(r"(\d{4})", medline)
        return m.group(1) if m else None
    month = (parent.findtext("Month") or "").strip().lower()[:3]
    month_n = int(month) if month.isdigit() else _MONTHS.get(month)
    day = parent.findtext("Day")
    if month_n and day and day.isdigit():
        return f"{year}-{month_n:02d}-{int(day):02d}"
    return f"{year}-{month_n:02d}" if month_n else year


def parse_efetch(body):
    try:
        root = ET.fromstring(body)
    except ET.ParseError as exc:
        raise SourceError(f"PubMed answered something that is not XML ({exc})")
    papers = []
    for art in root.findall("PubmedArticle"):
        cite = art.find("MedlineCitation")
        article = cite.find("Article") if cite is not None else None
        if article is None:
            continue
        pmid = cite.findtext("PMID")
        title = _text(article.find("ArticleTitle")).rstrip(".")
        if not pmid or not title:
            continue
        parts = []
        for at in article.findall("Abstract/AbstractText"):
            label, text = at.get("Label"), _text(at)
            parts.append(f"{label}: {text}" if label and label.upper() not in ("UNLABELLED", "UNLABELED") else text)
        authors = []
        for a in article.findall("AuthorList/Author"):
            if a.findtext("CollectiveName"):
                authors.append(a.findtext("CollectiveName").strip())
            elif a.findtext("LastName"):
                given = a.findtext("ForeName") or a.findtext("Initials") or ""
                authors.append(f"{given} {a.findtext('LastName')}".strip())
        doi = next((e.text for e in article.findall("ELocationID") if e.get("EIdType") == "doi" and e.text), None)
        if not doi:
            doi = next((i.text for i in art.findall("PubmedData/ArticleIdList/ArticleId") if i.get("IdType") == "doi" and i.text), None)
        date = _date(article.find("ArticleDate")) or _date(article.find("Journal/JournalIssue/PubDate"))
        journal = article.find("Journal")
        venue = (journal.findtext("ISOAbbreviation") or journal.findtext("Title") or "") if journal is not None else ""
        papers.append(Paper(title=title, authors=authors, year=year_of(date), date=date, venue=venue.strip(), abstract=" ".join(parts), url=f"https://pubmed.ncbi.nlm.nih.gov/{pmid}/",
                            ids=ids_from(doi=doi, pmid=pmid), source=NAME, source_id=pmid))
    return papers


def _common(email, api_key):
    params = {"tool": "research-notebook"}
    if email:
        params["email"] = email
    if api_key:
        params["api_key"] = api_key
    return params


def fetch_ids(client, endpoints, pmids, email=None, api_key=None):
    out = []
    for i in range(0, len(pmids), 150):
        chunk = pmids[i:i + 150]
        response = client.get(endpoints.pubmed_fetch, params={"db": "pubmed", "id": ",".join(chunk), "retmode": "xml", **_common(email, api_key)})
        check(response, "PubMed")
        out += parse_efetch(response.body)
    return out


def search(client, endpoints, query, since=None, max_results=50, email=None, api_key=None):
    """The newest records matching the query; ``since`` (a ``datetime.date``) restricts to records added to PubMed on or after it."""
    params = {"db": "pubmed", "term": query, "retmax": str(max_results), "retmode": "json", "sort": "pub_date", **_common(email, api_key)}
    if since:
        params.update({"datetype": "edat", "mindate": since.strftime("%Y/%m/%d"), "maxdate": "3000"})
    response = client.get(endpoints.pubmed_search, params=params)
    check(response, "PubMed")
    try:
        result = response.json()["esearchresult"]
    except (ValueError, KeyError) as exc:
        raise SourceError(f"PubMed answered something unreadable ({exc})")
    if "ERROR" in result:
        raise SourceError(f"PubMed rejected the query: {result['ERROR']}")
    ids = result.get("idlist", [])
    return fetch_ids(client, endpoints, ids, email, api_key) if ids else []
