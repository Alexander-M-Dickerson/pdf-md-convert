from collections import Counter
from pathlib import Path

import fitz

from pdf_md_convert.api import _windows_path_problem
from pdf_md_convert.equation_regions import (
    false_positive_reasons,
    find_equation_groups,
    render_crop,
    repair_equation_regions,
    tag_from_text,
)
from pdf_md_convert.hybrid_backend import (
    build_conversion_qa,
    hyphenation_vocabulary,
    normalize_fast_markdown,
)
from pdf_md_convert.layout import layout_markdown
from pdf_md_convert.math_geometry import reconstruct


def test_page_furniture_is_not_an_equation_tag():
    assert tag_from_text("running title of the paper\n179\n") is None  # head with page number
    assert tag_from_text("Journal Short Title\n3649\n") is None  # four-digit page number
    assert tag_from_text("9\n") is None  # lone footer number
    assert tag_from_text("4\nEmpirical Results\n") is None  # numbered section heading
    assert tag_from_text("1 Low\n2\n3\n9\n10 High\n") is None  # table column


def test_citation_years_and_statistics_labels_are_not_tags():
    assert tag_from_text("(2019) impose that \u0393 = 0, that the second moment") is None
    assert tag_from_text("R2\nx = 98.37%), closely followed by funds") is None
    assert tag_from_text("(R2\nx = 82.1%).") is None


def test_bare_tag_needs_display_math_or_alignment():
    assert tag_from_text("Pb = x\n20\n") == "20"
    assert tag_from_text("(20)\n") == "20"
    assert tag_from_text("20\n", allow_lone_bare=True) == "20"


def _page_with_blocks(blocks):
    doc = fitz.open(); page = doc.new_page(width=595, height=842)
    for point, text in blocks:
        page.insert_text(point, text, fontsize=10)
    return doc, page


def test_lone_number_beside_equation_is_tag_but_footer_is_not():
    doc, page = _page_with_blocks([((100, 300), "y = a + b x"), ((500, 300), "7"), ((290, 800), "12")])
    tags = [tag for tag, _, _ in find_equation_groups(page)]
    assert "7" in tags and "12" not in tags
    doc.close()


def test_lead_in_sentence_does_not_swallow_tagged_display():
    doc, page = _page_with_blocks([
        ((60, 200), "Decomposition. We write the total gain as a sum of terms in \u0394"),
        ((100, 240), "g = \u0394a + \u0394b + \u0394c,"), ((500, 240), "(9)"),
    ])
    groups = {tag: text for tag, _, text in find_equation_groups(page)}
    assert "9" in groups and "We write" not in groups["9"]
    doc.close()


def test_tagged_inequality_display_stands_alone():
    doc, page = _page_with_blocks([
        ((60, 200), "Hence, this relation captures the insight that prices are low when inventories are high."),
        ((260, 240), "m(i) < m(j)."), ((500, 240), "(2)"),
        ((60, 280), "To elaborate on the long-run effect on prices, we assume that the β = 1 case holds."),
    ])
    groups = {tag: text for tag, _, text in find_equation_groups(page)}
    assert "2" in groups and "Hence" not in groups["2"] and "elaborate" not in groups["2"]
    doc.close()


def test_numbered_equations_claim_the_crop_budget_first(tmp_path):
    pdf = tmp_path / "paper.pdf"
    doc = fitz.open()
    first = doc.new_page()
    for i in range(3):
        first.insert_text((100, 100 + 60*i), f"u{i} = a + b", fontsize=10)
    doc.new_page().insert_text((100, 100), "y = c + d x    (1)", fontsize=10)
    doc.save(pdf); doc.close()
    regions = repair_equation_regions(pdf, tmp_path / "out", [1, 2], max_crops=2, time_budget=10)
    assert [r.tag for r in regions][-1] == "1" and regions[0].page == 1  # page order kept


def test_stacked_display_with_own_tag_line_is_kept_for_review():
    stacked = "w =\nexp{b + x}\n\n1 +\n\u2211\nm\nexp{b + y}\n(2)\n"
    assert "fragmented-inline-equation" not in false_positive_reasons(stacked, "2")
    assert "fragmented-inline-equation" in false_positive_reasons("a\nb\nc =\nd\ne\nf\n", "unnumbered-1")


class _RawPage:
    """Minimal page whose encoded characters are supplied directly."""
    rect = fitz.Rect(0, 0, 595, 842)

    def __init__(self, text, placed=()):
        chars = [dict(c=ch, bbox=(100 + 6*i, 90, 105 + 6*i, 102), origin=(100 + 6*i, 100)) for i, ch in enumerate(text)]
        chars += [dict(c=ch, bbox=(x0, 87, x1, 99), origin=(x0, 97)) for ch, x0, x1 in placed]
        self.raw = {"blocks": [{"bbox": (90, 80, 400, 110), "lines": [{"dir": (1, 0), "spans": [{"size": 10, "font": "CMMI10", "chars": chars}]}]}]}

    def get_image_info(self): return []
    def get_text(self, option): return self.raw
    def get_drawings(self): return []


def test_unknown_private_glyph_blocks_high_confidence():
    rect = fitz.Rect(90, 80, 400, 110)
    assert reconstruct(_RawPage("p = d(\ue236) + s"), rect, "unnumbered-1") == (None, ["unknown-encoded-symbol"])
    value, _ = reconstruct(_RawPage("p = d + s"), rect, "unnumbered-1")
    assert value == "p = d + s"


def test_accent_glyph_attaches_to_the_letter_beneath_it():
    rect = fitz.Rect(90, 80, 400, 110)
    # "p = p + Z" with a tilde drawn over the second p (x 124-129), out of reading order.
    value, _ = reconstruct(_RawPage("p = p + Z", placed=[("\u02dc", 125, 128)]), rect, "unnumbered-1")
    assert value == r"p = \tilde{p} + Z"
    # An accent with no letter beneath it, or an unsupported mark, stays a review crop.
    assert reconstruct(_RawPage("p = p", placed=[("\u02dc", 140, 143)]), rect, "unnumbered-1")[1] == ["accent-not-reconstructed"]
    assert reconstruct(_RawPage("p = p", placed=[("\u0360", 125, 128)]), rect, "unnumbered-1")[1] == ["accent-not-reconstructed"]


def test_prose_line_with_equals_is_not_an_equation():
    rect = fitz.Rect(90, 80, 400, 110)
    line = "becomes smaller once latent factors are used, with K = 2"
    assert reconstruct(_RawPage(line), rect, "unnumbered-1") == (None, ["prose-in-equation-region"])
    assert reconstruct(_RawPage("s = 100.09 with a standard deviation of 15.4"), rect, "unnumbered-1")[0] is None
    # Regression specifications use words as variable names and stay equations.
    value, _ = reconstruct(_RawPage("Markup = b Centrality + g Log(Trade size) + e"), rect, "unnumbered-1")
    assert value == "Markup = b Centrality + g Log(Trade size) + e"
    assert reconstruct(_RawPage("&1 = ?A @F > &3"), rect, "unnumbered-1") == (None, ["unknown-encoded-symbol"])
    doc, page = _page_with_blocks([((60, 200), "to model fit performance. For a model with K = 2 factors, a stock level R2 of")])
    assert find_equation_groups(page) == []
    doc.close()


class _ScriptPage(_RawPage):
    """Glyphs given as (char, x, baseline, size)."""

    def __init__(self, glyphs, font="CMMI10"):
        chars = [dict(c=ch, bbox=(x, y - .8*s, x + .5*s, y + .2*s), origin=(x, y)) for ch, x, y, s in glyphs]
        spans = [{"size": s, "font": font, "chars": [c]} for c, (_, _, _, s) in zip(chars, glyphs)]
        self.raw = {"blocks": [{"bbox": (90, 80, 400, 110), "lines": [{"dir": (1, 0), "spans": spans}]}]}


def test_stacked_scripts_merge_into_one_group_per_base():
    # r with superscript j stacked over subscript t+1: glyphs interleave by x.
    glyphs = [("r", 100, 100, 10), ("t", 106, 103, 7), ("j", 106.5, 95, 7), ("+", 110, 103, 7),
              ("1", 114, 103, 7), (" ", 118, 100, 10), ("=", 122, 100, 10), (" ", 128, 100, 10), ("c", 132, 100, 10)]
    value, _ = reconstruct(_ScriptPage(glyphs), fitz.Rect(90, 80, 400, 110), "unnumbered-1")
    assert value == "r_{t+1}^{j}= c"  # spacing is irrelevant in math mode


def test_mismapped_and_overprinted_glyphs():
    rect = fitz.Rect(90, 80, 400, 110)
    assert reconstruct(_RawPage("R = ↵ + ✏"), rect, "unnumbered-1") == (None, ["unknown-encoded-symbol"])
    assert reconstruct(_RawPage("풀(훀) = 1"), rect, "unnumbered-1") == (None, ["unknown-encoded-symbol"])
    doubled = [("b", 100, 100, 10), ("b", 100.3, 100, 10), (" ", 106, 100, 10), ("=", 112, 100, 10),
               (" ", 118, 100, 10), ("c", 124, 100, 10), ("+", 130, 100, 10), ("d", 136, 100, 10)]
    assert reconstruct(_ScriptPage(doubled), rect, "unnumbered-1")[0] == "b = c+d"
    assert reconstruct(_RawPage("Return Portfolio=4"), rect, "unnumbered-1") == (None, ["not-a-formula"])
    # Micro sign for mu, ligatures in variable names and a quote used as a prime are fine.
    assert reconstruct(_RawPage("µ = Proﬁt + X’b"), rect, "unnumbered-1")[0] == r"\mu{} = Profit + X'b"


def test_formulas_with_lost_glyphs_or_prose_statistics_are_not_reconstructed():
    rect = fitz.Rect(90, 80, 400, 110)
    # A summation sign that was not extracted leaves limits with no base.
    lost_sum = [("r", 100, 100, 10), ("=", 106, 100, 10), ("a", 112, 100, 10), ("+", 118, 100, 10),
                ("k", 124, 103, 7), ("=", 128, 103, 7), ("1", 132, 103, 7), ("K", 126, 95, 7), ("b", 138, 100, 10)]
    assert reconstruct(_ScriptPage(lost_sum), rect, "unnumbered-1") == (None, ["script-without-base"])
    assert reconstruct(_RawPage("Total R2 = 1 ↑"), rect, "unnumbered-1") == (None, ["unknown-encoded-symbol"])
    statistics = "smaller premium 1.06 bps (t = 1.52)"
    assert reconstruct(_RawPage(statistics), rect, "unnumbered-1") == (None, ["prose-in-equation-region"])
    assert reconstruct(_RawPage("t* = 15%"), rect, "unnumbered-1")[0] == r"t* = 15\%"
    # Lowercase variable names in a definition are not prose.
    assert reconstruct(_RawPage("boll width = boll high - boll low"), rect, "unnumbered-1")[0] is not None
    # A space glyph between a letter and its subscript does not orphan the subscript.
    spaced = [("C", 100, 100, 10), (" ", 106, 100, 10), ("f", 108, 103, 7), ("=", 114, 100, 10), ("a", 120, 100, 10)]
    assert reconstruct(_ScriptPage(spaced), rect, "unnumbered-1")[0] == "C_{f}=a"


def test_display_ending_on_an_operator_is_incomplete():
    rect = fitz.Rect(90, 80, 400, 110)
    assert reconstruct(_RawPage("h = a + b -"), rect, "unnumbered-1") == (None, ["incomplete-expression"])


def test_tex_math_alphabet_fonts_are_encoded():
    from pdf_md_convert.math_geometry import encode_char
    assert encode_char("S", "MSBM10") == r"\mathbb{S}"
    assert encode_char("0", "CMBX10") == r"\boldsymbol{0}"
    assert encode_char("S", "CMMI12") == "S"
    assert encode_char("\u2206") == r"\Delta{}"
    assert encode_char("\u0177") == r"\hat{y}" and encode_char("\u0233") == r"\bar{y}"


def _document(pages):
    doc = fitz.open()
    for blocks in pages:
        page = doc.new_page(width=595, height=702)
        for point, text in blocks:
            page.insert_text(point, text, fontsize=9)
    return doc


def test_alternating_running_heads_and_page_numbers_are_removed():
    pages = []
    for index in range(8):
        number = 173 + index
        head = [((60, 62), f"{number}"), ((200, 62), "A. AUTHOR AND B. AUTHOR")] if index % 2 else \
               [((200, 62), "SHORT TITLE OF PAPER"), ((520, 62), f"{number}")]
        if index == 0:
            head = [((280, 615), "173")]
        body = [((60, 120), f"Body paragraph {index} discusses the model in detail.")]
        if index == 6:
            body.append(((60, 75), "Table 3 (continued)"))
        pages.append(head + body)
    doc = _document(pages)
    markdown, mapping = layout_markdown(doc, lambda text: text)
    assert "SHORT TITLE OF PAPER" not in markdown and "A. AUTHOR" not in markdown
    assert "\n173\n" not in markdown and "180" not in markdown
    assert "Body paragraph 7" in markdown and "Table 3 (continued)" in markdown
    doc.close()


class _BlocksPage:
    rect = fitz.Rect(0, 0, 595, 702)

    def __init__(self, blocks): self.blocks = blocks
    def get_text(self, option): return self.blocks


def test_text_drawn_twice_at_one_position_is_emitted_once():
    reference = "Obstfeld, M. (1996): Models of Currency Crises.\n"
    page = _BlocksPage([(60, 82, 540, 231, reference, 0, 0), (60, 82, 540, 231, reference, 1, 0)])
    markdown, mapping = layout_markdown([page], lambda text: text)
    assert markdown.count("Obstfeld") == 1
    assert [block["excluded_duplicate"] for block in mapping[0]["blocks"]] == [False, True]


def test_line_break_hyphens_follow_document_evidence():
    vocabulary = hyphenation_vocabulary(["rational agents are rational", "the risk-free rate", "a risk factor"])
    assert "rational" in normalize_fast_markdown("ratio-\nnal", vocabulary)
    assert "risk-free" in normalize_fast_markdown("risk-\nfree", vocabulary)
    assert "waiting" in normalize_fast_markdown("wait-\ning", vocabulary)
    assert "sub-stantial" in normalize_fast_markdown("sub-\nstantial", vocabulary)  # no evidence: keep
    assert "ratio-nal" in normalize_fast_markdown("ratio-\nnal")  # no document context


def test_unreadable_glyphs_are_visible_and_reported():
    markdown = normalize_fast_markdown("*(Page 1)*\n\nclean text\n\n*(Page 2)*\n\nLet \x0e\x08t\x03 denote \x12 > 0")
    assert "\x0e" not in markdown and markdown.count("\ufffd") == 4
    qa = build_conversion_qa(markdown, [], [])
    assert qa["damaged_text_pages"] == [2]


class _Table:
    def __init__(self, number): self.number = number


def test_appendix_tables_published_separately_are_external():
    body = "*(Page 1)*\n\nAppendix Table B.1 shows the shares. Table 1 reports results.\n\n"
    qa = build_conversion_qa(body + "Appendix A. Supplementary data\n", [_Table("1")], [])
    assert qa["missing_referenced_tables"] == [] and qa["missing_internet_appendix_tables"] == ["B1"]
    # Without that statement the appendix may be in the PDF, so the gap stays a hard finding.
    assert build_conversion_qa(body, [_Table("1")], [])["missing_referenced_tables"] == ["B1"]


def test_failed_crop_write_is_not_fatal(tmp_path):
    blocker = tmp_path / "file"; blocker.write_text("x")
    doc = fitz.open(); page = doc.new_page()
    assert render_crop(page, fitz.Rect(0, 0, 50, 50), blocker / "crop.png") is False
    doc.close()


def test_windows_path_budget_is_checked_before_conversion():
    pdf = Path("Econometrica - 2003 - Author - A Long Title for Testing.pdf")
    long_root = Path("C:/" + "x" * 120) / "_artifacts" / ("a" * 45) / ("0" * 12)
    assert "shorter --output-dir" in _windows_path_problem(long_root, pdf, Path("C:/out/a.md"))
    assert _windows_path_problem(Path("C:/out/_artifacts") / ("a" * 45) / ("0" * 12), pdf, Path("C:/out/a.md")) is None
