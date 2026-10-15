"""A planted-truth scholarly world for tests and validation: a real local HTTP server that speaks the formats of arXiv (Atom), PubMed's E-utilities (esearch JSON,
efetch XML) and OpenAlex (JSON), serving a made-up literature whose structure is fully known.

Each *work* has a true identity; each source holds zero or more *records* of it, written the way that source writes them (arXiv: ``First Last`` and LaTeX in titles;
PubMed: ``<sup>`` markup, trailing period, ``LastName``/``ForeName``, a journal issue date; OpenAlex: initials, Unicode superscripts, upper-case DOI URLs, abstracts as an
inverted index). The corpus contains the cases that make identity resolution hard: preprints that become journal papers, arXiv DOIs, parts I and II, generic titles, the same
title with different DOIs, a paper whose title changed between preprint and journal (a known miss, declared as such), and records that appear on different days.

Nothing here touches the network beyond the loopback interface.
"""
from __future__ import annotations

import json
import re
import threading
import time
import xml.sax.saxutils as sax
from dataclasses import dataclass, field
from datetime import date
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, unquote, urlparse

from .sources import Endpoints

SUP = str.maketrans("0123456789", "⁰¹²³⁴⁵⁶⁷⁸⁹")
AUTHORS = {
    "jm": "José Müller", "vb": "Anna van der Berg", "wz": "Wei Zhang", "md": "Marie-Claire Dupont", "ay": "Ayşe Yılmaz", "on": "Patrick O'Neill", "ep": "Elena Petrova",
    "kt": "Kenji Tanaka", "sa": "Sofía Álvarez", "dc": "David Cohen", "pn": "Priya Nair", "tb": "Thomas Becker", "ls": "Lars Søndergaard", "fh": "Fatima Al-Hassan",
    "js": "John Smith", "jx": "Jane Smith",
}


@dataclass
class Work:
    wid: str
    title: str                                   # canonical, with <sup> markup where the physics has superscripts
    authors: list
    date: str                                    # publication date of the journal version (or the preprint, for preprint-only works)
    abstract: str = ""
    venue: str = "Phys Med Biol"
    doi: str = None
    pmid: str = None
    arxiv: str = None
    categories: list = field(default_factory=lambda: ["physics.med-ph"])
    records: dict = field(default_factory=dict)  # source -> overrides: title, authors_style, date, appears, doi (arxiv:doi), arxiv_doi (openalex), with_doi, with_pmid


@dataclass
class Record:
    work: Work
    source: str
    source_id: str
    title: str
    authors: list
    date: str
    appears: str
    ids: dict
    extra: dict


def _w(wid, title, authors, d, abstract, doi=None, pmid=None, arxiv=None, venue="Phys Med Biol", records=None, categories=None):
    return Work(wid, title, [AUTHORS[a] for a in authors], d, abstract, venue, doi, pmid, arxiv, categories or ["physics.med-ph"], records or {})


def build_works():
    """The planted literature. Titles contain the topic words the watch queries use (Monte Carlo, brachytherapy, dose, proton, MRI, ...)."""
    both = {"pubmed": {}, "openalex": {}}
    works = [
        # --- A: journal papers in PubMed and OpenAlex (shared DOI and PubMed id) --------------------------------------------------------------------
        _w("a1", "Dosimetry of <sup>192</sup>Ir brachytherapy sources: a Monte Carlo study", ["jm", "vb"], "2026-09-10", "We compute dose rate constants for HDR brachytherapy sources with Monte Carlo.",
           "10.1016/j.apradiso.2026.100101", "50000001", venue="Appl Radiat Isot", records=dict(both, openalex={"authors_style": "initials"})),
        _w("a2", "Out-of-field dose in proton therapy measured with a thermoluminescent phantom", ["wz"], "2026-09-22", "Thermoluminescent detectors in an anthropomorphic phantom measure secondary dose.",
           "10.1088/1361-6560/ae0002", "50000002", records=both),
        _w("a3", "Commissioning of an MRI-guided linac: beam model validation", ["md", "ay", "on"], "2026-10-02", "Beam model validation for an MRI linac in the presence of the magnetic field.",
           "10.1002/mp.17003", "50000003", venue="Med Phys", records=dict(both, openalex={"authors_style": "initials"})),
        _w("a4", "A deep learning approach to dose calculation in radiotherapy", ["ep", "kt"], "2026-08-28", "A convolutional network predicts dose distributions for radiotherapy plans.",
           "10.1016/j.radonc.2026.110004", "50000004", venue="Radiother Oncol", records=both),
        _w("a5", "Radiochromic film dosimetry for small fields", ["sa"], "2026-09-15", "Film dosimetry of small radiotherapy fields with uncertainty analysis.",
           "10.1088/1361-6560/ae0005", "50000005", records=both),
        _w("a6", "Patient-specific quality assurance with machine learning", ["dc", "pn"], "2026-10-08", "Machine learning predicts gamma passing rates for patient quality assurance.",
           "10.1002/acm2.70006", "50000006", venue="J Appl Clin Med Phys", records=both),
        _w("a7", "Neutron contamination in high-energy photon beams", ["tb"], "2026-08-12", "Neutron dose near linear accelerators operating above ten megavolts.",
           "10.1016/j.radmeas.2026.107007", "50000007", venue="Radiat Meas", records=both),
        _w("a8", "Gamma index analysis revisited: tolerance and noise", ["ls", "fh"], "2026-09-30", "How tolerance and image noise change the gamma index in dose comparison.",
           "10.1088/1361-6560/ae0008", "50000008", records=dict(both, openalex={"authors_style": "initials"})),
        # --- B: arXiv preprint, then the journal version (no shared identifier: only title, first author and year link them) -------------------------
        _w("b1", "Fast Monte Carlo dose calculation on GPU for proton therapy", ["wz", "jm"], "2026-10-05", "A GPU Monte Carlo engine for proton dose calculation.", "10.1088/1361-6560/ae0101", "50000101",
           arxiv="2608.10101", records={"arxiv": {"date": "2026-08-20", "title": "Fast Monte Carlo dose calculation on GPU for proton therapy"}, "pubmed": {"title": "Fast Monte Carlo dose calculation on GPU for proton therapy."},
                                         "openalex": {}}),
        _w("b2", "Voxel phantoms for CT dose estimation", ["ay"], "2026-10-01", "Voxel phantoms improve organ dose estimates from CT.", "10.1002/mp.17102", "50000102", venue="Med Phys", arxiv="2607.10102",
           records={"arxiv": {"date": "2026-07-28", "title": "Voxel Phantoms for CT Dose Estimation"}, "pubmed": {}, "openalex": {"title": "Voxel Phantoms For CT Dose Estimation"}}),
        _w("b3", "Uncertainty quantification in <sup>192</sup>Ir brachytherapy dose calculation", ["md", "ep"], "2026-09-18", "Uncertainty propagation in brachytherapy dose calculation.", "10.1016/j.brachy.2026.110103", "50000103",
           venue="Brachytherapy", arxiv="2606.10103", records={"arxiv": {"date": "2026-06-30", "title": "Uncertainty quantification in 192 Ir brachytherapy dose calculation"}, "pubmed": {},
                                                              "openalex": {"title": "Uncertainty quantification in 192 Ir brachytherapy dose calculation"}}),
        _w("b4", "Respiratory motion modelling for lung radiotherapy", ["on", "kt", "sa"], "2026-09-25", "Motion models from 4D CT for lung radiotherapy planning.", "10.1088/1361-6560/ae0104", "50000104",
           arxiv="2607.10104", records={"arxiv": {"date": "2026-07-15", "authors_style": "initials"}, "pubmed": {}, "openalex": {}}),
        _w("b5", "{C}herenkov imaging for in vivo dosimetry", ["dc"], "2026-10-09", "Cherenkov light imaging gives in vivo dose information during radiotherapy.", "10.1002/mp.17105", "50000105",
           venue="Med Phys", arxiv="2609.10105", records={"arxiv": {"date": "2026-09-05", "title": "{C}herenkov imaging for in vivo dosimetry"}, "pubmed": {"title": "Cherenkov imaging for in vivo dosimetry."}, "openalex": {}}),
        # --- C: arXiv record carries the journal DOI itself ------------------------------------------------------------------------------------------
        _w("c1", "Spectral CT calibration with a Monte Carlo forward model", ["pn", "fh"], "2026-09-12", "Spectral CT calibration using a Monte Carlo model.", "10.1088/1361-6560/ae0201", arxiv="2609.10201",
           records={"arxiv": {"with_doi": True}, "openalex": {}}),
        _w("c2", "Proton range verification by prompt gamma imaging", ["tb", "ls"], "2026-08-30", "Prompt gamma imaging verifies proton range in vivo.", "10.1016/j.ejmp.2026.102202", arxiv="2608.10202", venue="Phys Med",
           records={"arxiv": {"with_doi": True}, "openalex": {}}),
        _w("c3", "Linac log files for dose reconstruction", ["jx"], "2026-10-03", "Log files enable dose reconstruction without measurements.", "10.1002/acm2.70203", arxiv="2610.10203",
           venue="J Appl Clin Med Phys", records={"arxiv": {"with_doi": True}, "openalex": {}}),
        # --- D: OpenAlex lists the preprint under arXiv's own DOI -----------------------------------------------------------------------------------
        _w("d1", "Normal tissue complication models for proton therapy of head and neck cancer", ["vb", "dc"], "2026-09-08", "NTCP models for proton therapy of head and neck cancer.", arxiv="2609.10301",
           records={"arxiv": {}, "openalex": {"arxiv_doi": True}}),
        _w("d2", "Phantom-based end-to-end testing of MRI guided radiotherapy", ["ay", "md"], "2026-08-18", "End-to-end phantom testing for MRI guided radiotherapy.", arxiv="2608.10302",
           records={"arxiv": {}, "openalex": {"arxiv_doi": True}}),
        _w("d3", "Bayesian optimisation of brachytherapy applicator positions", ["ep"], "2026-10-11", "Bayesian optimisation of applicator positions in brachytherapy.", arxiv="2610.10303",
           records={"arxiv": {}, "openalex": {"arxiv_doi": True}}),
        # --- E: singletons -------------------------------------------------------------------------------------------------------------------------
        _w("e1", "Cone beam CT scatter correction with Monte Carlo priors", ["kt"], "2026-09-02", "Scatter correction for cone beam CT.", arxiv="2609.10401", records={"arxiv": {}}),
        _w("e2", "Linac head model tuning for dose calculation", ["on"], "2026-10-12", "Tuning a linac head model for dose calculation.", arxiv="2610.10402", records={"arxiv": {}}),
        _w("e3", "Dose painting with proton beams: a simulation study", ["sa", "jx"], "2026-08-05", "Dose painting with proton beams.", arxiv="2608.10403", records={"arxiv": {}}),
        _w("e4", "Silicon detectors for proton dose verification", ["fh"], "2026-09-27", "Silicon detectors verify proton dose.", arxiv="2609.10404", records={"arxiv": {}}),
        _w("e5", "Radiation dose to the heart in breast radiotherapy", ["jm", "tb"], "2026-09-03", "Cardiac dose in breast radiotherapy.", "10.1016/j.ijrobp.2026.110501", "50000501", venue="Int J Radiat Oncol Biol Phys", records={"pubmed": {}}),
        _w("e6", "Magnetic resonance imaging artefacts near brachytherapy applicators", ["wz"], "2026-10-04", "MRI artefacts near brachytherapy applicators.", "10.1002/jmri.30502", "50000502", venue="J Magn Reson Imaging", records={"pubmed": {}}),
        _w("e7", "Stereotactic radiosurgery dose gradient measurement", ["ls"], "2026-08-22", "Measuring steep dose gradients in radiosurgery.", "10.1016/j.radonc.2026.110503", "50000503", venue="Radiother Oncol", records={"pubmed": {}}),
        _w("e8", "Patient dose during interventional radiology procedures", ["pn", "ay"], "2026-09-14", "Skin dose in interventional radiology.", "10.1016/j.ejmp.2026.102504", "50000504", venue="Phys Med", records={"pubmed": {}}),
        _w("e9", "Smart triage for Monte Carlo dosimetry workflows", ["dc"], "2026-10-13", "Triage of dosimetry workflows with unsupervised learning.", "10.5281/zenodo.9000001", venue="Zenodo", records={"openalex": {}}),
        _w("e10", "Dosimetric evaluation of alternative capsules for an HDR source", ["ep", "md"], "2026-10-10", "Capsule materials and HDR source dosimetry.", "10.5281/zenodo.9000002", venue="Zenodo", records={"openalex": {}}),
        _w("e11", "Shielding composites for secondary radiation: a modelling comparison", ["kt", "jm"], "2026-10-06", "Composite shields for secondary radiation.", "10.24200/jonra.2026.9000003", venue="J Nucl Res", records={"openalex": {}}),
        _w("e12", "Voxel size effects on hand dose calculation", ["fh"], "2026-09-29", "Voxel size and hand dose in nuclear medicine.", "10.1007/s12194-026-90004-w", venue="Radiol Phys Technol", records={"openalex": {}}),
        # --- traps: look alike, are different ---------------------------------------------------------------------------------------------------------
        _w("t1a", "Dose calculation in proton therapy. Part I: Methods", ["vb", "wz"], "2026-09-06", "Part one of a two part series on proton dose calculation.", "10.1088/1361-6560/ae0601", "50000601", records=both),
        _w("t1b", "Dose calculation in proton therapy. Part II: Validation", ["vb", "wz"], "2026-09-06", "Part two of a two part series on proton dose calculation.", "10.1088/1361-6560/ae0602", "50000602", records=both),
        _w("t2a", "Introduction", ["js"], "2026-09-11", "An editorial introduction to a special issue on dose.", "10.1088/1361-6560/ae0603", "50000603", records={"pubmed": {}}),
        _w("t2b", "Introduction", ["dc"], "2026-09-11", "An introduction to dose in radiology.", "10.1002/mp.17604", venue="Med Phys", records={"openalex": {}}),
        _w("t3a", "Neural networks for beam data modelling", ["js"], "2024-05-02", "Neural networks model linac beam data.", arxiv="2405.10701", records={"arxiv": {}}),
        _w("t3b", "Neural networks for beam data modelling", ["js"], "2026-09-30", "A journal paper on neural networks for linac beam data modelling.", "10.1088/1361-6560/ae0702", "50000702", records={"pubmed": {}}),
        _w("t4a", "Machine learning for dosimetry", ["js"], "2026-08-09", "Machine learning methods in radiation dosimetry.", "10.1088/1361-6560/ae0801", "50000801", records={"pubmed": {}}),
        _w("t4b", "Machine learning for dosimetry", ["js"], "2026-08-09", "A different paper with the same title on dosimetry.", "10.1016/j.radphyschem.2026.110802", venue="Radiat Phys Chem", records={"openalex": {}}),
        _w("t5", "Correction to: Radiochromic film dosimetry for small fields", ["sa"], "2026-10-07", "Correction of a figure in the film dosimetry paper.", "10.1088/1361-6560/ae0905", "50000905", records={"pubmed": {}}),
        _w("t6a", "ResNet-18 for CT organ segmentation", ["ep"], "2026-09-19", "Organ segmentation on CT with a small residual network.", arxiv="2609.11001", records={"arxiv": {}}),
        _w("t6b", "ResNet-50 for CT organ segmentation", ["ep"], "2026-09-19", "Organ segmentation on CT with a larger residual network.", arxiv="2609.11002", records={"arxiv": {}}),
        # --- a known miss: the title changed between preprint and journal, no shared identifier -----------------------------------------------------
        _w("m1", "Dose engines for brachytherapy: a comparison", ["on", "jm"], "2026-10-06", "Comparison of dose engines for brachytherapy.", "10.1016/j.brachy.2026.111001", "50001001", venue="Brachytherapy",
           arxiv="2608.11001", records={"arxiv": {"date": "2026-08-25", "title": "A comparison of dose calculation engines for brachytherapy"}, "pubmed": {}}),
    ]
    return works


KNOWN_MISSES = [{"m1"}]            # works whose records the library is expected to leave split (documented limitation)


def _tex(title):
    """The canonical title the way an arXiv author writes it in LaTeX: ``<sup>192</sup>`` becomes ``$^{192}$``."""
    return re.sub(r"<sup>(.*?)</sup>", r"$^{\1}$", title)


def _plain(title):
    return re.sub(r"<[^>]+>", "", title)


def _unicode(title):
    return re.sub(r"<sup>(\d+)</sup>", lambda m: m.group(1).translate(SUP), title)


def _initials(name):
    parts = name.split()
    return " ".join([p[0] + "." for p in parts[:-1]] + [parts[-1]]) if len(parts) > 1 else name


def make_records(works):
    out = []
    for w in works:
        for src, o in w.records.items():
            title = o.get("title")
            if src == "arxiv":
                rec_title = title or _tex(w.title)
                authors = [_initials(a) for a in w.authors] if o.get("authors_style") == "initials" else list(w.authors)
                rec_date = o.get("date", w.date)
                ids = {"arxiv": w.arxiv}
                if o.get("with_doi"):
                    ids["doi"] = w.doi
                sid = w.arxiv
            elif src == "pubmed":
                rec_title = title or (_plain(w.title) + ".")
                authors = list(w.authors)
                rec_date = o.get("date", w.date)
                ids = {"pmid": w.pmid, "doi": w.doi}
                sid = w.pmid
            else:
                rec_title = title or _unicode(w.title)
                authors = [_initials(a) for a in w.authors] if o.get("authors_style") == "initials" else list(w.authors)
                rec_date = o.get("date", w.date)
                ids = {}
                if o.get("arxiv_doi"):
                    ids["arxiv"] = w.arxiv
                else:
                    if w.doi:
                        ids["doi"] = w.doi
                    if w.pmid and "pubmed" in w.records:
                        ids["pmid"] = w.pmid
                sid = "W" + str(7000000000 + len(out) * 7 + 1)
            out.append(Record(w, src, sid, rec_title, authors, rec_date, o.get("appears", rec_date), ids, o))
    return out


class PlantedScholarship:
    """The planted works and their records, plus the truth the tests compare with."""

    def __init__(self, later=False):
        self.works = build_works()
        self.records = make_records(self.works)
        self.today = date(2026, 10, 15)
        self.later = later

    def visible(self):
        return [r for r in self.records if r.appears <= self.today.isoformat()]

    def truth_partition(self, records=None):
        """The expected clusters of record keys ``(source, source_id)``: one per work, except the known-miss works, whose records stay split by source."""
        records = records if records is not None else self.records
        by_work = {}
        for r in records:
            by_work.setdefault(r.work.wid, []).append(r)
        clusters = []
        for wid, rs in by_work.items():
            if any(wid in miss for miss in KNOWN_MISSES):
                clusters += [{(r.source, r.source_id)} for r in rs]
            else:
                clusters.append({(r.source, r.source_id) for r in rs})
        return clusters


# --- rendering one record the way its source writes it ------------------------------------------------------------------------------------------
def _split_name(name):
    parts = name.split(" ")
    i = len(parts) - 1
    while i > 1 and parts[i - 1].lower() in ("van", "der", "von", "de", "den"):
        i -= 1
    return " ".join(parts[:i]), " ".join(parts[i:])


def atom_entry(r, version="v1"):
    w = r.work
    aid = r.ids["arxiv"]
    authors = "".join(f"<author><name>{sax.escape(a)}</name></author>" for a in r.authors)
    cats = "".join(f'<category term="{c}" scheme="http://arxiv.org/schemas/atom"/>' for c in w.categories)
    title = sax.escape(r.title)
    wrapped = re.sub(r"(.{38,60}?) ", r"\1\n  ", title, count=1)               # arXiv wraps long titles over lines
    extra = (f"<arxiv:doi>{r.ids['doi']}</arxiv:doi>" if r.ids.get("doi") else "") + (f"<arxiv:journal_ref>{sax.escape(w.venue)} (2026)</arxiv:journal_ref>" if r.ids.get("doi") else "")
    return (f"<entry><id>http://arxiv.org/abs/{aid}{version}</id><title>{wrapped}</title><updated>{r.date}T10:00:00Z</updated>"
            f'<link href="https://arxiv.org/abs/{aid}{version}" rel="alternate" type="text/html"/>'
            f"<summary>  {sax.escape(w.abstract)}\n</summary>{cats}<published>{r.date}T10:00:00Z</published>{extra}"
            f'<arxiv:primary_category term="{w.categories[0]}"/>{authors}</entry>')


def pubmed_article(r):
    w = r.work
    y, m, d = r.date.split("-")
    authors = ""
    for a in r.authors:
        fore, last = _split_name(a)
        initials = "".join(p[0] for p in re.split(r"[ \-]", fore) if p)
        authors += f"<Author ValidYN=\"Y\"><LastName>{sax.escape(last)}</LastName><ForeName>{sax.escape(fore)}</ForeName><Initials>{initials}</Initials></Author>"
    month_name = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"][int(m) - 1]
    pubdate = f"<PubDate><Year>{y}</Year><Month>{month_name}</Month></PubDate>" if int(w.pmid) % 3 else f"<PubDate><MedlineDate>{y} {month_name}-Dec</MedlineDate></PubDate>"
    article_date = f'<ArticleDate DateType="Electronic"><Year>{y}</Year><Month>{m}</Month><Day>{d}</Day></ArticleDate>' if int(w.pmid) % 2 else ""
    title = re.sub(r"<sup>(.*?)</sup>", r"<sup>\1</sup>", r.title)
    title = title.replace("&", "&amp;") if "<" not in title else title
    abstract = f'<AbstractText Label="BACKGROUND">{sax.escape(w.abstract)}</AbstractText>' if int(w.pmid) % 2 else f"<AbstractText>{sax.escape(w.abstract)}</AbstractText>"
    return (f'<PubmedArticle><MedlineCitation Status="MEDLINE" Owner="NLM"><PMID Version="1">{w.pmid}</PMID><Article PubModel="Print-Electronic">'
            f"<Journal><ISSN IssnType=\"Electronic\">1234-5678</ISSN><JournalIssue CitedMedium=\"Internet\"><Volume>1</Volume>{pubdate}</JournalIssue><Title>{sax.escape(w.venue)} full title</Title>"
            f"<ISOAbbreviation>{sax.escape(w.venue)}</ISOAbbreviation></Journal><ArticleTitle>{title}</ArticleTitle>"
            f'<ELocationID EIdType="doi" ValidYN="Y">{w.doi}</ELocationID><Abstract>{abstract}</Abstract><AuthorList CompleteYN="Y">{authors}</AuthorList>{article_date}</Article></MedlineCitation>'
            f'<PubmedData><ArticleIdList><ArticleId IdType="pubmed">{w.pmid}</ArticleId><ArticleId IdType="doi">{w.doi}</ArticleId></ArticleIdList></PubmedData></PubmedArticle>')


def openalex_work(r):
    w = r.work
    words = {}
    for i, word in enumerate(w.abstract.split()):
        words.setdefault(word, []).append(i)
    ids = {"openalex": "https://openalex.org/" + r.source_id}
    doi = None
    if r.ids.get("doi"):
        doi = "https://doi.org/" + r.ids["doi"].upper()
        ids["doi"] = doi
    elif r.ids.get("arxiv"):
        doi = "https://doi.org/10.48550/arXiv." + r.ids["arxiv"]
        ids["doi"] = doi
    if r.ids.get("pmid"):
        ids["pmid"] = "https://pubmed.ncbi.nlm.nih.gov/" + r.ids["pmid"]
    return {"id": "https://openalex.org/" + r.source_id, "doi": doi, "title": r.title, "display_name": r.title, "publication_year": int(r.date[:4]), "publication_date": r.date, "ids": ids,
            "primary_location": {"landing_page_url": doi or ("https://openalex.org/" + r.source_id), "source": {"display_name": w.venue}},
            "type": "article", "authorships": [{"author": {"display_name": a}, "raw_author_name": a} for a in r.authors], "abstract_inverted_index": words, "cited_by_count": 0}


# --- the server ----------------------------------------------------------------------------------------------------------------------------------
class Faults:
    """Scripted failures per route: ``{"arxiv": [429, 429]}`` answers the first two requests with 429; ``"always:500"`` always fails; ``"slow"`` answers late;
    ``"garbage"`` answers 200 with an unreadable body."""

    def __init__(self, spec=None):
        self.spec = {k: (list(v) if isinstance(v, list) else v) for k, v in (spec or {}).items()}
        self._lock = threading.Lock()

    def take(self, route):
        with self._lock:
            s = self.spec.get(route)
            if isinstance(s, list) and s:
                return s.pop(0)
            if isinstance(s, str):
                return s
        return None


def _matches(record, words):
    text = (re.sub(r"<[^>]+>", "", record.title) + " " + record.work.abstract).lower()
    text += " " + _unicode(record.title).lower()
    return all(w.lower() in text for w in words)


class FakeScholar:
    def __init__(self, world=None, faults=None, delay=1.0):
        self.world = world or PlantedScholarship()
        self.faults = Faults(faults)
        self.delay = delay
        self.requests = []
        self._lock = threading.Lock()
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), self._handler())
        self.server.daemon_threads = True
        self.base = f"http://127.0.0.1:{self.server.server_address[1]}"
        self._thread = threading.Thread(target=self.server.serve_forever, daemon=True)

    def __enter__(self):
        self._thread.start()
        return self

    def __exit__(self, *exc):
        self.server.shutdown()
        self.server.server_close()

    def endpoints(self):
        return Endpoints(arxiv=self.base + "/arxiv", pubmed_search=self.base + "/pubmed/esearch", pubmed_fetch=self.base + "/pubmed/efetch", openalex=self.base + "/openalex/works")

    def _handler(self):
        outer = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def _send(self, status, body, ctype="application/json", headers=None):
                data = body if isinstance(body, bytes) else (body if isinstance(body, str) else json.dumps(body)).encode("utf-8")
                self.send_response(status)
                self.send_header("Content-Type", ctype)
                self.send_header("Content-Length", str(len(data)))
                for k, v in (headers or {}).items():
                    self.send_header(k, v)
                self.end_headers()
                self.wfile.write(data)

            def do_GET(self):
                url = urlparse(self.path)
                q = {k: v[0] for k, v in parse_qs(url.query).items()}
                route = url.path.strip("/").split("/")[0]
                with outer._lock:
                    outer.requests.append({"path": url.path, "query": q, "route": route, "ua": self.headers.get("User-Agent", ""), "t": time.time()})
                fault = outer.faults.take(route)
                if fault == "slow":
                    time.sleep(outer.delay)
                elif fault == "garbage":
                    return self._send(200, b"\x00<<not xml or json>>", "text/plain")
                elif isinstance(fault, int):
                    return self._send(fault, "Rate exceeded." if fault == 429 else "planted failure", "text/html")
                elif isinstance(fault, str) and fault.startswith("always:"):
                    return self._send(int(fault.split(":")[1]), "Rate exceeded." if fault.endswith("429") else "planted outage", "text/html")
                try:
                    return self._route(route, url, q)
                except KeyError:
                    return self._send(404, {"error": "not found"})

            def _route(self, route, url, q):
                w = outer.world
                visible = w.visible()
                if route == "arxiv":
                    recs = [r for r in visible if r.source == "arxiv"]
                    if "id_list" in q:
                        wanted = {i.split("v")[0] for i in q["id_list"].split(",")}
                        recs = [r for r in recs if r.ids["arxiv"] in wanted]
                    else:
                        sq = q.get("search_query", "")
                        cat = re.search(r"cat:([\w\.\-]+)", sq)
                        since = re.search(r"submittedDate:\[(\d{8})", sq)
                        words = [m[0] or m[1] for m in re.findall(r'all:(?:"([^"]+)"|(\S+))', sq)]
                        words = [x for ph in words for x in ph.split()]
                        recs = [r for r in recs if _matches(r, words) and (not cat or cat.group(1) in r.work.categories)
                                and (not since or r.date.replace("-", "") >= since.group(1))]
                        recs.sort(key=lambda r: (r.date, r.source_id), reverse=True)
                    recs = recs[: int(q.get("max_results", 50))]
                    body = ('<?xml version="1.0" encoding="UTF-8"?><feed xmlns:opensearch="http://a9.com/-/spec/opensearch/1.1/" xmlns:arxiv="http://arxiv.org/schemas/atom" '
                            f'xmlns="http://www.w3.org/2005/Atom"><title>arXiv Query</title><opensearch:totalResults>{len(recs)}</opensearch:totalResults>'
                            f"<opensearch:startIndex>0</opensearch:startIndex>{''.join(atom_entry(r) for r in recs)}</feed>")
                    return self._send(200, body, "application/atom+xml")
                if route == "pubmed":
                    recs = [r for r in visible if r.source == "pubmed"]
                    if url.path.endswith("/esearch"):
                        words = q.get("term", "").split()
                        recs = [r for r in recs if _matches(r, words)]
                        if q.get("mindate"):
                            lo = q["mindate"].replace("/", "-")
                            recs = [r for r in recs if r.appears >= lo]
                        recs.sort(key=lambda r: (r.date, r.source_id), reverse=True)
                        ids = [r.source_id for r in recs][: int(q.get("retmax", 50))]
                        return self._send(200, {"header": {"type": "esearch", "version": "0.3"}, "esearchresult": {"count": str(len(recs)), "retmax": str(len(ids)), "retstart": "0", "idlist": ids}})
                    wanted = set(q.get("id", "").split(","))
                    found = [r for r in recs if r.source_id in wanted]
                    body = '<?xml version="1.0" ?><PubmedArticleSet>' + "".join(pubmed_article(r) for r in found) + "</PubmedArticleSet>"
                    return self._send(200, body, "text/xml")
                if route == "openalex":
                    recs = [r for r in visible if r.source == "openalex"]
                    rest = unquote(url.path.split("/works", 1)[1])
                    if rest.startswith("/https://doi.org/"):
                        doi = rest[len("/https://doi.org/"):].lower()
                        for r in recs:
                            if (r.ids.get("doi") or "").lower() == doi:
                                return self._send(200, openalex_work(r))
                        return self._send(404, {"error": "Not Found"})
                    flt = q.get("filter", "")
                    words = []
                    m = re.search(r"title_and_abstract\.search:([^,]*)", flt)
                    if m:
                        words = m.group(1).split()
                    since = re.search(r"from_publication_date:(\d{4}-\d{2}-\d{2})", flt)
                    recs = [r for r in recs if _matches(r, words) and (not since or r.date >= since.group(1))]
                    recs.sort(key=lambda r: (r.date, r.source_id), reverse=True)
                    recs = recs[: int(q.get("per-page", 25))]
                    return self._send(200, {"meta": {"count": len(recs), "page": 1, "per_page": int(q.get("per-page", 25)), "notice": "planted"}, "results": [openalex_work(r) for r in recs]})
                raise KeyError(route)
        return Handler


def parse_all(world, records=None):
    """Every record of the world rendered in its source's format and read back through the real parser: ``{(source, id): Paper}``. (No sockets involved.)"""
    from .sources import arxiv, openalex, pubmed
    out = {}
    for r in records if records is not None else world.records:
        if r.source == "arxiv":
            feed = ('<?xml version="1.0"?><feed xmlns:opensearch="http://a9.com/-/spec/opensearch/1.1/" xmlns:arxiv="http://arxiv.org/schemas/atom" xmlns="http://www.w3.org/2005/Atom">'
                    f"<opensearch:totalResults>1</opensearch:totalResults>{atom_entry(r)}</feed>")
            paper = arxiv.parse_feed(feed.encode("utf-8"))[1][0]
        elif r.source == "pubmed":
            paper = pubmed.parse_efetch(('<PubmedArticleSet>' + pubmed_article(r) + '</PubmedArticleSet>').encode("utf-8"))[0]
        else:
            paper = openalex.parse_works({"results": [openalex_work(r)]})[0]
        out[(r.source, r.source_id)] = paper
    return out
