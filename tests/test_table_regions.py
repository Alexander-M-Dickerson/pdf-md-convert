from pdf_md_convert.table_regions import (
    TableRegion,
    actual_table_caption_lines,
    group_word_rows,
    is_header_row,
    reconstruct_list_table,
    reconstruct_coordinate_text,
    row_text,
    split_prose_table_rows,
    table_repair_appendix,
    markdown_table_to_tsv,
    trim_embedded_prose_tail,
    trim_trailing_notes,
)


def test_actual_table_caption_lines_ignores_inline_reference():
    text = "Table VII illustrates the sensitivity.\n\nTable VIII\nModel-Implied Persistence\nThis table reports results."

    assert actual_table_caption_lines(text) == [("VIII", "Model-Implied Persistence")]


def test_actual_table_caption_lines_ignores_here_placeholder():
    text = "Table I\nhere\nElectronic copy available at: https://ssrn.com/abstract=1"

    assert actual_table_caption_lines(text) == []


def test_actual_table_caption_lines_accepts_titled_same_line_captions():
    text = "\n".join(
        [
            "Table 1: Descriptive Statistics for Traded Instruments",
            "Table IV. Capital Commitment Summary Statistics",
            "INSERT TABLE 5 ABOUT HERE",
            "Table VII illustrates the sensitivity of the model fit.",
        ]
    )

    assert actual_table_caption_lines(text) == [
        ("1", "Descriptive Statistics for Traded Instruments"),
        ("IV", "Capital Commitment Summary Statistics"),
    ]


def test_actual_table_caption_lines_accepts_dash_spacing_and_letter_suffixes():
    text = "\n".join(
        [
            "Table 1 \u2013 Distribution of bond characteristics",
            "Table 2  Trading Activity of MBS SCPs",
            "Table 8a",
            "Dealer spread regressions",
        ]
    )

    assert actual_table_caption_lines(text) == [
        ("1", "Distribution of bond characteristics"),
        ("2", "Trading Activity of MBS SCPs"),
        ("8a", "Dealer spread regressions"),
    ]


def test_actual_table_caption_lines_accepts_long_standalone_suffix_title():
    text = "\n".join(
        [
            "Table 8a",
            "Dealer round-trip (DRT) spread estimate regressions for 90 disseminated bonds and nondisseminated matchers",
        ]
    )

    assert actual_table_caption_lines(text) == [
        ("8a", "Dealer round-trip (DRT) spread estimate regressions for 90 disseminated bonds and nondisseminated matchers")
    ]


def test_actual_table_caption_lines_rejects_note_prose_after_table_reference():
    text = "\n".join(
        [
            "Table VI. Capital Commitment Summary Statistics: Bank vs. Non-Bank Dealers",
            "Table I. Panel A reports trading statistics for the bank and non-bank samples of dealers.",
            "Table A1. *, **, and *** denote statistical significance at the 0.10, 0.05, and 0.01 level, respectively.",
        ]
    )

    assert actual_table_caption_lines(text) == [
        ("VI", "Capital Commitment Summary Statistics: Bank vs. Non-Bank Dealers")
    ]


def test_actual_table_caption_lines_accepts_appendix_and_standalone_period():
    text = "\n".join(
        [
            "Electronic copy available at: https://ssrn.com/abstract=1",
            "Appendix Table A2",
            "Stability of trading activity deciles",
            "Table 1.",
            "Trading costs during the COVID-19 crisis.",
            "Table II, Panel A",
            "Transaction Cost Regression coefficients",
        ]
    )

    assert actual_table_caption_lines(text) == [
        ("A2", "Stability of trading activity deciles"),
        ("1", "Trading costs during the COVID-19 crisis."),
        ("II", "Transaction Cost Regression coefficients"),
    ]


def test_actual_table_caption_lines_accepts_short_sample_title_lines():
    text = "\n".join(
        [
            "Table I",
            "Sample Construction",
            "Aggregate Market Sample",
            "# Cusips",
            "# Trades",
        ]
    )

    assert actual_table_caption_lines(text) == [("I", "Sample Construction Aggregate Market Sample")]


def test_group_word_rows_groups_by_y_coordinate():
    words = [
        (10, 10, 20, 15, "A"),
        (30, 10.5, 40, 15, "B"),
        (10, 25, 20, 30, "C"),
    ]

    rows = group_word_rows(words)

    assert [row_text(row) for row in rows] == ["A B", "C"]


def test_is_header_row_requires_actual_aggregate_market_header():
    assert not is_header_row("The sample period is January 2006 to October 2016. The Aggregate Market sample that")
    assert not is_header_row("trading volume each year. To construct the Constant Dealer sample, we select dealers")
    assert is_header_row("Aggregate Market Sample # Cusips # Trades")
    assert is_header_row("Year (Billions) (Billions)")


def test_reconstruct_coordinate_text_clusters_numeric_columns():
    words = [
        (50, 10, 75, 15, "Variable"),
        (210, 10, 225, 15, "(1)"),
        (270, 10, 285, 15, "(2)"),
        (330, 10, 345, 15, "(3)"),
        (50, 25, 92, 30, "Intercept"),
        (210, 25, 235, 30, "0.12"),
        (270, 25, 295, 30, "0.10"),
        (330, 25, 355, 30, "0.08"),
        (50, 40, 90, 45, "Liquidity"),
        (210, 40, 240, 45, "-0.31"),
        (270, 40, 300, 45, "-0.28"),
        (330, 40, 360, 45, "-0.22"),
        (50, 55, 82, 60, "Rating"),
        (210, 55, 235, 60, "0.04"),
        (270, 55, 295, 60, "0.05"),
        (330, 55, 355, 60, "0.06"),
        (50, 70, 110, 75, "Observations"),
        (210, 70, 238, 75, "1,200"),
        (270, 70, 298, 75, "1,190"),
        (330, 70, 358, 75, "1,185"),
    ]

    extracted, confidence, reasons = reconstruct_coordinate_text(words, caption_start_y=-10)

    assert confidence == "medium"
    assert "x-coordinate-column-clustering" in reasons
    assert "| row          | (1)   | (2)   | (3)   |" in extracted
    assert "| Liquidity    | -0.31 | -0.28 | -0.22 |" in extracted


def test_reconstruct_coordinate_text_keeps_definition_tables_as_low_confidence_text():
    words = [
        (50, 10, 120, 15, "Variable"),
        (50, 25, 95, 30, "Spread"),
        (140, 25, 300, 30, "Customer bid-ask spread in percent"),
        (50, 40, 95, 45, "Chain"),
        (140, 40, 300, 45, "Number of dealers in the chain"),
        (50, 55, 110, 60, "Centrality"),
        (140, 55, 330, 60, "Eigenvector centrality indicator"),
    ]

    extracted, confidence, reasons = reconstruct_coordinate_text(words, caption_start_y=-10)

    assert confidence == "low"
    assert "text-table-coordinate-fallback" in reasons
    assert "Customer bid-ask spread in percent" in extracted


def test_table_repair_appendix_groups_continuation_tables():
    regions = [
        TableRegion(
            page=10,
            number="11",
            title="Liquidity regressions",
            bbox=[0, 0, 10, 10],
            crop_path="p010.png",
            confidence="medium",
            reasons=["x-coordinate-column-clustering"],
            extracted_text="| row | c1 |\n| --- | -- |\n| A | 1 |",
            note_text="First note.",
            tsv_path="p010.tsv",
            markdown=None,
        ),
        TableRegion(
            page=11,
            number="11",
            title="continued.",
            bbox=[0, 0, 10, 10],
            crop_path="p011.png",
            confidence="low",
            reasons=["text-table-coordinate-fallback"],
            extracted_text="continued rows",
            note_text="Second note.",
            tsv_path="p011.tsv",
            markdown=None,
        ),
    ]

    appendix = table_repair_appendix(regions)

    assert appendix.count("### Table 11.") == 1
    assert "#### Part 1: page 10" in appendix
    assert "#### Part 2: page 11" in appendix
    assert "Second note." in appendix
    assert "p011.tsv" in appendix


def test_trim_trailing_notes_ignores_page_numbers_and_section_headings():
    rows = [
        ["Observations", "100"],
        ["33", ""],
        ["3.2 Dealer P&L", ""],
        ["Standard errors are reported in parentheses and * indicates significance.", ""],
    ]

    kept, notes = trim_trailing_notes(rows)

    assert kept == [["Observations", "100"]]
    assert notes == ["Standard errors are reported in parentheses and * indicates significance."]


def test_markdown_table_to_tsv_preserves_cells():
    table = "| row | c1 | c2 |\n| --- | -- | -- |\n| A | 1.0 | (2.1) |\n"

    assert markdown_table_to_tsv(table) == "row\tc1\tc2\nA\t1.0\t(2.1)\n"


def test_reconstruct_list_table_handles_text_columns_without_numeric_anchors():
    words = [
        (220, 10, 270, 15, "ABN"),
        (275, 10, 320, 15, "Amro"),
        (455, 10, 505, 15, "Bank"),
        (510, 10, 525, 15, "of"),
        (530, 10, 590, 15, "America"),
        (715, 10, 765, 15, "Barclays"),
        (220, 25, 275, 30, "Citigroup"),
        (455, 25, 505, 30, "Credit"),
        (510, 25, 560, 30, "Suisse"),
        (715, 25, 760, 30, "Daiwa"),
        (220, 40, 290, 45, "Jefferies"),
        (455, 40, 485, 45, "JP"),
        (490, 40, 555, 45, "Morgan"),
        (560, 40, 610, 45, "Chase"),
        (715, 40, 775, 45, "Mizuho"),
    ]

    table, confidence, reasons = reconstruct_list_table(group_word_rows(words))

    assert confidence == "medium"
    assert "list-table-column-clustering" in reasons
    assert "Bank of America" in table
    assert "JP Morgan Chase" in table


def test_split_prose_table_rows_removes_zero_data_caption_text():
    rows = [
        ["Column (1) reports the reduced form and all specifications include fixed effects.", "", ""],
        ["Equation (1) defines the dealer inventory target in the surrounding body text.", "", ""],
        ["Liquidity", "-0.31", "-0.28"],
    ]

    kept, notes, removed = split_prose_table_rows(rows)

    assert kept == [["Liquidity", "-0.31", "-0.28"]]
    assert notes
    assert removed == 2


def test_trim_embedded_prose_tail_stops_after_table_flows_into_body_text():
    rows = [
        ["Day -2", "48.6", "24.2"],
        ["Day -1", "65.2", "24.6"],
        ["Day 0", "128.4", "26.0"],
        ["Observations", "569", "569"],
        ["we follow Madhavan and Smidt, who derive the following equation", "", ""],
        ["It - It-1 = beta x (It-1 - I*) + epsilon", "", "(1)"],
        ["where It is inventory at time t and I* is the desired inventory level.", "", ""],
    ]

    kept, notes, removed = trim_embedded_prose_tail(rows)

    assert kept == rows[:4]
    assert notes == []
    assert removed == 3


def test_split_prose_table_rows_strips_download_footer_labels_but_keeps_data():
    rows = [
        ["Downloaded", "-4", "23.7"],
        ["https://academic.oup.com/rfs/article-abstract/32/1/1/5058062", "4", "104.9"],
        ["Liquidity", "-0.31", "-0.28"],
    ]

    kept, notes, removed = split_prose_table_rows(rows)

    assert kept == [["", "-4", "23.7"], ["", "4", "104.9"], ["Liquidity", "-0.31", "-0.28"]]
    assert notes == []
    assert removed == 0
