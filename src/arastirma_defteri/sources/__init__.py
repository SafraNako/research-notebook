"""The sources: arXiv (physics and more, preprints), PubMed (biomedicine, including medical physics journals) and OpenAlex (a broad index of scholarly works)."""
from dataclasses import dataclass


@dataclass
class Endpoints:
    arxiv: str = "https://export.arxiv.org/api/query"
    pubmed_search: str = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esearch.fcgi"
    pubmed_fetch: str = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/efetch.fcgi"
    openalex: str = "https://api.openalex.org/works"


class SourceError(Exception):
    """A source could not deliver (rate limited, down, answered something unreadable); the message says which and why."""


def check(response, source):
    """Raise ``SourceError`` unless the response is a plain success; 429 gets its own message because arXiv and OpenAlex both throttle."""
    if response.status == 429:
        raise SourceError(f"{source} is rate limiting this connection (HTTP 429); try again in a few minutes")
    if response.status != 200:
        raise SourceError(f"{source} answered HTTP {response.status}")
