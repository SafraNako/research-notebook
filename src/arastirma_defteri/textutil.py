"""Text and identifier normalisation: the part that decides whether two records are the same paper.

Titles reach the library as ``Monte Carlo of $^{192}$Ir sources.`` (BibTeX), ``Monte Carlo of <sup>192</sup>Ir sources.`` (PubMed) or with accents written as
``{\\"o}``; authors as ``Smith, John A.``, ``John A. Smith`` or ``Smith JA``; DOIs as URLs, with a ``doi:`` prefix or in upper case. Everything is reduced to a
canonical form here.
"""
from __future__ import annotations

import html
import re
import unicodedata
import urllib.parse

# LaTeX accent commands and their combining characters
_ACCENTS = {'"': "\u0308", "'": "\u0301", "`": "\u0300", "^": "\u0302", "~": "\u0303", "=": "\u0304", ".": "\u0307", "u": "\u0306", "v": "\u030c", "H": "\u030b",
            "c": "\u0327", "k": "\u0328", "r": "\u030a"}
_LOGOS = {r"\BibTeX": "BibTeX", r"\LaTeX": "LaTeX", r"\TeX": "TeX"}
_LETTERS = {r"\ss": "ß", r"\o": "ø", r"\O": "Ø", r"\l": "ł", r"\L": "Ł", r"\aa": "å", r"\AA": "Å", r"\ae": "æ", r"\AE": "Æ", r"\oe": "œ", r"\OE": "Œ", r"\i": "ı"}
_ESCAPES = {r"\textbackslash{}": "\\", r"\textbackslash": "\\", r"\textasciicircum{}": "^", r"\textasciicircum": "^", r"\textasciitilde{}": "~", r"\&": "&", r"\%": "%", r"\$": "$", r"\#": "#", r"\_": "_", r"\{": "{", r"\}": "}", r"\~{}": "~", r"\textasciitilde": "~", r"\^{}": "^"}
_PARTICLES = {"van", "von", "der", "den", "de", "del", "della", "di", "da", "du", "la", "le", "ten", "ter", "bin", "ibn", "al", "el", "dos", "das", "do", "st"}


_SUP = str.maketrans("0123456789+-=()ni", "⁰¹²³⁴⁵⁶⁷⁸⁹⁺⁻⁼⁽⁾ⁿⁱ")
_SUB = str.maketrans("0123456789+-=()", "₀₁₂₃₄₅₆₇₈₉₊₋₌₍₎")
_MATH_COMMANDS = {"alpha": "α", "beta": "β", "gamma": "γ", "delta": "δ", "epsilon": "ε", "varepsilon": "ε", "theta": "θ", "lambda": "λ", "mu": "μ", "nu": "ν", "pi": "π", "rho": "ρ",
                  "sigma": "σ", "tau": "τ", "phi": "φ", "chi": "χ", "psi": "ψ", "omega": "ω", "Delta": "Δ", "Gamma": "Γ", "Omega": "Ω", "Sigma": "Σ", "Phi": "Φ", "Lambda": "Λ",
                  "Theta": "Θ", "times": "×", "pm": "±", "sim": "∼", "approx": "≈", "rightarrow": "→", "to": "→", "leq": "≤", "geq": "≥", "le": "≤", "ge": "≥", "circ": "°",
                  "degree": "°", "cdot": "·", "infty": "∞"}


def _math(body):
    """Inline math (the part between dollar signs) as plain text: super- and subscripts that have Unicode forms become those, Greek letters and common symbols are decoded."""
    s = re.sub(r"\\([A-Za-z]+)", lambda m: _MATH_COMMANDS.get(m.group(1), ""), body)

    def script(mark, table):
        def sub(m):
            inner = m.group(1) if m.group(1) is not None else m.group(2)
            return inner.translate(table) if all(ch in table for ch in map(ord, inner)) else f"{mark}({inner})"
        return sub
    s = re.sub(r"\^(?:\{([^{}]*)\}|(.))", script("^", _SUP), s)
    s = re.sub(r"_(?:\{([^{}]*)\}|(.))", script("_", _SUB), s)
    return s.replace("{", "").replace("}", "")


def latex_to_unicode(text):
    """Decode the LaTeX that appears in BibTeX fields: accents (``{\\"o}``, ``\\'e``, ``\\c{c}``), special letters (``\\ss``), escaped characters, and the
    formatting wrappers (``\\textit{...}``, ``{...}``), whose content is kept."""
    s = text
    placeholders = {}
    for i, (k, v) in enumerate(_ESCAPES.items()):
        mark = chr(0xE000 + i)                                           # a private-use character stands in for each literal until the commands are processed
        placeholders[mark] = v
        s = s.replace(k, mark)
    s = re.sub(r"(?<!\\)\$([^$]*)\$", lambda m: _math(m.group(1)), s)
    s = s.replace("$", "")
    s = re.sub(r"\\([\"'`^~=.uvHckr])\s*\{\s*(\\?[A-Za-z])\s*\}", lambda m: _accent(m.group(1), m.group(2)), s)
    s = re.sub(r"\\([\"'`^~=.])\s*(\\?[A-Za-z])", lambda m: _accent(m.group(1), m.group(2)), s)
    s = re.sub(r"\\([uvHckr])\s+([A-Za-z])", lambda m: _accent(m.group(1), m.group(2)), s)
    for k, v in _LOGOS.items():
        s = re.sub(re.escape(k) + r"(?![A-Za-z])\s*(\{\})?", v, s)
    for k, v in _LETTERS.items():
        s = re.sub(re.escape(k) + r"(?![A-Za-z])\s*(\{\})?", v, s)
    s = re.sub(r"\\(?:textit|textbf|emph|textrm|texttt|textsc|mathrm|mathit|mathbf|text)\s*\{([^{}]*)\}", r"\1", s)
    s = s.replace("\\\\", " ").replace("~", " ")
    s = re.sub(r"\\[A-Za-z]+\s*", "", s)                                 # any remaining command is dropped, its braces' content stays
    s = s.replace("{", "").replace("}", "")
    for mark, literal in placeholders.items():
        s = s.replace(mark, literal)
    return unicodedata.normalize("NFC", s)


def _accent(mark, letter):
    if letter.startswith("\\"):
        base = {r"\i": "i", r"\j": "j"}.get(letter, letter[1:])
    else:
        base = letter
    return unicodedata.normalize("NFC", base + _ACCENTS[mark])


def strip_accents(text):
    return "".join(c for c in unicodedata.normalize("NFKD", text) if not unicodedata.combining(c))


# inline formatting that scholarly sources embed in titles and abstracts; only these are tags, so "p < 0.05 ... x > 3" and "(<6 h)" are never mistaken for markup
_TAG = re.compile(r"</?(?:i|b|em|strong|sup|sub|u|small|sc|scp|span|tt|inf|italic|bold|mml:[A-Za-z]+|math)"
                  r"""(?:\s+[A-Za-z:\-]+\s*=\s*(?:"[^"]*"|'[^']*'))*\s*/?>""")


def strip_tags(text):
    return _TAG.sub("", text)


def norm_title(text):
    """The comparison form of a title: markup and LaTeX removed, accents and case folded, punctuation gone, whitespace collapsed."""
    s = html.unescape(strip_tags(text or ""))
    s = latex_to_unicode(s)
    s = strip_accents(s).casefold()
    return re.sub(r"[\W_]+", " ", s).strip()


def title_key(text):
    """The identity form of a title: ``norm_title`` with the spaces removed too, so that ``192 Ir`` (OpenAlex), ``192Ir`` (PubMed) and ``¹⁹²Ir`` compare equal."""
    return norm_title(text).replace(" ", "")


def clean_text(text):
    """A title or abstract as it should be shown: markup tags removed, entities decoded, whitespace collapsed (formatting such as ``<sup>`` is flattened)."""
    return re.sub(r"\s+", " ", html.unescape(strip_tags(text or ""))).strip()


# --- identifiers ---------------------------------------------------------------------------------------------------------------------------------
_ARXIV_NEW = re.compile(r"^(\d{4}\.\d{4,5})(v\d+)?$")
_ARXIV_OLD = re.compile(r"^([a-z]+(?:-[a-z]+)*(?:\.[a-z]{2})?/\d{7})(v\d+)?$", re.I)


def norm_arxiv(value):
    """The arXiv identifier without version (``2301.00001``, ``hep-th/9901001``) from any of its usual spellings, or ``None``."""
    s = urllib.parse.unquote((value or "").strip())
    s = re.sub(r"^(https?://)?(www\.)?arxiv\.org/(abs|pdf)/", "", s, flags=re.I)
    s = re.sub(r"^arxiv:\s*", "", s, flags=re.I)
    s = re.sub(r"^10\.48550/arxiv\.", "", s, flags=re.I)
    s = re.sub(r"\.pdf$", "", s)
    m = _ARXIV_NEW.match(s) or _ARXIV_OLD.match(s)
    return m.group(1).lower() if m else None


def norm_doi(value):
    """A DOI in lower case without URL or ``doi:`` prefix, or ``None``. arXiv's own DOIs (``10.48550/arXiv.*``) are arXiv identifiers, not DOIs."""
    s = urllib.parse.unquote(html.unescape((value or "").strip()))
    s = re.sub(r"^(https?://)?(dx\.)?doi\.org/", "", s, flags=re.I)
    s = re.sub(r"^doi:\s*", "", s, flags=re.I).strip().lower()
    s = s.rstrip(".,;")
    while s.endswith(")") and s.count(")") > s.count("("):
        s = s[:-1]
    if s.startswith("10.48550/arxiv."):
        return None
    return s if re.match(r"^10\.\d{4,9}/\S+$", s) else None


def norm_pmid(value):
    s = (value or "").strip()
    s = re.sub(r"^https?://pubmed\.ncbi\.nlm\.nih\.gov/", "", s, flags=re.I).rstrip("/")
    s = re.sub(r"^pmid:\s*", "", s, flags=re.I)
    return s if re.fullmatch(r"\d{1,9}", s) else None


def norm_openalex(value):
    m = re.search(r"(W\d+)$", (value or "").strip())
    return m.group(1) if m else None


def ids_from(*, doi=None, arxiv=None, pmid=None, openalex=None):
    """The normalised identifier dict (only the identifiers that are present and valid); an arXiv DOI given as ``doi`` becomes an ``arxiv`` identifier."""
    out = {}
    if doi:
        bare = re.sub(r"^doi:\s*", "", re.sub(r"^(https?://)?(dx\.)?doi\.org/", "", doi.strip(), flags=re.I), flags=re.I)
        if bare.lower().startswith("10.48550/arxiv."):
            if norm_arxiv(bare):
                out["arxiv"] = norm_arxiv(bare)
        elif norm_doi(bare):
            out["doi"] = norm_doi(bare)
    if arxiv and norm_arxiv(arxiv):
        out["arxiv"] = norm_arxiv(arxiv)
    if pmid and norm_pmid(pmid):
        out["pmid"] = norm_pmid(pmid)
    if openalex and norm_openalex(openalex):
        out["openalex"] = norm_openalex(openalex)
    return out


# --- authors -------------------------------------------------------------------------------------------------------------------------------------
def parse_author(name):
    """``(surname, given)`` of a name written ``Surname, Given`` or ``Given Surname`` (particles such as *van der* stay with the surname)."""
    name = re.sub(r"\s+", " ", (name or "").strip())
    if "," in name:
        surname, _, given = name.partition(",")
        return surname.strip(), given.strip()
    tokens = name.split(" ")
    if len(tokens) == 1:
        return name, ""
    i = len(tokens) - 1
    while i > 1 and tokens[i - 1].casefold().rstrip(".") in _PARTICLES:
        i -= 1
    return " ".join(tokens[i:]), " ".join(tokens[:i])


def surname_key(name):
    """The comparison form of an author's surname: accents and case folded, everything but letters removed (``van der Berg`` and ``Van-Der-Berg`` agree)."""
    surname, _ = parse_author(latex_to_unicode(name))
    return re.sub(r"[\W_\d]+", "", strip_accents(surname).casefold())


def first_author_key(authors):
    return surname_key(authors[0]) if authors else ""


def display_authors(authors, limit=3):
    names = [", ".join(p for p in parse_author(a) if p) for a in authors]
    return "; ".join(names[:limit]) + (f" et al. ({len(authors)})" if len(authors) > limit else "")
