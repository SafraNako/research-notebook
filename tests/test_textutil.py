import random
import string

import pytest

from arastirma_defteri import textutil as t


@pytest.mark.parametrize("raw,expected", [
    ("https://doi.org/10.1016/J.APRADISO.2026.112946.", "10.1016/j.apradiso.2026.112946"), ("DOI:10.1088/1361-6560/AE0002", "10.1088/1361-6560/ae0002"),
    ("http://dx.doi.org/10.1002/mp.17003", "10.1002/mp.17003"), ("  10.1007/s12194-026-01151-w ", "10.1007/s12194-026-01151-w"),
    ("10.1002/(SICI)1097-0258(19980815/30)17:15/16<1661::AID-SIM968>3.0.CO;2-2", "10.1002/(sici)1097-0258(19980815/30)17:15/16<1661::aid-sim968>3.0.co;2-2"),
    ("https://doi.org/10.1000/abc%28def%29", "10.1000/abc(def)"), ("10.1000/trailing).", "10.1000/trailing")])
def test_doi_normalisation(raw, expected):
    assert t.norm_doi(raw) == expected


@pytest.mark.parametrize("raw", ["", "not a doi", "10.12/short", "https://example.org/10.1000/x", "10.48550/arXiv.2301.00001", None])
def test_things_that_are_not_dois(raw):
    assert t.norm_doi(raw) is None


@pytest.mark.parametrize("raw,expected", [
    ("arXiv:2301.00001v3", "2301.00001"), ("https://arxiv.org/abs/2301.00001v1", "2301.00001"), ("https://arxiv.org/pdf/2301.00001v2.pdf", "2301.00001"),
    ("http://arxiv.org/abs/cond-mat/0011267v1", "cond-mat/0011267"), ("hep-th/9901001", "hep-th/9901001"), ("10.48550/arXiv.2301.00001", "2301.00001"),
    ("2301.0001", "2301.0001"), ("math.GT/0309136", "math.gt/0309136")])
def test_arxiv_normalisation(raw, expected):
    assert t.norm_arxiv(raw) == expected


def test_arxiv_dois_are_arxiv_ids_not_dois():
    assert t.ids_from(doi="https://doi.org/10.48550/arXiv.2301.00001") == {"arxiv": "2301.00001"}
    assert t.ids_from(doi="10.1016/J.X.2026.1", arxiv="arXiv:2301.00001v2", pmid="PMID: 123", openalex="https://openalex.org/W42") == {
        "doi": "10.1016/j.x.2026.1", "arxiv": "2301.00001", "pmid": "123", "openalex": "W42"}
    assert t.ids_from(doi="garbage", arxiv="nope", pmid="abc", openalex="X") == {}


def test_pmid_and_openalex_ids():
    assert t.norm_pmid("https://pubmed.ncbi.nlm.nih.gov/42862461/") == "42862461" and t.norm_pmid("PMID:  17") == "17" and t.norm_pmid("12a") is None
    assert t.norm_openalex("https://openalex.org/W7221507533") == "W7221507533" and t.norm_openalex("W1") == "W1" and t.norm_openalex("nope") is None


@pytest.mark.parametrize("a,b", [
    ("Dosimetry of <sup>192</sup>Ir sources: a Monte Carlo study.", r"Dosimetry of $^{192}$Ir sources: a {M}onte {C}arlo study"),
    ("Dosimetry of <sup>192</sup>Ir sources", "Dosimetry of ¹⁹²Ir Sources"), ("Über die M{\\\"o}ller-Streuung", "Uber die Möller Streuung"),
    ("Fast Monte-Carlo, dose calculation!", "fast monte carlo dose calculation"), ("A &amp; B in <i>vivo</i>", "A & B in vivo"), ("Schr\\\"odinger's equation", "Schrödinger's equation")])
def test_titles_that_differ_only_in_formatting_normalise_alike(a, b):
    assert t.norm_title(a) == t.norm_title(b)


@pytest.mark.parametrize("a,b", [("Dose calculation. Part I: Methods", "Dose calculation. Part II: Validation"), ("ResNet-18 for CT", "ResNet-50 for CT"), ("Dose in 192Ir", "Dose in 125I")])
def test_titles_that_differ_in_substance_stay_different(a, b):
    assert t.norm_title(a) != t.norm_title(b)


def test_title_normalisation_is_idempotent_and_keeps_non_latin_scripts():
    rng = random.Random(1)
    alphabet = string.ascii_letters + string.digits + " .,:;-_()[]{}$^&%#'\"éüßøΩμ中文"
    for _ in range(300):
        s = "".join(rng.choice(alphabet) for _ in range(rng.randint(0, 60)))
        n = t.norm_title(s)
        assert t.norm_title(n) == n and n == n.strip() and "  " not in n
    assert t.norm_title("中文标题 测试") == "中文标题 测试"


@pytest.mark.parametrize("a,b", [("Smith, John A.", "John A. Smith"), ("Pieter van der Berg", "van der Berg, P."), ("José Müller", "Mueller, J."[:0] + "Jose Muller"), ("Jos\\'e M{\\\"u}ller", "José Müller"),
                                  ("Marie-Claire Dupont", "Dupont, Marie-Claire"), ("J. Smith", "Smith")])
def test_author_surnames_agree_across_spellings(a, b):
    assert t.surname_key(a) == t.surname_key(b)


def test_different_surnames_differ_and_particles_stay_with_the_surname():
    assert t.surname_key("John Smith") != t.surname_key("John Smyth")
    assert t.parse_author("Anna van der Berg") == ("van der Berg", "Anna") and t.parse_author("Berg, Anna van der") == ("Berg", "Anna van der")
    assert t.parse_author("Madonna") == ("Madonna", "") and t.first_author_key([]) == ""


def test_latex_decoding():
    assert t.latex_to_unicode(r"Schr{\"o}dinger {\'E}tienne \c{c}a \ss \o") == "Schrödinger Étienne ça ßø"          # TeX swallows the space after a control word
    assert t.latex_to_unicode(r"\textit{in vivo} \emph{dose} 5\% \& \$5 \_x \{y\}") == "in vivo dose 5% & $5 _x {y}"
    assert t.latex_to_unicode(r"Monte Carlo of {$^{192}$Ir}") == "Monte Carlo of ¹⁹²Ir"
    assert t.latex_to_unicode(r"$\mu$-CT of H$_2$O, $\alpha\times\beta$") == "μ-CT of H₂O, α×β"
    assert t.latex_to_unicode(r"C:\textbackslash{}temp") == "C:\\temp"


def test_clean_text_flattens_markup():
    assert t.clean_text("  Dose of <sup>192</sup>Ir &amp; <i>more</i>\n  text ") == "Dose of 192Ir & more text"


def test_display_authors():
    assert t.display_authors(["Mark S. Golden", "Christian Duerr", "Z W Hu", "Q X"]) == "Golden, Mark S.; Duerr, Christian; Hu, Z W et al. (4)"


def test_title_key_ignores_spacing_around_isotopes_and_markup():
    keys = {t.title_key(x) for x in ("Dose of <sup>192</sup>Ir sources", "Dose of 192 Ir sources", "Dose of ¹⁹²Ir Sources.", r"Dose of $^{192}$Ir {S}ources", "dose of 192ir sources")}
    assert len(keys) == 1
    assert t.title_key("Dose of 192Ir sources") != t.title_key("Dose of 125I sources")


@pytest.mark.parametrize("raw,expected", [
    ("Dose of <sup>192</sup>Ir and <i>in vivo</i> <b>x</b>", "Dose of 192Ir and in vivo x"),
    ("p < 0.05 and later values > 3 are significant", "p < 0.05 and later values > 3 are significant"),
    ("immediate (<6 h postoperative) and (<5 min) then > 2", "immediate (<6 h postoperative) and (<5 min) then > 2"),
    ("a<b and c>d", "a<b and c>d"), ("H<sub>2</sub>O and E=mc<sup>2</sup>", "H2O and E=mc2"),
    ('<mml:math xmlns:mml="http://www.w3.org/1998/Math/MathML"><mml:mi>x</mml:mi></mml:math> units', "x units"),
    ("&lt;i&gt;not a tag&lt;/i&gt;", "<i>not a tag</i>"), ("<span class='x'>kept text</span>", "kept text"), ("  many \n\t spaces ", "many spaces")])
def test_only_real_inline_markup_is_stripped(raw, expected):
    assert t.clean_text(raw) == expected


def test_a_less_than_sign_never_deletes_text_between_it_and_a_later_greater_than_sign():
    abstract = "Results were significant (p < 0.001) for group A. Group B had values > 5 Gy and <10% error."
    assert t.clean_text(abstract) == abstract and "group A" in t.clean_text(abstract)
