"""Deciding whether two records are the same paper.

1. A shared DOI or PubMed id means the same paper; a shared OpenAlex id means the same record. A shared arXiv id does too, unless both records carry
   different DOIs (then it is not safe to say).
2. Records whose identifiers *conflict* (both have a DOI, a PubMed id or an arXiv id and none is shared) are different papers, however alike their titles.
3. With no shared and no conflicting identifier (an arXiv preprint against its journal version, say), the records are the same paper only if the titles are identical
   after normalisation, the titles are long enough not to be generic ("Introduction"), the years are at most one apart, and the first authors have the same surname.
   When a record fits two or more different papers equally well, it is kept separate rather than guessed.
"""
from __future__ import annotations

from .textutil import first_author_key, norm_title, title_key

KINDS = ("doi", "pmid", "arxiv", "openalex")
MIN_TITLE_WORDS = 4


def id_sets(ids):
    """The identifier dict as ``{kind: set of values}`` (a paper that merged several records may hold several values of a kind)."""
    return {k: ({v} if isinstance(v, str) else set(v)) for k, v in ids.items() if v}


def shared(a, b):
    return [k for k in KINDS if a.get(k) and b.get(k) and a[k] & b[k]]


def conflicts(a, b):
    """Kinds in which both sides have identifiers and none is shared."""
    return [k for k in ("doi", "pmid", "arxiv") if a.get(k) and b.get(k) and not (a[k] & b[k])]


def strong_match(a, b):
    """Decided by identifiers alone: ``True`` (same), ``False`` (different) or ``None`` (the identifiers say nothing either way)."""
    s, c = shared(a, b), conflicts(a, b)
    if "doi" in s or "pmid" in s or "openalex" in s:
        return True
    if "arxiv" in s:
        return False if "doi" in c else True
    return False if c else None


def fuzzy_match(a, b):
    """Same title (normalised), compatible years, same first author: only for records the identifiers could not decide."""
    ta, tb = title_key(a.title), title_key(b.title)
    words = len(norm_title(a.title).split())
    if not ta or ta != tb or words < MIN_TITLE_WORDS:
        return False
    if a.year and b.year and abs(a.year - b.year) > 1:
        return False
    fa, fb = first_author_key(a.authors), first_author_key(b.authors)
    if fa and fb:
        return fa == fb
    return bool(a.year and b.year and a.year == b.year and words >= 6)


def same_paper(a, b):
    """Are two ``Paper`` records (or a stored paper rebuilt as one) the same paper?"""
    decided = strong_match(id_sets(a.ids), id_sets(b.ids))
    return decided if decided is not None else fuzzy_match(a, b)


def cluster(papers):
    """Group records into papers the way the library does when they arrive in the given order; returns a list of lists of indices into ``papers``."""
    groups = []                                                    # each: {"ids": {kind: set}, "rep": Paper, "members": [indices]}
    for i, p in enumerate(papers):
        cand = id_sets(p.ids)
        strong = [g for g in groups if strong_match(g["ids"], cand) is True]
        if strong:
            home = strong[0]
            for other in strong[1:]:
                if not conflicts(home["ids"], other["ids"]):
                    _absorb(home, other)
                    groups.remove(other)
        else:
            fuzzy = [g for g in groups if strong_match(g["ids"], cand) is None and fuzzy_match(g["rep"], p)]
            home = fuzzy[0] if len(fuzzy) == 1 else None
        if home is None:
            groups.append({"ids": cand, "rep": p, "members": [i]})
        else:
            for k, v in cand.items():
                home["ids"].setdefault(k, set()).update(v)
            home["members"].append(i)
    return [sorted(g["members"]) for g in groups]


def _absorb(home, other):
    for k, v in other["ids"].items():
        home["ids"].setdefault(k, set()).update(v)
    home["members"] += other["members"]
