from pdf_md_convert.equation_regions import candidate_latex, equation_context_flags


def test_candidate_latex_accepts_clean_aligned_system():
    raw = (
        "Haircuti,t = gamma MMFExposurei,'14 * 1(Year = 2017) + deltai + deltaj,t + etai,j,t,\n\n"
        "BondLiquidityi,j,t = betaIV Haircuti,t + mui + muj,t + epsiloni,j,t,\n"
        "(3)\n"
    )

    latex, confidence, reasons = candidate_latex(raw, "3")

    assert confidence == "high"
    assert reasons == ["aligned-multi-line-equation"]
    assert latex is not None
    assert "\\begin{aligned}" in latex
    assert "\\tag{3}" in latex


def test_candidate_latex_discards_code_listing_false_positive():
    raw = "13\n\n*****************************************************************************\nERROR FILTER\n*****"

    latex, confidence, reasons = candidate_latex(raw, "13")

    assert latex is None
    assert confidence == "discard"
    assert "code-or-listing-block" in reasons


def test_candidate_latex_discards_prose_inline_fragment():
    raw = "and d2 = d1 - sigmaV sqrt(T). Furthermore, the relation can be expressed as a long sentence."

    latex, confidence, reasons = candidate_latex(raw, "1")

    assert latex is None
    assert confidence == "discard"
    assert "prose-inline-equation" in reasons


def test_equation_context_flags_accepts_standalone_display_tag():
    has_tag_text, has_display_tag, has_reference = equation_context_flags("45", "45")

    assert not has_tag_text
    assert has_display_tag
    assert not has_reference
