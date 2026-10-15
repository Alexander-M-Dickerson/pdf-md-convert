from dataclasses import dataclass

from pdf_md_convert.hybrid_backend import (
    build_conversion_qa,
    referenced_equations,
    referenced_internet_appendix_tables,
    referenced_tables,
)


@dataclass(frozen=True)
class Region:
    number: str = ""
    tag: str = ""
    confidence: str = "low"
    latex: str | None = None


def test_referenced_tables_normalizes_roman_arabic_and_appendix_ids():
    markdown = "Table IV reports results. Table 1 reports summary data. Table IA.V adds robustness. Table C is prose."

    assert referenced_tables(markdown) == ["1", "IAV", "IV"]


def test_referenced_tables_ignores_bibliographic_source_note_references():
    markdown = "d Bessembinder, Maxwell and Venkataraman (2013); Table 7; Sample period: 5/11-1/13."

    assert referenced_tables(markdown) == []


def test_referenced_tables_ignores_external_possessive_and_author_citations():
    markdown = "\n".join(
        [
            "Their table IV presents estimated cost averages.",
            "The specification is copied from Table VI of Ellul et al. (2015).",
        ]
    )

    assert referenced_tables(markdown) == []


def test_referenced_tables_tracks_internet_appendix_context():
    markdown = "In Table A9 of the Internet Appendix, we report bid-ask spreads."

    assert referenced_tables(markdown) == ["A9"]
    assert referenced_internet_appendix_tables(markdown) == ["A9"]


def test_referenced_equations_expands_short_ranges():
    markdown = "Equation (4) defines costs. Equations (7)-(9) describe the model."

    assert referenced_equations(markdown) == ["4", "7", "8", "9"]


def test_build_conversion_qa_reports_missing_referenced_items():
    markdown = "Table I, Table II, and Table IA.IV report estimates. Equation (4) and Eq. (5) define moments."
    tables = [Region(number="I")]
    equations = [Region(tag="4", confidence="high", latex="$$x=1$$"), Region(tag="6")]

    qa = build_conversion_qa(markdown, tables, equations)

    assert qa["missing_referenced_tables"] == ["II"]
    assert qa["missing_internet_appendix_tables"] == ["IAIV"]
    assert qa["missing_referenced_tables_all"] == ["IAIV", "II"]
    assert qa["missing_referenced_equation_crops"] == ["5"]
    assert qa["missing_high_confidence_equation_repairs"] == ["5"]


def test_build_conversion_qa_classifies_appendix_table_references():
    markdown = "In Table A9 of the Internet Appendix, we report bid-ask spreads."

    qa = build_conversion_qa(markdown, [], [])

    assert qa["missing_referenced_tables"] == []
    assert qa["missing_internet_appendix_tables"] == ["A9"]


def test_build_conversion_qa_treats_roman_and_arabic_table_ids_as_aliases():
    markdown = "Table III reports costs. Table 4 reports capital."
    tables = [Region(number="3"), Region(number="IV")]

    qa = build_conversion_qa(markdown, tables, [])

    assert qa["missing_referenced_tables"] == []


def test_build_conversion_qa_does_not_credit_caption_only_coverage():
    markdown = "\n".join(
        [
            "Table 1 reports the sample.",
            "Table 1 \u2013 Sample distribution",
            "The results are summarized in Table 8.",
            "Table 8a",
            "Dealer spread regressions",
            "Table 8b",
            "Control portfolio regressions",
        ]
    )

    qa = build_conversion_qa(markdown, [], [])

    assert qa["missing_referenced_tables"] == ["1", "8", "8A", "8B"]
    assert qa["detected_table_captions"] == ["1", "8a", "8b"]


def test_referenced_tables_accepts_named_table_id_with_number():
    markdown = "Table Reversal 2 reports cleaned TRACE reversal filters."

    assert referenced_tables(markdown) == ["REVERSAL2"]


def test_referenced_tables_ignores_named_verb_false_positive():
    markdown = "The table includes 8 controls and Table 2 reports estimates."

    assert referenced_tables(markdown) == ["2"]


def test_referenced_tables_ignores_caption_note_prose():
    markdown = "\n".join(
        [
            "Table I. Panel A reports trading statistics for the bank and non-bank samples of dealers.",
            "Table A1. *, **, and *** denote statistical significance at conventional levels. Standard errors are clustered.",
            "The real results are summarized in Table 7.",
        ]
    )

    assert referenced_tables(markdown) == ["7"]


def test_referenced_tables_ignores_parenthetical_example_reference():
    markdown = "\n".join(
        [
            "This setup corrects for outliers in individual factors (e.g., see the value scores in Table 1),",
            "This setup corrects for outliers in individual factors (e.g., see",
            "the value scores in Table 4), after a PDF line wrap.",
            "The main calibration is reported separately (Table 3).",
            "Table 2 reports the portfolio results.",
        ]
    )

    assert referenced_tables(markdown) == ["2", "3"]
