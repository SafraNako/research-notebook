"""The library: one SQLite file holding papers (merged across sources), their identifiers and sightings, reading states, tags, notes, saved searches (watches)
and a full-text index over titles, abstracts, authors, tags and notes."""
from __future__ import annotations

import functools
import json
import re
import sqlite3
import unicodedata
from dataclasses import dataclass, field
from datetime import datetime, timezone

from . import dedup
from .model import Paper
from .textutil import strip_accents, title_key

STATUSES = ["yeni", "okunacak", "okunuyor", "okundu", "atildi"]
# when two records of one paper meet, the more decided state wins: what the user chose beats what a watch found
_PRECEDENCE = {"yeni": 0, "atildi": 1, "okunacak": 2, "okunuyor": 3, "okundu": 4}

SCHEMA = """
CREATE TABLE IF NOT EXISTS papers (id INTEGER PRIMARY KEY, title TEXT NOT NULL, title_norm TEXT NOT NULL, authors TEXT NOT NULL, year INTEGER, date TEXT, venue TEXT NOT NULL DEFAULT '',
    abstract TEXT NOT NULL DEFAULT '', url TEXT NOT NULL DEFAULT '', status TEXT NOT NULL DEFAULT 'yeni', rating INTEGER, added_at TEXT NOT NULL, updated_at TEXT NOT NULL);
CREATE INDEX IF NOT EXISTS papers_title ON papers(title_norm);
CREATE INDEX IF NOT EXISTS papers_status ON papers(status);
CREATE TABLE IF NOT EXISTS alt_titles (paper_id INTEGER NOT NULL REFERENCES papers(id), title TEXT NOT NULL, PRIMARY KEY (paper_id, title));
CREATE TABLE IF NOT EXISTS paper_ids (kind TEXT NOT NULL, value TEXT NOT NULL, paper_id INTEGER NOT NULL REFERENCES papers(id), PRIMARY KEY (kind, value));
CREATE TABLE IF NOT EXISTS sightings (paper_id INTEGER NOT NULL REFERENCES papers(id), source TEXT NOT NULL, source_id TEXT NOT NULL, watch_id INTEGER, seen_at TEXT NOT NULL,
    PRIMARY KEY (paper_id, source, source_id));
CREATE TABLE IF NOT EXISTS tags (paper_id INTEGER NOT NULL REFERENCES papers(id), tag TEXT NOT NULL, PRIMARY KEY (paper_id, tag));
CREATE TABLE IF NOT EXISTS notes (id INTEGER PRIMARY KEY, paper_id INTEGER REFERENCES papers(id), title TEXT NOT NULL DEFAULT '', body TEXT NOT NULL, created_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS watches (id INTEGER PRIMARY KEY, name TEXT UNIQUE NOT NULL, query TEXT NOT NULL, sources TEXT NOT NULL, category TEXT, days INTEGER NOT NULL,
    max_results INTEGER NOT NULL, created_at TEXT NOT NULL, last_run TEXT);
CREATE TABLE IF NOT EXISTS watch_state (watch_id INTEGER NOT NULL REFERENCES watches(id), source TEXT NOT NULL, last_ok TEXT NOT NULL, PRIMARY KEY (watch_id, source));
CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS runs (id INTEGER PRIMARY KEY, watch_id INTEGER NOT NULL REFERENCES watches(id), started_at TEXT NOT NULL, summary TEXT NOT NULL);
CREATE VIRTUAL TABLE IF NOT EXISTS search USING fts5(kind UNINDEXED, ref UNINDEXED, title, abstract, authors, tags, notes, tokenize='unicode61 remove_diacritics 2');
"""


def _atomic(method):
    """Roll back whatever a failing method had begun: an implicit SQLite write transaction left open by an error path would lock every other connection to the file."""
    @functools.wraps(method)
    def wrapper(self, *args, **kwargs):
        try:
            return method(self, *args, **kwargs)
        except BaseException:
            self.db.rollback()
            raise
    return wrapper


def _index_form(text):
    """Text as it is indexed: compatibility forms (superscript digits, ligatures) made plain, so that ``192Ir`` finds ``¹⁹²Ir``; accents are folded by the tokenizer."""
    return unicodedata.normalize("NFKC", text)


@dataclass
class IngestResult:
    paper_id: int
    created: bool
    merged: list = field(default_factory=list)      # ids of existing papers that were folded into ``paper_id`` because this record bridged them
    ids_added: list = field(default_factory=list)   # identifiers this record added to an existing paper


class Library:
    def __init__(self, path=":memory:", clock=lambda: datetime.now(timezone.utc)):
        self.path, self.clock = path, clock
        self.db = sqlite3.connect(path)
        self.db.row_factory = sqlite3.Row
        self.db.executescript(SCHEMA)
        self.db.commit()

    def close(self):
        self.db.close()

    def now(self):
        return self.clock().strftime("%Y-%m-%dT%H:%M:%SZ")

    # --- reading --------------------------------------------------------------------------------------------------------------------------
    def _ids_of(self, pid):
        out = {}
        for r in self.db.execute("SELECT kind, value FROM paper_ids WHERE paper_id = ? ORDER BY kind, value", (pid,)):
            out.setdefault(r["kind"], set()).add(r["value"])
        return out

    def as_paper(self, pid):
        r = self.db.execute("SELECT * FROM papers WHERE id = ?", (pid,)).fetchone()
        if r is None:
            return None
        ids = self._ids_of(pid)
        return Paper(title=r["title"], authors=json.loads(r["authors"]), year=r["year"], date=r["date"], venue=r["venue"], abstract=r["abstract"], url=r["url"],
                     ids={k: sorted(v)[0] for k, v in ids.items()}, source="library")

    def get(self, pid):
        r = self.db.execute("SELECT * FROM papers WHERE id = ?", (pid,)).fetchone()
        if r is None:
            return None
        d = dict(r)
        d["authors"] = json.loads(d["authors"])
        d["ids"] = {k: sorted(v) for k, v in self._ids_of(pid).items()}
        d["alt_titles"] = [t["title"] for t in self.db.execute("SELECT title FROM alt_titles WHERE paper_id = ? ORDER BY title", (pid,))]
        d["tags"] = [t["tag"] for t in self.db.execute("SELECT tag FROM tags WHERE paper_id = ? ORDER BY tag", (pid,))]
        d["sources"] = [dict(s) for s in self.db.execute("SELECT source, source_id, watch_id, seen_at FROM sightings WHERE paper_id = ? ORDER BY seen_at, source", (pid,))]
        return d

    def papers(self, status=None, tag=None, since_added=None, limit=None, order="added"):
        sql, args = "SELECT p.id FROM papers p", []
        where = []
        if tag:
            sql += " JOIN tags t ON t.paper_id = p.id"
            where.append("t.tag = ?")
            args.append(tag)
        if status:
            where.append("p.status = ?")
            args.append(status)
        if since_added:
            where.append("p.added_at >= ?")
            args.append(since_added)
        if where:
            sql += " WHERE " + " AND ".join(where)
        sql += {"added": " ORDER BY p.added_at DESC, p.id DESC", "date": " ORDER BY COALESCE(p.date, '') DESC, p.id DESC"}[order]
        if limit:
            sql += f" LIMIT {int(limit)}"
        return [self.get(r["id"]) for r in self.db.execute(sql, args)]

    def find_id(self, kind, value):
        r = self.db.execute("SELECT paper_id FROM paper_ids WHERE kind = ? AND value = ?", (kind, value)).fetchone()
        return r["paper_id"] if r else None

    def count(self):
        return {r["status"]: r["n"] for r in self.db.execute("SELECT status, COUNT(*) AS n FROM papers GROUP BY status")}

    # --- ingesting records ----------------------------------------------------------------------------------------------------------------
    @_atomic
    def ingest(self, paper, watch_id=None, status="yeni"):
        """Add a record, or fold it into the paper it belongs to. A record that bridges two existing papers (shares an identifier with each) merges them."""
        cand = dedup.id_sets(paper.ids)
        strong = []
        for kind, values in cand.items():
            for v in values:
                pid = self.find_id(kind, v)
                if pid is not None and pid not in strong and dedup.strong_match(self._ids_of(pid), cand) is True:
                    strong.append(pid)
        if strong:
            home = min(strong)
            merged = []
            for other in sorted(strong):
                if other != home and not dedup.conflicts(self._ids_of(home), self._ids_of(other)):
                    self._merge(home, other)
                    merged.append(other)
            created = False
        else:
            fuzzy = []
            tn = title_key(paper.title)
            for r in self.db.execute("SELECT id FROM papers WHERE title_norm = ?", (tn,)) if tn else []:
                existing = self._ids_of(r["id"])
                if dedup.strong_match(existing, cand) is None and dedup.fuzzy_match(self.as_paper(r["id"]), paper):
                    fuzzy.append(r["id"])
            if len(fuzzy) == 1:
                home, merged, created = fuzzy[0], [], False
            else:
                home, merged, created = None, [], True
        now = self.now()
        if created:
            cur = self.db.execute("INSERT INTO papers (title, title_norm, authors, year, date, venue, abstract, url, status, added_at, updated_at) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                                  (paper.title, title_key(paper.title), json.dumps(paper.authors, ensure_ascii=False), paper.year, paper.date, paper.venue, paper.abstract, paper.url,
                                   status, now, now))
            home, added = cur.lastrowid, []
        else:
            added = self._fill(home, paper, now)
            if _PRECEDENCE[status] > _PRECEDENCE[self.db.execute("SELECT status FROM papers WHERE id = ?", (home,)).fetchone()["status"]]:
                self.db.execute("UPDATE papers SET status = ? WHERE id = ?", (status, home))
        for kind, values in cand.items():
            for v in sorted(values):
                if self.db.execute("INSERT OR IGNORE INTO paper_ids (kind, value, paper_id) VALUES (?, ?, ?)", (kind, v, home)).rowcount and not created:
                    added.append(f"{kind}:{v}")
        if paper.source:
            self.db.execute("INSERT OR IGNORE INTO sightings (paper_id, source, source_id, watch_id, seen_at) VALUES (?, ?, ?, ?, ?)", (home, paper.source, paper.source_id or "", watch_id, now))
        self.db.commit()
        self.reindex(home)
        return IngestResult(home, created, merged, added)

    def _fill(self, pid, p, now):
        """Fold a record's metadata into an existing paper: empty fields are filled, the longer abstract and author list win, the existing title and date are kept."""
        cur = self.db.execute("SELECT * FROM papers WHERE id = ?", (pid,)).fetchone()
        authors = json.loads(cur["authors"])
        self._remember_title(pid, p.title, cur["title_norm"])
        updates = {}
        if len(p.authors) > len(authors):
            updates["authors"] = json.dumps(p.authors, ensure_ascii=False)
        if len(p.abstract) > len(cur["abstract"]):
            updates["abstract"] = p.abstract
        for field_name in ("venue", "url"):
            if getattr(p, field_name) and not cur[field_name]:
                updates[field_name] = getattr(p, field_name)
        if p.year and not cur["year"]:
            updates["year"], updates["date"] = p.year, p.date
        if updates:
            updates["updated_at"] = now
            self.db.execute("UPDATE papers SET " + ", ".join(f"{k} = ?" for k in updates) + " WHERE id = ?", [*updates.values(), pid])
        return []

    def _remember_title(self, pid, title, main_norm):
        """Keep a title that differs in substance from the paper's own (not merely in formatting) as an alternate title, so the paper is found under either."""
        n = title_key(title)
        if not n or n == main_norm:
            return
        known = {title_key(r["title"]) for r in self.db.execute("SELECT title FROM alt_titles WHERE paper_id = ?", (pid,))}
        if n not in known:
            self.db.execute("INSERT OR IGNORE INTO alt_titles (paper_id, title) VALUES (?, ?)", (pid, title))

    def _merge(self, keep, drop):
        """Fold paper ``drop`` into ``keep``: identifiers, sightings, tags and notes move; the more decided state, the higher rating and the fuller metadata win."""
        a, b = self.db.execute("SELECT * FROM papers WHERE id = ?", (keep,)).fetchone(), self.db.execute("SELECT * FROM papers WHERE id = ?", (drop,)).fetchone()
        status = a["status"] if _PRECEDENCE[a["status"]] >= _PRECEDENCE[b["status"]] else b["status"]
        rating = max([r for r in (a["rating"], b["rating"]) if r is not None], default=None)
        self.db.execute("UPDATE papers SET status = ?, rating = ?, added_at = MIN(added_at, ?) WHERE id = ?", (status, rating, b["added_at"], keep))
        self._fill(keep, self.as_paper(drop), self.now())
        for r in self.db.execute("SELECT title FROM alt_titles WHERE paper_id = ?", (drop,)).fetchall():
            self._remember_title(keep, r["title"], a["title_norm"])
        self.db.execute("DELETE FROM alt_titles WHERE paper_id = ?", (drop,))
        self.db.execute("UPDATE OR IGNORE paper_ids SET paper_id = ? WHERE paper_id = ?", (keep, drop))
        self.db.execute("DELETE FROM paper_ids WHERE paper_id = ?", (drop,))
        self.db.execute("UPDATE OR IGNORE sightings SET paper_id = ? WHERE paper_id = ?", (keep, drop))
        self.db.execute("DELETE FROM sightings WHERE paper_id = ?", (drop,))
        self.db.execute("INSERT OR IGNORE INTO tags (paper_id, tag) SELECT ?, tag FROM tags WHERE paper_id = ?", (keep, drop))
        self.db.execute("DELETE FROM tags WHERE paper_id = ?", (drop,))
        self.db.execute("UPDATE notes SET paper_id = ? WHERE paper_id = ?", (keep, drop))
        self.db.execute("DELETE FROM search WHERE kind = 'paper' AND ref = ?", (drop,))
        self.db.execute("DELETE FROM papers WHERE id = ?", (drop,))
        self.db.commit()

    @_atomic
    def merge(self, keep, drop):
        """Merge two papers by hand (the user knows they are one)."""
        if keep == drop or self.get(keep) is None or self.get(drop) is None:
            raise ValueError("both papers must exist and differ")
        self._merge(keep, drop)
        self.reindex(keep)

    # --- states, tags, notes --------------------------------------------------------------------------------------------------------------
    @_atomic
    def set_status(self, pid, status, rating=None):
        if status not in STATUSES:
            raise ValueError(f"unknown status {status!r}: choose from {', '.join(STATUSES)}")
        if rating is not None and not 1 <= rating <= 5:
            raise ValueError("rating is 1 to 5")
        self._require(pid)
        self.db.execute("UPDATE papers SET status = ?, rating = COALESCE(?, rating), updated_at = ? WHERE id = ?", (status, rating, self.now(), pid))
        self.db.commit()

    @_atomic
    def tag(self, pid, add=(), remove=()):
        self._require(pid)
        for t in add:
            t = self.norm_tag(t)
            if t:
                self.db.execute("INSERT OR IGNORE INTO tags (paper_id, tag) VALUES (?, ?)", (pid, t))
        for t in remove:
            self.db.execute("DELETE FROM tags WHERE paper_id = ? AND tag = ?", (pid, self.norm_tag(t)))
        self.db.commit()
        self.reindex(pid)

    @staticmethod
    def norm_tag(tag):
        return re.sub(r"\s+", "-", tag.strip().lower().lstrip("#"))

    @_atomic
    def add_note(self, body, pid=None, title=""):
        if not body.strip():
            raise ValueError("an empty note is not stored")
        if pid is not None:
            self._require(pid)
        cur = self.db.execute("INSERT INTO notes (paper_id, title, body, created_at) VALUES (?, ?, ?, ?)", (pid, title.strip(), body.strip(), self.now()))
        self.db.commit()
        self.reindex(pid) if pid is not None else self._index_note(cur.lastrowid)
        return cur.lastrowid

    def notes(self, pid=None):
        if pid is None:
            rows = self.db.execute("SELECT * FROM notes WHERE paper_id IS NULL ORDER BY id")
        else:
            rows = self.db.execute("SELECT * FROM notes WHERE paper_id = ? ORDER BY id", (pid,))
        return [dict(r) for r in rows]

    @_atomic
    def delete_note(self, note_id):
        row = self.db.execute("SELECT paper_id FROM notes WHERE id = ?", (note_id,)).fetchone()
        if row is None:
            raise ValueError(f"no note {note_id}")
        self.db.execute("DELETE FROM notes WHERE id = ?", (note_id,))
        self.db.execute("DELETE FROM search WHERE kind = 'note' AND ref = ?", (note_id,))
        self.db.commit()
        if row["paper_id"] is not None:
            self.reindex(row["paper_id"])

    def _require(self, pid):
        if self.db.execute("SELECT 1 FROM papers WHERE id = ?", (pid,)).fetchone() is None:
            raise ValueError(f"no paper {pid}")

    # --- full-text search -----------------------------------------------------------------------------------------------------------------
    def reindex(self, pid):
        p = self.get(pid)
        self.db.execute("DELETE FROM search WHERE kind = 'paper' AND ref = ?", (pid,))
        if p is None:
            return
        notes = " ".join(n["title"] + " " + n["body"] for n in self.notes(pid))
        self.db.execute("INSERT INTO search (kind, ref, title, abstract, authors, tags, notes) VALUES ('paper', ?, ?, ?, ?, ?, ?)",
                        (pid, *map(_index_form, (" ".join([p["title"], *p["alt_titles"]]), p["abstract"], " ".join(p["authors"]), " ".join(p["tags"]), notes))))
        self.db.commit()

    def _index_note(self, note_id):
        n = self.db.execute("SELECT * FROM notes WHERE id = ?", (note_id,)).fetchone()
        self.db.execute("DELETE FROM search WHERE kind = 'note' AND ref = ?", (note_id,))
        self.db.execute("INSERT INTO search (kind, ref, title, abstract, authors, tags, notes) VALUES ('note', ?, ?, '', '', '', ?)", (note_id, _index_form(n["title"]), _index_form(n["body"])))
        self.db.commit()

    def reindex_all(self):
        self.db.execute("DELETE FROM search")
        for r in self.db.execute("SELECT id FROM papers").fetchall():
            self.reindex(r["id"])
        for r in self.db.execute("SELECT id FROM notes WHERE paper_id IS NULL").fetchall():
            self._index_note(r["id"])

    @staticmethod
    def fts_query(text):
        """Every word of the query must occur (as a word prefix) in the paper's title, abstract, authors, tags or notes; punctuation never reaches the FTS syntax."""
        words = [w for w in re.findall(r"[^\W_]+", strip_accents(text).casefold()) if w]
        return " AND ".join('"%s"*' % w for w in words)

    def search(self, text, limit=20):
        """Papers and free notes matching every word, best first (BM25 with the title weighted highest). Returns dicts with ``kind``, ``ref`` and a ``snippet``."""
        q = self.fts_query(text)
        if not q:
            return []
        rows = self.db.execute("SELECT kind, ref, title, snippet(search, -1, '[', ']', ' ... ', 12) AS snip FROM search WHERE search MATCH ? ORDER BY bm25(search, 0, 0, 10.0, 3.0, 2.0, 2.0, 4.0) LIMIT ?",
                               (q, limit)).fetchall()
        return [{"kind": r["kind"], "ref": r["ref"], "title": r["title"], "snippet": r["snip"]} for r in rows]

    # --- watches --------------------------------------------------------------------------------------------------------------------------
    @_atomic
    def add_watch(self, name, query, sources, category=None, days=30, max_results=50):
        if not name.strip() or not (query.strip() or category):
            raise ValueError("a watch needs a name and a query or a category")
        try:
            cur = self.db.execute("INSERT INTO watches (name, query, sources, category, days, max_results, created_at) VALUES (?,?,?,?,?,?,?)",
                                  (name.strip(), query.strip(), json.dumps(list(sources)), category, days, max_results, self.now()))
        except sqlite3.IntegrityError:
            raise ValueError(f"there is already a watch named {name!r}")
        self.db.commit()
        return cur.lastrowid

    def watches(self):
        out = []
        for r in self.db.execute("SELECT * FROM watches ORDER BY id"):
            d = dict(r)
            d["sources"] = json.loads(d["sources"])
            out.append(d)
        return out

    @_atomic
    def remove_watch(self, watch_id):
        if self.db.execute("SELECT 1 FROM watches WHERE id = ?", (watch_id,)).fetchone() is None:     # look before writing: no transaction is opened for a miss
            raise ValueError(f"no watch {watch_id}")
        self.db.execute("DELETE FROM watches WHERE id = ?", (watch_id,))
        self.db.execute("DELETE FROM runs WHERE watch_id = ?", (watch_id,))
        self.db.execute("DELETE FROM watch_state WHERE watch_id = ?", (watch_id,))
        self.db.commit()

    @_atomic
    def record_run(self, watch_id, summary, when):
        self.db.execute("INSERT INTO runs (watch_id, started_at, summary) VALUES (?, ?, ?)", (watch_id, when, json.dumps(summary, ensure_ascii=False)))
        self.db.execute("UPDATE watches SET last_run = ? WHERE id = ?", (when, watch_id))
        self.db.commit()

    def last_ok(self, watch_id, source):
        r = self.db.execute("SELECT last_ok FROM watch_state WHERE watch_id = ? AND source = ?", (watch_id, source)).fetchone()
        return r["last_ok"] if r else None

    @_atomic
    def mark_ok(self, watch_id, source, when):
        self.db.execute("INSERT OR REPLACE INTO watch_state (watch_id, source, last_ok) VALUES (?, ?, ?)", (watch_id, source, when))
        self.db.commit()

    def setting(self, key, default=None):
        r = self.db.execute("SELECT value FROM settings WHERE key = ?", (key,)).fetchone()
        return r["value"] if r else default

    @_atomic
    def set_setting(self, key, value):
        self.db.execute("INSERT OR REPLACE INTO settings (key, value) VALUES (?, ?)", (key, value))
        self.db.commit()

    def runs(self, watch_id=None, limit=20):
        sql, args = "SELECT * FROM runs", []
        if watch_id:
            sql += " WHERE watch_id = ?"
            args.append(watch_id)
        return [{**dict(r), "summary": json.loads(r["summary"])} for r in self.db.execute(sql + " ORDER BY id DESC LIMIT ?", (*args, limit))]
