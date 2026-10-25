from collections import Counter

import fitz

from pdf_md_convert.layout import _block_text, layout_markdown, restore_missing_spaces

FONT = fitz.Font("tiro")
SIZE = 10


def _words(page, x, y, words, gap_share):
    """Draw each word as its own text run, separated by a gap but no space character."""
    for word in words:
        page.insert_text((x, y), word, fontname="tiro", fontsize=SIZE)
        x += FONT.text_length(word, SIZE) + gap_share * SIZE
    return x


def _page(tmp_path, draw):
    doc = fitz.open(); page = doc.new_page(width=420, height=300); draw(page)
    path = tmp_path / "synthetic.pdf"; doc.save(path)
    return fitz.open(path)


def test_narrow_word_gaps_become_spaces(tmp_path):
    # Word gaps of 0.14 em sit just below MuPDF's own space threshold, so the line is read as one token.
    doc = _page(tmp_path, lambda page: (
        _words(page, 50, 60, ["Investors", "receive", "the", "stipulated", "amount"], .14),
        page.insert_text((50, 90), "A normal sentence keeps its ordinary spaces.", fontname="tiro", fontsize=SIZE)))
    assert "Investorsreceivethestipulatedamount" in doc[0].get_text("text")
    markdown, _ = layout_markdown(doc, lambda text: text)
    assert "Investors receive the stipulated amount" in markdown
    assert "A normal sentence keeps its ordinary spaces." in markdown


def test_tight_gaps_inside_a_word_stay_closed(tmp_path):
    # "liquidation" is drawn in two runs 0.03 em apart, inside a glued line; it must not be split.
    def draw(page):
        x = _words(page, 50, 60, ["when", "a"], .14)
        x = _words(page, x, 60, ["liqui"], .03)
        _words(page, x, 60, ["dation", "event", "is", "triggered"], .14)
    markdown, _ = layout_markdown(_page(tmp_path, draw), lambda text: text)
    assert "when a liquidation event is triggered" in markdown


def test_existing_spaces_are_not_doubled(tmp_path):
    # MuPDF already separates the raised footnote marker; the rebuild must not add a second space.
    def draw(page):
        x = _words(page, 50, 60, ["Households", "refinanced", "their", "mortgages"], .14)
        page.insert_text((x + .2 * SIZE, 56), "1", fontname="tiro", fontsize=6)
    markdown, _ = layout_markdown(_page(tmp_path, draw), lambda text: text)
    assert "Households refinanced their mortgages 1" in markdown and "  " not in markdown


def _char(c, x0, x1, baseline):
    return {"c": c, "bbox": (x0, baseline - 8, x1, baseline + 2), "origin": (x0, baseline)}


def _span(chars, size=10, font="Times-Roman"):
    return {"size": size, "font": font, "chars": chars}


def _text(spans, gap_share=.13):
    return _block_text({"lines": [{"dir": (1, 0), "spans": spans}]}, gap_share)


def test_gap_rule_skips_raised_characters_and_closing_punctuation():
    # "rate" then a raised "2" 2 pt away, then "," and "and" after wide gaps.
    spans = [_span([_char("r", 0, 4, 60), _char("a", 4, 9, 60), _char("t", 9, 12, 60), _char("e", 12, 16, 60)]),
             _span([_char("2", 18, 21, 56)], size=6),
             _span([_char(",", 23, 25, 60), _char("a", 27, 32, 60), _char("n", 32, 37, 60), _char("d", 37, 42, 60)])]
    assert _text(spans, None) == "rate2,and\n"
    assert _text(spans) == "rate2, and\n"


def test_gap_rule_skips_accents_math_fonts_and_the_space_after_f():
    # "P´astor": the accent glyph sits 1.4 pt after the "P"; it is not a word break.
    assert _text([_span([_char("P", 0, 6, 60), _char("´", 7.4, 10, 60), _char("a", 7.6, 12, 60)])]) == "P´a\n"
    # Math italic letters set apart are not prose words.
    assert _text([_span([_char("π", 0, 5, 60), _char("t", 6.5, 9, 60)], font="MTMI")]) == "πt\n"
    # Some fonts leave 0.10 to 0.12 em after "f" inside a word ("flows"); real glued word gaps are 0.13 to 0.15.
    assert _text([_span([_char("f", 0, 3, 60), _char("l", 4.2, 7, 60), _char("o", 7, 12, 60)])]) == "flo\n"
    assert _text([_span([_char("f", 0, 3, 60), _char("t", 4.4, 7, 60), _char("h", 7, 12, 60)])]) == "f th\n"


def test_blocks_without_a_unique_long_run_are_left_alone(tmp_path):
    doc = _page(tmp_path, lambda page: _words(page, 50, 60, ["Investors", "receive", "the", "stipulated", "amount"], .14))
    page = doc[0]
    blocks = [b for b in page.get_text("blocks") if b[6] == 0]
    repeated = Counter({"investorsreceivethestipulatedamount": 2})  # the run also occurs elsewhere in the document
    assert restore_missing_spaces(page, blocks, repeated) == blocks
