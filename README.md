# research-notebook

A **local-first literature tracker and research notebook**, deliberately small. You save searches (*watches*) over **arXiv**, **PubMed** and **OpenAlex**; each run
puts only the papers you have not seen before into an **inbox**, whichever source they come from and however many sources list them. You accept or dismiss, read, rate,
tag and write **Markdown notes**, search everything (titles, abstracts, authors, tags, notes) with SQLite full-text search, move references in and out as **BibTeX**, and get
a weekly **digest**. The whole library is one SQLite file on your machine; only your search words ever leave it. No accounts, no server, no runtime dependencies (Python
standard library only).

```
$ arastirma izle ekle tibbi-fizik "Monte Carlo dose" --kategori physics.med-ph --gun 14
$ arastirma izle calistir
== tibbi-fizik ==
  arxiv     tamam: 3 kayit, 3 yeni  (since 2026-10-01)
  pubmed    tamam: 15 kayit, 15 yeni  (since 2026-10-01)
  openalex  tamam: 15 kayit, 14 yeni  (since 2026-10-01)
  YENI #4 [yeni] 2026-10-13  Integrating quantum chemical descriptors and Monte Carlo simulations for analytical quality assurance of antineoplastic agents ...  (Saleh, Abdelmoneim et al. (4)) {openalex,pubmed}
  ...
Kutuda toplam 32 makale var (32 yeni).
```

*(the output of a live session; `{openalex,pubmed}` is one paper that both sources listed, merged into one inbox entry)*

## Quick start

```bash
pip install -e ".[dev]"                     # Python >= 3.9, no runtime dependencies

arastirma izle ekle brachy "brachytherapy dosimetry" --kaynak pubmed,openalex --gun 30
arastirma izle ekle medfiz "Monte Carlo dose" --kategori physics.med-ph      # arXiv category; all three sources by default
arastirma izle calistir                      # run all watches; new papers go to the inbox
arastirma kutu                               # the inbox
arastirma al 4 7 23                          # accept: they become "okunacak" (to read)
arastirma at 16 30                           # dismiss: they never come back
arastirma durum 4 okundu --puan 4            # reading state and rating
arastirma etiket 4 --ekle "monte-carlo,kalite-guvence"
arastirma not 4 "Doz belirsizligi icin derste ornek olabilir" --baslik "ders fikri"
arastirma not --serbest "Aylik tarama: brachy + proton izlemeleri" --baslik "yapilacak"       # a note not tied to a paper
arastirma ara monte carlo gama               # full-text search over everything, notes included
arastirma ekle --doi 10.1007/s00894-026-07013-x       # add one paper by DOI, arXiv id or PubMed id
arastirma bibtex disa-aktar -o refs.bib      # papers you have decided on, as BibTeX
arastirma bibtex ice-aktar mine.bib          # and BibTeX in
arastirma disa-aktar --klasor notes/         # one Markdown file per paper, with front matter and your notes
arastirma ozet                               # the weekly digest
arastirma dogrulama                          # 12 validation experiments, offline
pytest                                       # 186 tests, offline
```

Command-line tokens are ASCII-transliterated Turkish (`izle` = watch, `kutu` = inbox, `al` = accept, `at` = dismiss, `durum` = status, `etiket` = tag, `not` = note,
`ara` = search, `ozet` = digest) and the output is Turkish; the code, the tests and this README are English. The library defaults to `./arastirma.db` (`--db FILE`).

| command | what it does |
|---|---|
| `izle ekle ISIM "sorgu" [--kaynak arxiv,pubmed,openalex] [--kategori physics.med-ph] [--gun 30] [--en-cok 50]` | a saved search |
| `izle liste` / `izle sil N` / `izle calistir [N]` | list, delete, run (exit code 1 if any source failed) |
| `kutu` / `al N...` / `at N...` | inbox, accept, dismiss |
| `liste [--durum D] [--etiket T]` / `goster N` / `durum N D [--puan 1-5]` / `etiket N --ekle a,b --cikar c` | the library |
| `not N "metin" [--baslik B]` / `not --serbest "metin"` / `notlar [N]` / `not-sil N` | notes |
| `ara sozcukler...` | full-text search: every word must occur (as a word prefix); accents and case are ignored |
| `birlestir N M` | tell it two entries are one paper |
| `bibtex disa-aktar` / `bibtex ice-aktar F` / `disa-aktar --klasor D` / `ozet` / `ayar --e-posta ADDR` / `kaynaklar` | exchange, digest, settings |

States: `yeni` (inbox), `okunacak`, `okunuyor`, `okundu`, `atildi` (dismissed).

## What it does for you that a plain search does not

* **One paper, however many sources list it.** arXiv, PubMed and OpenAlex write the same paper differently (a preprint and its journal version, an arXiv DOI, `<sup>192</sup>Ir`
  against `¹⁹²Ir` against `192 Ir`, `Smith, J.` against `John A. Smith`). Records are merged by the rules below, so the inbox shows each paper once, and a journal version
  of a preprint you already have does not arrive as "new".
* **A window per source.** A watch remembers the last successful run *of each source*. If arXiv throttles you (it does, with HTTP 429), arXiv's window stays open and the
  next run asks it again from where it last succeeded, while the others move on. Windows overlap by two days (indexes lag), which is harmless because a known paper is never
  new twice.
* **What you decided survives.** A dismissed paper never returns to the inbox, even if another source lists it later; a paper you read keeps its notes, tags and rating when
  records are merged.
* **Found under either title.** If merged records carry titles that differ in substance, the paper keeps both, and search finds it under each.

### The identity rules

1. A shared **DOI** or **PubMed id** means the same paper; a shared **OpenAlex id** means the same record; a shared **arXiv id** too, unless both records carry different DOIs.
2. Identifiers that **conflict** (both have a DOI, a PubMed id or an arXiv id and none is shared) mean different papers, however alike the titles.
3. Otherwise (a preprint against its journal version), the records are one paper only if the titles are **identical after normalisation** (markup, LaTeX, accents, case,
   punctuation and spacing removed), the titles are at least **four words**, the years are **at most one apart**, and the **first authors' surnames agree**.
4. A record that fits two different papers equally well is **kept separate** rather than guessed. A record that shares an identifier with each of two papers merges them.

The rules are conservative on purpose: a false merge puts two papers under one set of notes, a false split only shows a duplicate you can merge by hand (`birlestir`).

## Sources

| source | what it reads | limits |
|---|---|---|
| arXiv (Atom API) | preprints; `--kategori physics.med-ph` narrows to a category | at most one request per 3 seconds (the client enforces it); HTTP 429 comes and goes and is retried with back-off |
| PubMed (NCBI E-utilities) | biomedicine, including the medical physics journals: title, abstract, authors, DOI | three requests per second without a key (`NCBI_API_KEY` raises it); records added since the window start |
| OpenAlex | a broad open index of scholarly works; title-and-abstract search | without a key requests share a small daily budget per IP address (`OPENALEX_API_KEY` raises it) |

`arastirma ayar --e-posta you@example.org` (optional) is passed to PubMed and OpenAlex as the polite-pool contact and nowhere else. Watch queries are plain keyword searches:
the sources stem words, so "dose" also finds "dosing", and some results will be off-topic. That is what the inbox is for.

## How it is validated

Nothing is checked only against itself. The program ships a **planted-truth world**: a real local HTTP server that speaks the formats of arXiv (Atom), PubMed (esearch JSON,
efetch XML) and OpenAlex (JSON) and serves a made-up literature of 43 works in 70 records, each written the way its source writes it, including the cases that make duplicate
resolution hard. It also ships a **cassette**: the real responses of a live session on 2026-10-15 (two medical-physics watches over the three sources, three papers added by
identifier), replayed with no network. `arastirma dogrulama` runs 12 experiments and `pytest` 186 tests, all offline.

| what | checked against | result |
|---|---|---|
| parsers | every planted record rendered in its source's format (LaTeX titles, `<sup>` markup, season dates, inverted-index abstracts, upper-case DOI URLs) and read back; plus genuine responses | title, date, authors and identifiers recovered for all 70 records |
| duplicate resolution | the planted truth: 43 works in 44 papers (one declared known miss) | recovered exactly; **0 false merges**; identical result in 60 shuffled arrival orders of the standalone clusterer and 25 of the real library |
| traps | Part I vs Part II; a generic title ("Introduction"); same title and author years apart; same title and first author with different DOIs; a paper and its correction; ResNet-18 vs ResNet-50 | all kept apart; the 10 multi-record works resolved to one paper each |
| watches | a world that evolves day by day | the inbox is exactly the papers behind the records a source could return; a repeat run finds nothing new; journal versions of known preprints are merged; dismissed and library papers never return |
| failures | arXiv throttled for good, throttled twice, unreadable PubMed, failing OpenAlex, a slow site | each is a status; the other sources deliver; the failed source keeps its window; back-off 2 s, 4 s |
| full-text search | an independent brute-force scan of the stored data, 250 random queries (prefixes, accents, superscripts), notes and tags | identical results; hostile query strings never reach the FTS syntax |
| BibTeX | 400 random papers with LaTeX specials, accents, particles and CJK; a real-world `.bib` (`@string`, `@comment`, `#` concatenation, nested braces, corporate authors) | all round-trip unchanged; keys unique |
| real recorded session | 78 real records from PubMed, OpenAlex and arXiv | 68 papers; 10 PubMed/OpenAlex pairs merged by DOI, and **OpenAlex's own PubMed id agrees for 10 of 10**; no two papers remain that the rules would merge |
| the HTTP client | a real local server: retries with exponential back-off, `Retry-After`, timeouts, size cap, per-host interval, allow-list | as specified |

### What the validation caught during development

* **Stripping "tags" deleted text.** The first `clean_text` removed everything matching `<...>`, so an abstract containing both `p < 0.001` and a later `>` would have lost the text
  in between. Found while investigating a failed assertion about real abstracts; only real inline markup (`<sup>`, `<i>`, MathML, ...) is removed now, and `p < 0.001` and
  `(<6 h)` survive.
* **Real titles spell isotopes three ways** (`¹⁹²Ir`, `192Ir`, `192 Ir`); comparing normalised titles missed the spaced one. Titles are now compared with spacing removed.
* **Merging threw a title away.** After merging two records the second record's title was lost, so a paper could not be found by the title it had in the other source (caught by a
  test); papers now keep alternate titles. Search also failed to match `192ir` against `¹⁹²Ir` until the index was normalised.
* **An oversized response was retried with back-off** for about 14 seconds although retrying can never help (found because a test was slow); permanent failures are not retried.
* **The LaTeX decoder** dropped `\TeX`, kept `$^{192}$` raw, and could eat a literal backslash recovered from `\textbackslash{}`; BibTeX export re-ordered a corporate author
  (`{The ATLAS Collaboration}` became "Collaboration, The ATLAS"). Authors are now written as stored.
* **Error paths left the database locked.** A duplicate watch, or deleting a watch that does not exist, left an implicit SQLite write transaction open; the next command (a new
  connection) then failed with "database is locked". It passed on Python 3.9 and 3.12, where the abandoned connection happened to be garbage-collected in time, and failed on 3.13: found
  by CI, not by me. Every mutating operation now rolls back on failure, misses are detected before anything is written, the command line closes the library when a command ends,
  and a test proves a second connection can always write after any failed operation (it fails on the old code with exactly the CI error).
* arXiv returned HTTP 429 to the very first live probes, which is why throttling is a first-class case (per-source windows, retry with back-off, a status that says so).
* My own tests were wrong several times (a title I thought was short had seven words; a regex test that assumed no real abstract contains `<`; a world whose truth missed a hostname).
  The program was right each time.

## Limitations

* **The known miss.** If a paper's *title changes* between preprint and journal and no identifier links them, they stay two entries (the planted world contains exactly this case and
  the tests assert it). The same goes for a preprint and a journal version more than a year apart. Use `birlestir`.
* **Keyword search, not curation.** Results depend on what each source's search returns; there is no relevance model, no citation graph, no PDF or full-text handling, no author
  disambiguation, no sync between machines, and it is one user's notebook.
* PubMed's `edat` (entry date) drives its window and OpenAlex's publication date drives its own, so a paper can reach the inbox from one source days before the other lists it.
* BibTeX export writes UTF-8 (use `biber`/`xelatex`, or `inputenc`), keeps titles case-protected with an extra pair of braces, and does not emit `@string` or `crossref`.
* The real-data validation covers one recorded session; the planted world covers the structure it was built with, not every way scholarly metadata can be written.

## Project layout

```
src/arastirma_defteri/
    textutil.py dedup.py library.py              normalisation, identity rules, the SQLite library with full-text search
    sources/ arxiv.py pubmed.py openalex.py      the three parsers and clients
    watch.py bibtex.py export.py                 saved searches and their windows, BibTeX, digest and Markdown export
    http.py cli.py checks.py                     polite HTTP client, command line, the validation battery
    fakeworld.py cassette.py                     the planted-truth sources, record/replay of real sessions
    data/medphys.cassette.json.gz                the recorded real session
tests/  tools/record_cassette.py  tools/make_examples.py  examples/digest.md  examples/library.bib
```

CI (GitHub Actions): Python 3.9, 3.12 and 3.13, fully offline.

## License

MIT
