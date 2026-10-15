import os
from datetime import datetime, timezone

import pytest

from arastirma_defteri import checks, cli, fakeworld
from arastirma_defteri import http as httpmod
from arastirma_defteri import library as libmod

NOW = datetime(2026, 10, 15, 12, 0, tzinfo=timezone.utc)


@pytest.fixture
def env(tmp_path):
    world = fakeworld.PlantedScholarship()
    net = fakeworld.FakeScholar(world)
    net.__enter__()
    yield str(tmp_path / "k.db"), net, tmp_path
    net.__exit__(None, None, None)


def run(db, *args, net=None, now=NOW):
    client = httpmod.HttpClient(allow_hosts={"127.0.0.1"}, sleep=lambda s: None, retries=1)
    return cli.main(["--db", db, *args], endpoints=net.endpoints() if net else None, client=client, now=now)


def test_the_whole_workflow(env, capsys):
    db, net, tmp = env
    assert run(db, "izle", "ekle", "dozis", "dose", "--kaynak", "arxiv,pubmed,openalex", "--gun", "60", net=net) == 0
    assert "Izleme 1 eklendi" in capsys.readouterr().out
    assert run(db, "izle", "liste") == 0 and "1  dozis: 'dose'" in capsys.readouterr().out
    assert run(db, "izle", "calistir", net=net) == 0
    out = capsys.readouterr().out
    assert "== dozis ==" in out and "arxiv" in out and "YENI #" in out and "tamam" in out
    assert run(db, "kutu") == 0
    inbox = capsys.readouterr().out
    assert "[yeni]" in inbox and "makale kutuda" in inbox
    first_id = inbox.splitlines()[0].split()[0].lstrip("#")
    assert run(db, "izle", "calistir", net=net) == 0 and "Yeni makale yok." in capsys.readouterr().out                       # a second run finds nothing new
    assert run(db, "al", first_id) == 0 and "'okunacak' yapildi" in capsys.readouterr().out
    second_id = [ln for ln in inbox.splitlines() if ln.startswith("#")][1].split()[0].lstrip("#")
    assert run(db, "at", second_id) == 0
    assert run(db, "durum", first_id, "okundu", "--puan", "4") == 0 and "(4 puan)" in capsys.readouterr().out
    assert run(db, "etiket", first_id, "--ekle", "Monte Carlo,okul") == 0 and "monte-carlo, okul" in capsys.readouterr().out
    assert run(db, "not", first_id, "Kaynak siddeti kismini derste kullan", "--baslik", "ders") == 0
    assert run(db, "not", "--serbest", "Fantom deneyi icin bir fikir", "--baslik", "fikir") == 0 and "serbest not" in capsys.readouterr().out
    assert run(db, "goster", first_id) == 0
    shown = capsys.readouterr().out
    assert "[okundu] ****" in shown and "Etiketler: monte-carlo, okul" in shown and "[not 1, 2026-10-15: ders]" in shown and "Kaynaklar:" in shown
    assert run(db, "notlar", first_id) == 0 and "Kaynak siddeti" in capsys.readouterr().out
    assert run(db, "notlar") == 0 and "Fantom deneyi" in capsys.readouterr().out
    assert run(db, "ara", "kaynak", "siddeti") == 0 and f"#{first_id} " in capsys.readouterr().out
    assert run(db, "ara", "fantom") == 0 and "[serbest not" in capsys.readouterr().out
    assert run(db, "ara", "xyzzyplugh") == 1 and "Eslesme yok." in capsys.readouterr().out
    assert run(db, "liste", "--durum", "okundu") == 0 and f"#{first_id} [okundu]" in capsys.readouterr().out
    assert run(db, "liste", "--etiket", "okul") == 0 and "[monte-carlo, okul]" in capsys.readouterr().out
    assert run(db, "not-sil", "1") == 0 and run(db, "notlar", first_id) == 0 and "Not yok." in capsys.readouterr().out


def test_bibtex_markdown_and_digest(env, capsys):
    db, net, tmp = env
    run(db, "izle", "ekle", "d", "dose", "--gun", "60", net=net)
    run(db, "izle", "calistir", net=net)
    capsys.readouterr()
    run(db, "kutu", "--en-cok", "4")
    ids = [ln.split()[0].lstrip("#") for ln in capsys.readouterr().out.splitlines() if ln.startswith("#")]
    assert len(ids) == 4
    run(db, "al", *ids)
    run(db, "not", ids[0], "bu makale onemli")
    capsys.readouterr()
    bib = tmp / "refs.bib"
    assert run(db, "bibtex", "disa-aktar", "-o", str(bib)) == 0 and "4 kayit yazildi" in capsys.readouterr().out
    text = bib.read_text(encoding="utf-8")
    assert text.count("@") == 4 and "title = {{" in text
    fresh = str(tmp / "other.db")
    assert run(fresh, "bibtex", "ice-aktar", str(bib), "--durum", "okunacak") == 0 and "4 yeni makale, 0 zaten" in capsys.readouterr().out
    assert run(fresh, "bibtex", "ice-aktar", str(bib)) == 0 and "0 yeni makale, 4 zaten" in capsys.readouterr().out          # importing again adds nothing
    assert run(db, "bibtex", "disa-aktar") == 0 and capsys.readouterr().out.count("@") == 4                                     # to stdout
    notes = tmp / "notes"
    assert run(db, "disa-aktar", "--klasor", str(notes)) == 0 and "4 Markdown dosyasi" in capsys.readouterr().out
    assert len(os.listdir(notes)) == 4 and any("bu makale onemli" in (notes / f).read_text(encoding="utf-8") for f in os.listdir(notes))
    assert run(db, "ozet", "--gun", "7") == 0
    digest = capsys.readouterr().out
    assert digest.startswith("# Research digest: 2026-10-15") and "to read: **4**" in digest and "**d** (arxiv, pubmed, openalex)" in digest
    out_file = tmp / "ozet.md"
    assert run(db, "ozet", "-o", str(out_file)) == 0 and out_file.read_text(encoding="utf-8").startswith("# Research digest")


def test_adding_a_single_paper_by_identifier(env, capsys):
    db, net, _ = env
    assert run(db, "ekle", "--arxiv", "arXiv:2609.10105v2", net=net) == 0 and "Eklendi:" in capsys.readouterr().out
    assert run(db, "ekle", "--pmid", "50000105", net=net) == 0 and "Zaten kutuphanede" in capsys.readouterr().out         # the same paper by another route
    assert run(db, "ekle", "--doi", "https://doi.org/10.1002/MP.17105", net=net) == 0 and "Zaten kutuphanede" in capsys.readouterr().out
    assert run(db, "liste") == 0 and capsys.readouterr().out.count("#") == 1
    assert run(db, "ekle", "--doi", "10.9999/nothing", net=net) == 1 and "bulunamadi" in capsys.readouterr().err
    assert run(db, "ekle", "--doi", "bozuk", net=net) == 2 and run(db, "ekle", net=net) == 2 and run(db, "ekle", "--doi", "10.1/x", "--pmid", "5", net=net) == 2


def test_failures_and_bad_input_have_clear_messages_and_exit_codes(env, capsys):
    db, net, tmp = env
    assert run(db, "izle", "calistir", net=net) == 2 and "izleme yok" in capsys.readouterr().err
    assert run(db, "izle", "ekle", "x", "dose", "--kaynak", "arxiv,google", net=net) == 2 and "Bilinmeyen kaynak: google" in capsys.readouterr().err
    assert run(db, "izle", "ekle", "x", "dose", "--kategori", "physics med", net=net) == 2 and "arXiv kategorisi" in capsys.readouterr().err
    assert run(db, "izle", "ekle", "", "dose", net=net) == 2
    assert run(db, "izle", "ekle", "a", "dose", net=net) == 0 and run(db, "izle", "ekle", "a", "baska", net=net) == 2 and "already a watch named 'a'" in capsys.readouterr().err        # validation messages come from the library, in English
    net.faults.spec = {"arxiv": "always:429"}
    assert run(db, "izle", "calistir", net=net) == 1                                       # a source failed: non-zero exit, but the others delivered
    out = capsys.readouterr().out
    assert "HATA" in out and "rate limiting" in out and "tamam" in out
    assert run(db, "izle", "calistir", "9", net=net) == 2
    assert run(db, "izle", "sil", "9") == 2 and run(db, "izle", "sil", "1") == 0
    for bad in (["goster", "99"], ["goster", "abc"], ["durum", "99", "okundu"], ["al", "x"], ["etiket", "99", "--ekle", "a"], ["not", "99", "metin"], ["not-sil", "99"], ["birlestir", "998", "999"]):
        assert run(db, *bad) == 2, bad
    assert run(db, "not", "5") == 2 and run(db, "not", "--serbest") == 2


def test_settings_merge_and_sources(env, capsys):
    db, net, _ = env
    assert run(db, "ayar") == 0 and "e-posta: yok" in capsys.readouterr().out
    assert run(db, "ayar", "--e-posta", "me@example.org") == 0 and "e-posta: me@example.org" in capsys.readouterr().out
    run(db, "ekle", "--arxiv", "2609.10105", net=net)
    run(db, "ekle", "--arxiv", "2610.10402", net=net)
    capsys.readouterr()
    assert run(db, "birlestir", "1", "2") == 0 and "#2, #1 icine birlestirildi." in capsys.readouterr().out
    assert run(db, "liste") == 0 and capsys.readouterr().out.count("#") == 1
    assert run(db, "kaynaklar") == 0
    out = capsys.readouterr().out
    for name in ("arxiv", "pubmed", "openalex"):
        assert f"  {name}" in out
    assert "429" in out and "OPENALEX_API_KEY" in out


def test_validation_command_reports_all_passed(capsys):
    assert cli.main(["dogrulama"]) == 0
    assert "12/12 dogrulama gecti" in capsys.readouterr().out
    assert len(checks.ALL) == 12


def test_argument_errors_and_version(capsys):
    for bad in ([], ["izle"], ["bibtex"], ["durum", "1", "bilinmeyen"], ["liste", "--durum", "x"]):
        with pytest.raises(SystemExit):
            cli.main(bad)
    with pytest.raises(SystemExit) as e:
        cli.main(["--surum"])
    assert e.value.code == 0 and capsys.readouterr().out.startswith("arastirma 0.")


def test_the_database_is_one_file_that_persists_between_invocations(env, capsys):
    db, net, tmp = env
    run(db, "ekle", "--arxiv", "2609.10105", net=net)
    run(db, "not", "1", "kalici not")
    assert os.path.exists(db)
    lib = libmod.Library(db)
    assert len(lib.papers()) == 1 and lib.notes(1)[0]["body"] == "kalici not"
