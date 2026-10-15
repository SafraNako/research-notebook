"""``arastirma``: watches over arXiv, PubMed and OpenAlex, one library with an inbox, notes, tags, search, BibTeX and a weekly digest.

Subcommand and flag tokens are ASCII-transliterated Turkish (``izle`` = watch, ``kutu`` = inbox, ``al`` = accept, ``at`` = dismiss, ``durum`` = status, ``etiket`` = tag,
``not`` = note, ``ara`` = search, ``ozet`` = digest); the output is Turkish.
"""
from __future__ import annotations

import argparse
import os
import sys
from datetime import datetime, timezone

from . import __version__, bibtex, export
from . import watch as watchmod
from .http import HttpClient, HttpError
from .library import STATUSES, Library
from .sources import Endpoints, SourceError, arxiv
from .textutil import display_authors, norm_arxiv, norm_doi, norm_pmid

DEFAULT_DB = "arastirma.db"
SHOW_NEW = 10                # how many new papers a watch run lists (the rest are in the inbox)
SOURCE_TABLE = [
    ("arxiv", "arXiv Atom API (export.arxiv.org)", "fizik ve diger alanlarda on-baski; --kategori (ornek physics.med-ph) ile daraltilir", "saniyede 1/3 istek; gecici 429 gelebilir (yeniden denenir)"),
    ("pubmed", "NCBI E-utilities (PubMed)", "biyotip: tibbi fizik dergileri dahil; baslik, ozet, yazarlar, DOI", "saniyede 3 istek (anahtarsiz); NCBI_API_KEY ile artar"),
    ("openalex", "OpenAlex works API", "genis, acik bir akademik indeks; baslik+ozet aramasi", "anahtarsiz ortak gunluk butce; OPENALEX_API_KEY ile artar"),
]


def _now(args):
    return getattr(args, "_now", None) or datetime.now(timezone.utc)


def _open(args):
    lib = Library(args.db, clock=lambda: _now(args))
    args._libs.append(lib)                                  # closed when the command ends, so the file is never left locked
    return lib


def _client(args):
    if getattr(args, "_client", None) is not None:
        return args._client
    return HttpClient(min_interval=watchmod.HOST_INTERVALS)


def _endpoints(args):
    return getattr(args, "_endpoints", None) or Endpoints()


def _keys():
    return {"openalex": os.environ.get("OPENALEX_API_KEY"), "pubmed": os.environ.get("NCBI_API_KEY")}


def _line(p):
    authors = display_authors(p["authors"], 1) or "?"
    sources = ",".join(sorted({s["source"] for s in p["sources"]})) or "elle"
    return f"#{p['id']} [{p['status']}] {p['date'] or p['year'] or 'tarihsiz'}  {p['title']}  ({authors}) {{{sources}}}"


def _ids(values):
    out = []
    for v in values:
        v = v.lstrip("#")
        if not v.isdigit():
            raise ValueError(f"'{v}' bir makale numarasi degil")
        out.append(int(v))
    return out


# --- watches ------------------------------------------------------------------------------------------------------------------------------------
def cmd_izle_ekle(args):
    lib = _open(args)
    sources = [s.strip() for s in args.kaynak.split(",") if s.strip()]
    bad = [s for s in sources if s not in watchmod.SOURCE_NAMES]
    if bad:
        print(f"Bilinmeyen kaynak: {', '.join(bad)} (secenekler: {', '.join(watchmod.SOURCE_NAMES)})", file=sys.stderr)
        return 2
    if args.kategori and not arxiv.valid_category(args.kategori):
        print(f"'{args.kategori}' gecerli bir arXiv kategorisi gibi gorunmuyor (ornek: physics.med-ph)", file=sys.stderr)
        return 2
    try:
        wid = lib.add_watch(args.isim, args.sorgu or "", sources, category=args.kategori, days=args.gun, max_results=args.en_cok)
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        return 2
    print(f"Izleme {wid} eklendi: '{args.isim}' ({', '.join(sources)}). Calistirmak icin: arastirma izle calistir")
    return 0


def cmd_izle_liste(args):
    lib = _open(args)
    ws = lib.watches()
    if not ws:
        print("Izleme yok. Eklemek icin: arastirma izle ekle ISIM \"sorgu\" --kaynak arxiv,pubmed")
        return 0
    for w in ws:
        print(f"{w['id']}  {w['name']}: '{w['query']}'{' kategori ' + w['category'] if w['category'] else ''}  kaynaklar: {', '.join(w['sources'])}  son calisma: {w['last_run'] or 'hic'}")
    return 0


def cmd_izle_sil(args):
    lib = _open(args)
    try:
        lib.remove_watch(args.numara)
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        return 2
    print(f"Izleme {args.numara} silindi (bulunan makaleler kutuphanede kalir).")
    return 0


def cmd_izle_calistir(args):
    lib = _open(args)
    if not lib.watches():
        print("Calistirilacak izleme yok.", file=sys.stderr)
        return 2
    runs = watchmod.run_all(lib, _client(args), _endpoints(args), only=args.numara, now=_now(args), keys=_keys())
    if not runs:
        print(f"Izleme {args.numara} yok.", file=sys.stderr)
        return 2
    failed = False
    for r in runs:
        print(f"\n== {r.watch['name']} ==")
        for s in r.sources:
            mark = {"ok": "tamam", "error": "HATA"}.get(s.state, s.state)
            print(f"  {s.source:9} {mark}: {s.fetched} kayit, {s.new} yeni  ({s.detail})")
            failed |= s.state == "error"
        for pid in r.new_ids[:SHOW_NEW]:
            print("  YENI " + _line(lib.get(pid)))
        if len(r.new_ids) > SHOW_NEW:
            print(f"  ... ve {len(r.new_ids) - SHOW_NEW} yeni makale daha (hepsi icin: arastirma kutu)")
        if not r.new_ids:
            print("  Yeni makale yok.")
    total = sum(len(r.new_ids) for r in runs)
    print(f"\nKutuda toplam {lib.count().get('yeni', 0)} makale var ({total} yeni). Gormek icin: arastirma kutu")
    return 1 if failed else 0


# --- the library --------------------------------------------------------------------------------------------------------------------------------
def cmd_kutu(args):
    lib = _open(args)
    papers = lib.papers(status="yeni", limit=args.en_cok, order="date")
    if not papers:
        print("Kutu bos.")
        return 0
    for p in papers:
        print(_line(p))
    print(f"\n{lib.count().get('yeni', 0)} makale kutuda. Kabul: arastirma al NUMARA...   Reddet: arastirma at NUMARA...")
    return 0


def _set_all(args, status):
    lib = _open(args)
    try:
        ids = _ids(args.numaralar)
        for pid in ids:
            lib.set_status(pid, status)
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        return 2
    print(f"{len(ids)} makale '{status}' yapildi.")
    return 0


def cmd_al(args):
    return _set_all(args, "okunacak")


def cmd_at(args):
    return _set_all(args, "atildi")


def cmd_ekle(args):
    lib = _open(args)
    given = [(k, v) for k, v in (("doi", args.doi), ("arxiv", args.arxiv), ("pmid", args.pmid)) if v]
    if len(given) != 1:
        print("--doi, --arxiv ya da --pmid'den tam birini verin.", file=sys.stderr)
        return 2
    kind, raw = given[0]
    value = {"doi": norm_doi, "arxiv": norm_arxiv, "pmid": norm_pmid}[kind](raw)
    if not value:
        print(f"'{raw}' gecerli bir {kind} degil.", file=sys.stderr)
        return 2
    try:
        paper = watchmod.fetch_one(_client(args), _endpoints(args), kind, value, email=lib.setting("email"), keys=_keys())
    except (SourceError, HttpError) as exc:
        print(f"Getirilemedi: {exc}", file=sys.stderr)
        return 1
    if paper is None:
        print(f"{kind} {value} icin kayit bulunamadi.", file=sys.stderr)
        return 1
    r = lib.ingest(paper, status="okunacak")
    print(("Eklendi: " if r.created else "Zaten kutuphanede, durum guncellendi: ") + _line(lib.get(r.paper_id)))
    return 0


def cmd_liste(args):
    lib = _open(args)
    papers = lib.papers(status=args.durum, tag=args.etiket, limit=args.en_cok, order="date")
    for p in papers:
        print(_line(p) + (f"  [{', '.join(p['tags'])}]" if p["tags"] else ""))
    if not papers:
        print("Kayit yok.")
    return 0


def cmd_goster(args):
    lib = _open(args)
    try:
        p = lib.get(_ids([args.numara])[0])
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        return 2
    if p is None:
        print(f"{args.numara} numarali makale yok.", file=sys.stderr)
        return 2
    print(f"#{p['id']} [{p['status']}]{' ' + '*' * p['rating'] if p['rating'] else ''}\n{p['title']}")
    for t in p["alt_titles"]:
        print(f"  (ayrica: {t})")
    print(f"{'; '.join(p['authors']) or 'yazar yok'}\n{p['date'] or p['year'] or 'tarihsiz'}  {p['venue']}")
    print("Kimlikler: " + (", ".join(f"{k}:{'/'.join(v)}" for k, v in p["ids"].items()) or "yok"))
    print("Kaynaklar: " + ", ".join(f"{s['source']}({s['seen_at'][:10]})" for s in p["sources"]))
    if p["tags"]:
        print("Etiketler: " + ", ".join(p["tags"]))
    if p["url"]:
        print(p["url"])
    if p["abstract"]:
        print("\n" + p["abstract"])
    for n in lib.notes(p["id"]):
        print(f"\n[not {n['id']}, {n['created_at'][:10]}{': ' + n['title'] if n['title'] else ''}]\n{n['body']}")
    return 0


def cmd_durum(args):
    lib = _open(args)
    try:
        lib.set_status(_ids([args.numara])[0], args.durum, rating=args.puan)
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        return 2
    print(f"#{args.numara} -> {args.durum}" + (f" ({args.puan} puan)" if args.puan else ""))
    return 0


def cmd_etiket(args):
    lib = _open(args)
    try:
        pid = _ids([args.numara])[0]
        lib.tag(pid, add=[t for t in (args.ekle or "").split(",") if t.strip()], remove=[t for t in (args.cikar or "").split(",") if t.strip()])
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        return 2
    print(f"#{pid} etiketleri: {', '.join(lib.get(pid)['tags']) or 'yok'}")
    return 0


def cmd_not(args):
    lib = _open(args)
    try:
        if args.serbest:
            pid, body = None, args.metin if args.metin is not None else args.numara
        else:
            if args.numara is None or args.metin is None:
                raise ValueError("makale numarasi ve not metni gerekir (bir makaleye bagli olmayan not icin --serbest)")
            pid, body = _ids([args.numara])[0], args.metin
        if body is None:
            raise ValueError("not metni gerekir")
        nid = lib.add_note(body, pid=pid, title=args.baslik or "")
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        return 2
    print(f"Not {nid} eklendi" + (f" (#{pid})" if pid else " (serbest not)") + ".")
    return 0


def cmd_notlar(args):
    lib = _open(args)
    pid = _ids([args.numara])[0] if args.numara else None
    notes = lib.notes(pid)
    for n in notes:
        print(f"[{n['id']}] {n['created_at'][:10]}{' ' + n['title'] if n['title'] else ''}: {n['body']}")
    if not notes:
        print("Not yok.")
    return 0


def cmd_not_sil(args):
    lib = _open(args)
    try:
        lib.delete_note(args.numara)
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        return 2
    print(f"Not {args.numara} silindi.")
    return 0


def cmd_ara(args):
    lib = _open(args)
    hits = lib.search(" ".join(args.sorgu), limit=args.en_cok)
    if not hits:
        print("Eslesme yok.")
        return 1
    for h in hits:
        if h["kind"] == "paper":
            p = lib.get(h["ref"])
            print(_line(p) + f"\n      {h['snippet']}")
        else:
            print(f"[serbest not {h['ref']}] {h['title']}\n      {h['snippet']}")
    return 0


def cmd_birlestir(args):
    lib = _open(args)
    try:
        lib.merge(*_ids([args.tut, args.birlestirilecek]))
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        return 2
    print(f"#{args.birlestirilecek}, #{args.tut} icine birlestirildi.")
    return 0


# --- BibTeX, notes export, digest, settings -------------------------------------------------------------------------------------------------------
def cmd_bibtex_disa(args):
    lib = _open(args)
    papers = [lib.as_paper(p["id"]) for p in lib.papers(status=args.durum, order="date") if args.durum or p["status"] != "yeni"]
    text = bibtex.dumps(papers, with_abstract=args.ozet)
    if args.cikti:
        with open(args.cikti, "w", encoding="utf-8") as f:
            f.write(text)
        print(f"{len(papers)} kayit yazildi: {args.cikti}")
    else:
        sys.stdout.write(text)
    return 0


def cmd_bibtex_ice(args):
    lib = _open(args)
    try:
        with open(args.dosya, encoding="utf-8") as f:
            papers = bibtex.loads(f.read())
    except (OSError, ValueError) as exc:
        print(f"Okunamadi: {exc}", file=sys.stderr)
        return 1
    created = 0
    for p in papers:
        created += lib.ingest(p, status=args.durum).created
    print(f"{len(papers)} kayit okundu: {created} yeni makale, {len(papers) - created} zaten kutuphanedeydi.")
    return 0


def cmd_disa_aktar(args):
    lib = _open(args)
    paths = export.export_markdown(lib, args.klasor, only_with_notes=args.sadece_notlu)
    print(f"{len(paths)} Markdown dosyasi yazildi: {args.klasor}")
    return 0


def cmd_ozet(args):
    lib = _open(args)
    text = export.digest(lib, now=_now(args), days=args.gun)
    if args.cikti:
        with open(args.cikti, "w", encoding="utf-8") as f:
            f.write(text)
        print(f"Ozet yazildi: {args.cikti}")
    else:
        sys.stdout.write(text)
    return 0


def cmd_ayar(args):
    lib = _open(args)
    if args.e_posta is not None:
        lib.set_setting("email", args.e_posta.strip())
        print("E-posta kaydedildi (yalnizca PubMed ve OpenAlex'e kibar-kullanici bilgisi olarak gonderilir).")
    print(f"e-posta: {lib.setting('email') or 'yok'}")
    return 0


def cmd_kaynaklar(args):
    print("Kaynaklar (hepsi halka acik; yalnizca arama sorgunuz gonderilir, kutuphaneniz makinenizden cikmaz):\n")
    for name, what, reads, limit in SOURCE_TABLE:
        print(f"  {name:9} {what}\n            okur: {reads}\n            sinir: {limit}")
    print("\nKutuphane tek bir SQLite dosyasidir (--db ile secilir). Yinelenen kayitlar DOI/PMID/arXiv numarasi ve baslik+ilk yazar+yil ile birlestirilir.")
    return 0


def cmd_dogrulama(args):
    from . import checks
    results = checks.run_all()
    for c in results:
        print(f"[{'GECTI' if c.passed else 'KALDI'}] {c.name}\n        {c.detail}")
    failed = [c for c in results if not c.passed]
    print(f"\n{len(results) - len(failed)}/{len(results)} dogrulama gecti.")
    return 0 if not failed else 1


def build_parser():
    p = argparse.ArgumentParser(prog="arastirma", description="Yerel literatur takipcisi ve arastirma defteri: arXiv, PubMed, OpenAlex uzerinde izlemeler, kutuphane, notlar, arama, BibTeX.")
    p.add_argument("--surum", action="version", version=f"arastirma {__version__}")
    p.add_argument("--db", default=DEFAULT_DB, help=f"SQLite kutuphane dosyasi (varsayilan: {DEFAULT_DB})")
    sub = p.add_subparsers(dest="komut", required=True)

    izle = sub.add_parser("izle", help="kayitli aramalar (izlemeler)").add_subparsers(dest="altkomut", required=True)
    s = izle.add_parser("ekle", help="yeni izleme")
    s.add_argument("isim")
    s.add_argument("sorgu", nargs="?", default="", help="arama sozcukleri (arXiv icin tirnakla 'tam ifade' kullanilabilir)")
    s.add_argument("--kaynak", default="arxiv,pubmed,openalex", help="virgulle ayrilmis: arxiv,pubmed,openalex")
    s.add_argument("--kategori", help="arXiv kategorisi, ornek physics.med-ph")
    s.add_argument("--gun", type=int, default=30, help="ilk calismada kac gun geriye bakilsin")
    s.add_argument("--en-cok", type=int, default=50, dest="en_cok", help="kaynak basina en fazla kayit")
    s.set_defaults(func=cmd_izle_ekle)
    s = izle.add_parser("liste", help="izlemeleri listele")
    s.set_defaults(func=cmd_izle_liste)
    s = izle.add_parser("sil", help="izleme sil")
    s.add_argument("numara", type=int)
    s.set_defaults(func=cmd_izle_sil)
    s = izle.add_parser("calistir", help="izlemeleri calistir, yeni makaleleri kutuya al")
    s.add_argument("numara", type=int, nargs="?")
    s.set_defaults(func=cmd_izle_calistir)

    s = sub.add_parser("kutu", help="henuz karar vermediginiz yeni makaleler")
    s.add_argument("--en-cok", type=int, default=30, dest="en_cok")
    s.set_defaults(func=cmd_kutu)
    s = sub.add_parser("al", help="kutudaki makaleleri okunacak yap")
    s.add_argument("numaralar", nargs="+")
    s.set_defaults(func=cmd_al)
    s = sub.add_parser("at", help="makaleleri at (bir daha kutuya gelmez)")
    s.add_argument("numaralar", nargs="+")
    s.set_defaults(func=cmd_at)
    s = sub.add_parser("ekle", help="tek bir makaleyi kimligiyle ekle")
    s.add_argument("--doi")
    s.add_argument("--arxiv")
    s.add_argument("--pmid")
    s.set_defaults(func=cmd_ekle)
    s = sub.add_parser("liste", help="kutuphane")
    s.add_argument("--durum", choices=STATUSES)
    s.add_argument("--etiket")
    s.add_argument("--en-cok", type=int, dest="en_cok")
    s.set_defaults(func=cmd_liste)
    s = sub.add_parser("goster", help="bir makalenin tum ayrintilari ve notlari")
    s.add_argument("numara")
    s.set_defaults(func=cmd_goster)
    s = sub.add_parser("durum", help="okuma durumu ve puan")
    s.add_argument("numara")
    s.add_argument("durum", choices=STATUSES)
    s.add_argument("--puan", type=int, help="1-5")
    s.set_defaults(func=cmd_durum)
    s = sub.add_parser("etiket", help="etiket ekle/cikar")
    s.add_argument("numara")
    s.add_argument("--ekle", help="virgulle ayrilmis")
    s.add_argument("--cikar", help="virgulle ayrilmis")
    s.set_defaults(func=cmd_etiket)
    s = sub.add_parser("not", help="not ekle")
    s.add_argument("numara", nargs="?", help="makale numarasi (--serbest ile gerekmez)")
    s.add_argument("metin", nargs="?")
    s.add_argument("--baslik")
    s.add_argument("--serbest", action="store_true", help="bir makaleye bagli olmayan not")
    s.set_defaults(func=cmd_not)
    s = sub.add_parser("notlar", help="notlari goster")
    s.add_argument("numara", nargs="?")
    s.set_defaults(func=cmd_notlar)
    s = sub.add_parser("not-sil", help="not sil")
    s.add_argument("numara", type=int)
    s.set_defaults(func=cmd_not_sil)
    s = sub.add_parser("ara", help="baslik, ozet, yazar, etiket ve notlarda tam metin arama")
    s.add_argument("sorgu", nargs="+")
    s.add_argument("--en-cok", type=int, default=20, dest="en_cok")
    s.set_defaults(func=cmd_ara)
    s = sub.add_parser("birlestir", help="iki kaydin ayni makale oldugunu elle bildir")
    s.add_argument("tut")
    s.add_argument("birlestirilecek")
    s.set_defaults(func=cmd_birlestir)

    bib = sub.add_parser("bibtex", help="BibTeX").add_subparsers(dest="altkomut", required=True)
    s = bib.add_parser("disa-aktar", help="kutuphaneyi BibTeX olarak yaz (kutudakiler haric)")
    s.add_argument("-o", "--cikti")
    s.add_argument("--durum", choices=STATUSES)
    s.add_argument("--ozet", action="store_true", help="ozetleri de yaz")
    s.set_defaults(func=cmd_bibtex_disa)
    s = bib.add_parser("ice-aktar", help="BibTeX dosyasini kutuphaneye ekle")
    s.add_argument("dosya")
    s.add_argument("--durum", choices=STATUSES, default="okunacak")
    s.set_defaults(func=cmd_bibtex_ice)

    s = sub.add_parser("disa-aktar", help="makaleleri ve notlari Markdown dosyalari olarak yaz")
    s.add_argument("--klasor", required=True)
    s.add_argument("--sadece-notlu", action="store_true", dest="sadece_notlu")
    s.set_defaults(func=cmd_disa_aktar)
    s = sub.add_parser("ozet", help="haftalik ozet (Markdown)")
    s.add_argument("--gun", type=int, default=7)
    s.add_argument("-o", "--cikti")
    s.set_defaults(func=cmd_ozet)
    s = sub.add_parser("ayar", help="ayarlar")
    s.add_argument("--e-posta", dest="e_posta", help="PubMed/OpenAlex'e kibar-kullanici olarak bildirilir (istege bagli)")
    s.set_defaults(func=cmd_ayar)
    s = sub.add_parser("kaynaklar", help="kaynaklar ve sinirlari")
    s.set_defaults(func=cmd_kaynaklar)
    s = sub.add_parser("dogrulama", help="tum dogrulama deneylerini calistir")
    s.set_defaults(func=cmd_dogrulama)
    return p


def main(argv=None, endpoints=None, client=None, now=None):
    """Run the command line. ``endpoints``, ``client`` and ``now`` replace the public services, the HTTP client and the clock (tests, private mirrors)."""
    args = build_parser().parse_args(argv)
    args._endpoints, args._client, args._now, args._libs = endpoints, client, now, []
    try:
        return args.func(args)
    finally:
        for lib in args._libs:
            lib.close()


if __name__ == "__main__":
    sys.exit(main())
