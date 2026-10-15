import os
import re
from datetime import datetime, timedelta, timezone

from arastirma_defteri import export
from arastirma_defteri import library as libmod
from arastirma_defteri.model import Paper

NOW = datetime(2026, 10, 15, 12, 0, tzinfo=timezone.utc)


def make_lib():
    box = [NOW - timedelta(days=20)]
    lib = libmod.Library(clock=lambda: box[0])
    lib.clock_box = box
    return lib


def add(lib, title, n, status="yeni", **kw):
    r = lib.ingest(Paper(title=title, authors=kw.pop("authors", ["John Smith"]), year=2026, date="2026-09-01", ids={"pmid": str(n)}, source="pubmed", source_id=str(n), **kw))
    if status != "yeni":
        lib.set_status(r.paper_id, status, rating=kw.get("rating"))
    return r.paper_id


def test_digest_counts_sections_and_truncation():
    lib = make_lib()
    old = add(lib, "An old paper from long ago in the inbox", 1)                         # added 20 days ago: not in this week's section
    lib.clock_box[0] = NOW - timedelta(days=2)
    for i in range(5):
        add(lib, f"A new paper number {i} about dose in radiotherapy", 10 + i)
    read = add(lib, "A paper I read this week about brachytherapy", 20)
    lib.set_status(read, "okundu", rating=4)
    lib.add_note("This changed my mind about source strength", pid=read)
    lib.add_note("Free thought about teaching dosimetry", title="Idea")
    lib.add_watch("brachy", "brachytherapy", ["pubmed"])
    lib.clock_box[0] = NOW
    text = export.digest(lib, now=NOW, days=7, top=3)
    assert text.startswith("# Research digest: 2026-10-15 (last 7 days)")
    assert "Inbox: **6** new, to read: **0**, reading: **0**, read: **1**" in text
    assert "## New in the inbox this period (5)" in text and "... and 2 more" in text and "An old paper" not in text
    assert "## Read this period (1)" in text and "A paper I read this week about brachytherapy (2026) ****" in text
    assert "## Notes written this period (2)" in text and "This changed my mind" in text and "Idea: Free thought" in text
    assert "**brachy** (pubmed): last run never" in text and old > 0


def test_digest_of_an_empty_library():
    text = export.digest(libmod.Library(), now=NOW)
    assert "Nothing new." in text and "No watches yet" in text and "Nothing." in text and "None." in text


def test_digest_is_deterministic():
    lib = make_lib()
    add(lib, "A paper about dose in radiotherapy planning", 1)
    assert export.digest(lib, now=NOW) == export.digest(lib, now=NOW)


def parse_front_matter(text):
    head = text.split("---\n")[1]
    out = {}
    for line in head.splitlines():
        key, _, value = line.partition(": ")
        if value.startswith('"') and value.endswith('"'):
            value = value[1:-1].replace('\\"', '"').replace("\\\\", "\\")
        elif value.startswith("[") and value.endswith("]"):
            inner = value[1:-1]
            value = [v.strip().strip('"') for v in re.split(r'",\s*"', inner)] if inner.startswith('"') else [v.strip() for v in inner.split(",") if v.strip()]
        out[key] = value
    return out


def test_markdown_export(tmp_path):
    lib = make_lib()
    a = add(lib, 'A "quoted" title with a \\ backslash and: colon', 1, status="okundu", abstract="The abstract text.", venue="Med Phys", authors=["José Müller", "Jane Doe"])
    lib.set_status(a, "okundu", rating=5)
    lib.tag(a, add=["Monte Carlo", "dose"])
    lib.add_note("A first note.", pid=a)
    lib.add_note("A second note.", pid=a, title="Follow-up")
    add(lib, "A paper with no notes at all in the library", 2, status="okunacak")
    add(lib, "A paper still in the inbox, not exported", 3)
    lib.add_note("A free-standing idea about phantoms", title="Phantom idea / part 2")
    paths = export.export_markdown(lib, str(tmp_path))
    names = sorted(os.path.basename(p) for p in paths)
    assert len(paths) == 3 and "muller2026quoted.md" in names and any(n.startswith("note-3-phantom-idea-part-2") for n in names) and not any("inbox" in n for n in names)
    text = (tmp_path / "muller2026quoted.md").read_text(encoding="utf-8")
    fm = parse_front_matter(text)
    assert fm["title"] == 'A "quoted" title with a \\ backslash and: colon' and fm["authors"] == ["José Müller", "Jane Doe"] and fm["status"] == "okundu" and fm["rating"] == "5"
    assert fm["tags"] == ["dose", "monte-carlo"] and fm["venue"] == "Med Phys" and fm["pmid"] == "1"
    assert "## Abstract\n\nThe abstract text." in text and "### 2026-09-25: Follow-up\n\nA second note." in text and "A first note." in text
    only = export.export_markdown(lib, str(tmp_path / "only"), only_with_notes=True)
    assert len(only) == 2                                                          # the paper with notes and the free note
    assert export.slug("Ünïcode & Spaces!! ... and a very long title " * 5).startswith("unicode-spaces-and-a-very-long-title") and len(export.slug("x" * 200)) <= 60 and export.slug("???") == "paper"
