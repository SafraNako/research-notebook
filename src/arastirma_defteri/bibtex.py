"""BibTeX in and out.

Reading handles what real ``.bib`` files contain: nested braces, quoted values, numbers, ``#`` concatenation, ``@string`` macros, ``@comment`` blocks, LaTeX accents and
escapes, ``Last, First`` and ``First Last`` author forms. Writing produces stable citation keys (``smith2024monte``, with ``a``, ``b`` ... on collisions), escapes LaTeX
special characters, keeps UTF-8 as it is, and protects title capitalisation with an extra pair of braces.
"""
from __future__ import annotations

import re

from .model import Paper, year_of
from .textutil import clean_text, ids_from, latex_to_unicode, norm_arxiv, parse_author, strip_accents

_SKIP = {"comment", "preamble"}
_STOP_WORDS = {"a", "an", "the", "of", "on", "in", "for", "and", "to", "with", "by", "from", "at", "as", "is", "are", "using", "towards", "toward"}
_ESCAPE_OUT = {"\\": r"\textbackslash{}", "&": r"\&", "%": r"\%", "$": r"\$", "#": r"\#", "_": r"\_", "{": r"\{", "}": r"\}", "~": r"\textasciitilde{}", "^": r"\textasciicircum{}"}


# --- reading -------------------------------------------------------------------------------------------------------------------------------------
class _Reader:
    def __init__(self, text):
        self.s, self.i = text, 0

    def skip_ws(self):
        while self.i < len(self.s) and self.s[self.i].isspace():
            self.i += 1

    def balanced(self, open_ch, close_ch):
        """The content between a balanced pair of delimiters, starting at the opening one."""
        depth, start = 0, self.i
        while self.i < len(self.s):
            c = self.s[self.i]
            if c == "\\" and self.i + 1 < len(self.s):
                self.i += 2
                continue
            if c == open_ch:
                depth += 1
            elif c == close_ch:
                depth -= 1
                if depth == 0:
                    self.i += 1
                    return self.s[start + 1:self.i - 1]
            self.i += 1
        raise ValueError("unbalanced braces in BibTeX")

    def quoted(self):
        start, depth = self.i + 1, 0
        self.i += 1
        while self.i < len(self.s):
            c = self.s[self.i]
            if c == "\\":
                self.i += 2
                continue
            if c == "{":
                depth += 1
            elif c == "}":
                depth -= 1
            elif c == '"' and depth == 0:
                self.i += 1
                return self.s[start:self.i - 1]
            self.i += 1
        raise ValueError("unterminated quoted value in BibTeX")

    def value(self, macros):
        parts = []
        while True:
            self.skip_ws()
            if self.i >= len(self.s):
                break
            c = self.s[self.i]
            if c == "{":
                parts.append(self.balanced("{", "}"))
            elif c == '"':
                parts.append(self.quoted())
            else:
                m = re.compile(r"[^\s,#}\)]+").match(self.s, self.i)
                if not m:
                    break
                token = m.group(0)
                self.i = m.end()
                parts.append(macros.get(token.lower(), token))
            self.skip_ws()
            if self.i < len(self.s) and self.s[self.i] == "#":
                self.i += 1
                continue
            break
        return "".join(parts)


def parse(text):
    """The entries of a BibTeX source as ``[{"type", "key", "fields": {name: raw value}}]`` (names lower-case; macros expanded)."""
    r, entries, macros = _Reader(text), [], {}
    while True:
        j = r.s.find("@", r.i)
        if j < 0:
            break
        r.i = j + 1
        m = re.compile(r"\s*([A-Za-z]+)\s*").match(r.s, r.i)
        if not m:
            continue
        kind, r.i = m.group(1).lower(), m.end()
        if r.i >= len(r.s) or r.s[r.i] not in "{(":
            continue
        close = "}" if r.s[r.i] == "{" else ")"
        if kind in _SKIP:
            r.balanced(r.s[r.i], close)
            continue
        body_start = r.i
        r.i += 1
        if kind == "string":
            r.skip_ws()
            m2 = re.compile(r"([^\s=]+)\s*=").match(r.s, r.i)
            if m2:
                r.i = m2.end()
                macros[m2.group(1).lower()] = r.value(macros)
            r.i = body_start
            r.balanced(r.s[r.i], close)
            continue
        m2 = re.compile(r"\s*([^,\s]+)\s*,").match(r.s, r.i)
        if not m2:
            r.i = body_start
            r.balanced(r.s[r.i], close)
            continue
        key, r.i, fields = m2.group(1), m2.end(), {}
        while True:
            r.skip_ws()
            if r.i >= len(r.s) or r.s[r.i] == close:
                r.i += 1
                break
            m3 = re.compile(r"([A-Za-z][A-Za-z0-9_\-:]*)\s*=\s*").match(r.s, r.i)
            if not m3:
                r.i += 1
                continue
            name, r.i = m3.group(1).lower(), m3.end()
            fields[name] = r.value(macros)
            r.skip_ws()
            if r.i < len(r.s) and r.s[r.i] == ",":
                r.i += 1
        entries.append({"type": kind, "key": key, "fields": fields})
    return entries


def split_authors(value):
    """Split an author field at the top-level ``and`` (an ``and`` inside braces belongs to a name)."""
    out, depth, cur, i = [], 0, "", 0
    while i < len(value):
        c = value[i]
        depth += c == "{"
        depth -= c == "}"
        if depth == 0 and re.match(r"\s+and\s+", value[i:], re.I):
            out.append(cur)
            cur = ""
            i += re.match(r"\s+and\s+", value[i:], re.I).end()
            continue
        cur += c
        i += 1
    out.append(cur)
    return [latex_to_unicode(a).strip() for a in out if a.strip()]


def to_papers(entries):
    papers = []
    for e in entries:
        f = e["fields"]
        title = clean_text(latex_to_unicode(f.get("title", "")))
        if not title:
            continue
        eprint = f.get("eprint") or f.get("arxivid") or f.get("arxiv") or ""
        arxiv_id = norm_arxiv(eprint) if eprint and (f.get("archiveprefix", f.get("eprinttype", "arxiv")).lower() == "arxiv") else None
        year = year_of(f.get("year") or f.get("date"))
        papers.append(Paper(
            title=title, authors=split_authors(f.get("author", "")), year=year, date=(f.get("date") or "")[:10] or (str(year) if year else None),
            venue=clean_text(latex_to_unicode(f.get("journal") or f.get("journaltitle") or f.get("booktitle") or f.get("publisher") or "")),
            abstract=clean_text(latex_to_unicode(f.get("abstract", ""))), url=f.get("url", ""),
            ids=ids_from(doi=f.get("doi") or None, arxiv=arxiv_id, pmid=f.get("pmid") or None), source="bibtex", source_id=e["key"]))
    return papers


def loads(text):
    return to_papers(parse(text))


# --- writing -------------------------------------------------------------------------------------------------------------------------------------
def escape(text):
    return "".join(_ESCAPE_OUT.get(c, c) for c in text)


def _key_base(paper):
    surname = re.sub(r"[^a-z0-9]", "", strip_accents(parse_author(paper.authors[0])[0]).casefold()) if paper.authors else "anon"
    word = next((w for w in re.findall(r"[a-z0-9]+", strip_accents(paper.title).casefold()) if w not in _STOP_WORDS), "paper")
    return f"{surname or 'anon'}{paper.year or ''}{word}"


def keys_for(papers):
    """One unique citation key per paper, stable for a given list and order (collisions get ``a``, ``b``, ... appended)."""
    seen, out = {}, []
    for p in papers:
        base = _key_base(p)
        n = seen.get(base, 0)
        seen[base] = n + 1
        out.append(base if n == 0 else base + chr(ord("a") + n - 1) if n <= 26 else f"{base}{n}")
    return out


def dumps(papers, with_abstract=False):
    keys = keys_for(papers)
    chunks = []
    for key, p in zip(keys, papers):
        kind = "article" if p.venue else "misc"
        lines = [f"@{kind}{{{key},", f"  title = {{{{{escape(p.title)}}}}},"]
        if p.authors:
            lines.append("  author = {" + " and ".join(escape(a) for a in p.authors) + "},")
        if p.year:
            lines.append(f"  year = {{{p.year}}},")
        if p.venue:
            lines.append(f"  journal = {{{escape(p.venue)}}},")
        if p.ids.get("doi"):
            lines.append(f"  doi = {{{p.ids['doi']}}},")
        if p.ids.get("arxiv"):
            lines += [f"  eprint = {{{p.ids['arxiv']}}},", "  archiveprefix = {arXiv},"]
        if p.ids.get("pmid"):
            lines.append(f"  pmid = {{{p.ids['pmid']}}},")
        if p.url:
            lines.append(f"  url = {{{p.url}}},")
        if with_abstract and p.abstract:
            lines.append(f"  abstract = {{{escape(p.abstract)}}},")
        lines.append("}")
        chunks.append("\n".join(lines))
    return "\n\n".join(chunks) + ("\n" if chunks else "")
