from functools import partial
from itertools import islice, product

import fitz

from pdf_md_convert.hybrid_backend import hyphenation_vocabulary, normalize_fast_markdown, rejoin_line_break
from pdf_md_convert.layout import assemble_inline, layout_markdown, merge_broken_words

# Enough distinct filler words for a document long enough to trust a word's absence.
FILLER = " ".join("".join(letters) for letters in islice(product("bcdfgjkmpq", repeat=4), 600))


def test_broken_halves_never_vouch_for_a_spelling():
    vocabulary = hyphenation_vocabulary(["the eco-\nnomics of banks", "long-run and risk-adjusted returns"])
    assert vocabulary["eco"] == vocabulary["nomics"] == 0
    assert vocabulary["banks"] == vocabulary["long-run"] == 1
    assert vocabulary["long-"] == vocabulary["-run"] == vocabulary["-adjusted"] == 1


def test_a_half_never_printed_alone_is_a_fragment():
    vocabulary = hyphenation_vocabulary([FILLER, "the risk of a free lunch", "long-run growth and the term premium"])
    assert rejoin_line_break("eco", "nomics", vocabulary)  # neither half printed alone
    assert rejoin_line_break("the", "ory", vocabulary)  # "ory" never printed alone
    assert not rejoin_line_break("risk", "free", vocabulary)  # both halves are printed words
    assert not rejoin_line_break("long", "term", vocabulary)  # "long" starts printed compounds
    short = hyphenation_vocabulary(["the risk of a free lunch"])
    assert not rejoin_line_break("eco", "nomics", short)  # too short a document to trust absence
    assert not rejoin_line_break("Collin", "Dufresne", vocabulary)  # a capital needs the joined spelling printed


def test_a_compound_before_a_capital_loses_only_the_line_break():
    vocabulary = hyphenation_vocabulary([FILLER, "the MacKinlay test"])
    assert "Fama-French factors" in normalize_fast_markdown("Fama-\nFrench factors", vocabulary)
    assert "MacKinlay" in normalize_fast_markdown("Mac-\nKinlay", vocabulary)
    assert "Fama-French" in normalize_fast_markdown("Fama-\nFrench")  # no document context


def test_merge_broken_words_only_before_a_lowercase_continuation():
    texts = ["Banks fund the eco-\n", "nomics of lending\n", "Table 1-\n", "Panel A\n"]
    assert merge_broken_words(texts) == ["Banks fund the eco-\nnomics of lending\n", "Table 1-\n", "Panel A\n"]


def _two_block_pdf(tmp_path):
    doc = fitz.open(); page = doc.new_page(width=420, height=300)
    page.insert_text((50, 90), "Banks fund the eco-", fontname="tiro", fontsize=10)
    page.insert_text((50, 150), "nomics of lending in a long-", fontname="tiro", fontsize=10)
    page.insert_text((50, 210), "run view.", fontname="tiro", fontsize=10)
    path = tmp_path / "synthetic.pdf"; doc.save(path)
    return fitz.open(path)


def test_words_broken_across_blocks_are_rejoined(tmp_path):
    doc = _two_block_pdf(tmp_path)
    assert len(doc[0].get_text("blocks")) == 3
    normalize = partial(normalize_fast_markdown, vocabulary=hyphenation_vocabulary(["economics", "a long-run view"]))
    markdown, mapping = layout_markdown(doc, normalize)
    assert "Banks fund the economics of lending in a long-run view." in markdown
    assert len(mapping[0]["blocks"]) == 3  # the source mapping still lists every block
    inline, _ = assemble_inline(mapping, [], [], normalize)
    assert inline == markdown
