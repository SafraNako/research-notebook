"""Regenerate the example digest and BibTeX file from the recorded real session (no network):  python tools/make_examples.py"""
import sys
from datetime import datetime, timezone
from pathlib import Path

root = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(root / "src"))

from arastirma_defteri import bibtex, cassette, export, watch  # noqa: E402
from arastirma_defteri.library import Library  # noqa: E402
from arastirma_defteri.sources import Endpoints  # noqa: E402

meta, tape = cassette.load(root / "src" / "arastirma_defteri" / "data" / "medphys.cassette.json.gz")
now = datetime.strptime(meta["recorded_at"], "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
lib = Library(":memory:", clock=lambda: now)
lib.add_watch("medphys-mc", "Monte Carlo dose", ["arxiv", "pubmed", "openalex"], category="physics.med-ph", days=21, max_results=25)
lib.add_watch("brachy", "brachytherapy dosimetry", ["pubmed", "openalex"], days=30, max_results=25)
watch.run_all(lib, cassette.ReplayClient(tape, retries=0), Endpoints(), now=now)
out = root / "examples"
(out / "digest.md").write_text(export.digest(lib, now=now, days=7), encoding="utf-8")
(out / "library.bib").write_text(bibtex.dumps([lib.as_paper(p["id"]) for p in lib.papers(order="date")]), encoding="utf-8")
print("written:", ", ".join(p.name for p in sorted(out.iterdir())), f"({len(lib.papers())} papers)")
