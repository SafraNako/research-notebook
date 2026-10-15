"""A paper as it arrives from a source (or from BibTeX, or typed in): metadata plus the identifiers that let records of the same paper find each other."""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class Paper:
    title: str
    authors: list = field(default_factory=list)     # as written by the source, in order
    year: int = None
    date: str = None                                 # 'YYYY-MM-DD', 'YYYY-MM' or 'YYYY': as precise as the source knows
    venue: str = ""
    abstract: str = ""
    url: str = ""
    ids: dict = field(default_factory=dict)          # normalised: doi, arxiv, pmid, openalex
    source: str = ""                                 # arxiv | pubmed | openalex | bibtex | manual
    source_id: str = ""
    categories: list = field(default_factory=list)


def year_of(date):
    try:
        return int(str(date)[:4])
    except (TypeError, ValueError):
        return None
