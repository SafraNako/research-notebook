"""The weekly digest and the Markdown export of papers with their notes (one file per paper, with front matter, so the folder works in any Markdown notebook)."""
from __future__ import annotations

import re
from datetime import datetime, timedelta

from . import bibtex
from .textutil import display_authors, strip_accents


def digest(lib, now=None, days=7, top=15):
    """A Markdown digest: what is waiting in the inbox, what was read, which notes were written, how the watches did."""
    now = now or lib.clock()
    since = (now - timedelta(days=days)).strftime("%Y-%m-%dT%H:%M:%SZ")
    counts = lib.count()
    L = [f"# Research digest: {now.strftime('%Y-%m-%d')} (last {days} days)", "",
         f"- Inbox: **{counts.get('yeni', 0)}** new, to read: **{counts.get('okunacak', 0)}**, reading: **{counts.get('okunuyor', 0)}**, read: **{counts.get('okundu', 0)}**, dismissed: {counts.get('atildi', 0)}", ""]
    new = lib.papers(status="yeni", since_added=since, order="date")
    L += [f"## New in the inbox this period ({len(new)})", ""]
    for p in new[:top]:
        L.append(f"- **{p['title']}** ({p['year'] or 'n.d.'}) {display_authors(p['authors'])}; {', '.join(sorted({s['source'] for s in p['sources']})) or 'manual'} `#{p['id']}`")
    if len(new) > top:
        L.append(f"- ... and {len(new) - top} more (`arastirma kutu`)")
    if not new:
        L.append("Nothing new.")
    read = [p for p in lib.papers(status="okundu") if p["updated_at"] >= since]
    L += ["", f"## Read this period ({len(read)})", ""] + [f"- {p['title']} ({p['year'] or 'n.d.'})" + (f" {'*' * p['rating']}" if p["rating"] else "") + f" `#{p['id']}`" for p in read] + ([] if read else ["Nothing."])
    notes = [n for n in lib.db.execute("SELECT * FROM notes WHERE created_at >= ? ORDER BY id", (since,))]
    L += ["", f"## Notes written this period ({len(notes)})", ""]
    for n in notes:
        target = lib.get(n["paper_id"])["title"] if n["paper_id"] else (n["title"] or "free note")
        L.append(f"- {target}: {n['body'][:100].replace(chr(10), ' ')}{'...' if len(n['body']) > 100 else ''}")
    if not notes:
        L.append("None.")
    L += ["", "## Watches", ""]
    for w in lib.watches():
        L.append(f"- **{w['name']}** ({', '.join(w['sources'])}): last run {w['last_run'] or 'never'}")
    if not lib.watches():
        L.append("No watches yet (`arastirma izle ekle`).")
    return "\n".join(L) + "\n"


def slug(text, limit=60):
    s = re.sub(r"[^a-z0-9]+", "-", strip_accents(text).casefold()).strip("-")
    return s[:limit].rstrip("-") or "paper"


def _yaml(value):
    return '"' + str(value).replace("\\", "\\\\").replace('"', '\\"') + '"'


def export_markdown(lib, folder, only_with_notes=False):
    """Write one ``.md`` file per paper (and per free note) into ``folder``; returns the list of paths. Files are named after the citation key."""
    import os
    os.makedirs(folder, exist_ok=True)
    papers = [p for p in lib.papers() if p["status"] != "yeni"]
    keys = bibtex.keys_for([lib.as_paper(p["id"]) for p in papers])
    written = []
    for key, p in zip(keys, papers):
        notes = lib.notes(p["id"])
        if only_with_notes and not notes:
            continue
        lines = ["---", f"title: {_yaml(p['title'])}", "authors: [" + ", ".join(_yaml(a) for a in p["authors"]) + "]", f"year: {p['year'] or ''}", f"status: {p['status']}"]
        for kind in ("doi", "arxiv", "pmid"):
            if p["ids"].get(kind):
                lines.append(f"{kind}: {_yaml(p['ids'][kind][0])}")
        if p["venue"]:
            lines.append(f"venue: {_yaml(p['venue'])}")
        if p["rating"]:
            lines.append(f"rating: {p['rating']}")
        lines += ["tags: [" + ", ".join(p["tags"]) + "]", "---", "", f"# {p['title']}", ""]
        if p["abstract"]:
            lines += ["## Abstract", "", p["abstract"], ""]
        if notes:
            lines += ["## Notes", ""]
            for n in notes:
                lines += [f"### {n['created_at'][:10]}" + (f": {n['title']}" if n["title"] else ""), "", n["body"], ""]
        path = os.path.join(folder, f"{key}.md")
        with open(path, "w", encoding="utf-8") as f:
            f.write("\n".join(lines))
        written.append(path)
    for n in lib.notes(None):
        path = os.path.join(folder, f"note-{n['id']}-{slug(n['title'] or n['body'][:30])}.md")
        with open(path, "w", encoding="utf-8") as f:
            f.write("\n".join(["---", f"title: {_yaml(n['title'] or 'free note')}", f"created: {n['created_at'][:10]}", "---", "", n["body"], ""]))
        written.append(path)
    return written


__all__ = ["digest", "export_markdown", "slug", "datetime"]
