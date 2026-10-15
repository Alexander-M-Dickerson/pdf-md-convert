from pdf_md_convert.equation_regions import candidate_latex, is_math_block, tag_from_text


def test_tag_from_text_extracts_equation_number():
    assert tag_from_text("Pb = x\n(20)\n") == "20"


def test_tag_from_text_extracts_legacy_encoded_parentheses():
    assert tag_from_text("k\u017a1\neakZik:\n\u011119\u0162\n") == "19"


def test_tag_from_text_detects_same_line_equation_number():
    assert tag_from_text("𝐶𝑜𝑠𝑡𝑗= 𝛼+ 𝛽1 × 𝐶𝑟𝑖𝑠𝑖𝑠𝑡+ 𝜀𝑗. (2)") == "2"


def test_tag_from_text_detects_math_continuation_line_number():
    assert tag_from_text("+ 𝛽3 × 𝐿𝑜𝑔(𝐴𝑔𝑒𝑖) + 𝜀𝑖. (3)") == "3"


def test_tag_from_text_ignores_url_digits():
    assert tag_from_text("Electronic copy available at: https://ssrn.com/abstract=3615155") is None


def test_tag_from_text_ignores_ssrn_footer_page_number():
    assert tag_from_text("Electronic copy available at: https://ssrn.com/abstract=2977635\n\n1\n") is None


def test_is_math_block_ignores_abstract_equals_footer():
    assert not is_math_block("Electronic copy available at: https://ssrn.com/abstract=2977635\n\n1\n")


def test_tag_from_text_detects_proportional_equation_number():
    assert tag_from_text("p(N) \u221d N0.15e-0.20N.\n(1)\n") == "1"


def test_candidate_latex_high_confidence_linear_equation():
    latex, confidence, reasons = candidate_latex("Pb = (\x02V o - \x02V b)w + (Mask - U o)(1 - w),\n(20)\n", "20")

    assert confidence == "high"
    assert reasons == []
    assert latex is not None
    assert "\\widehat{V}^{o}" in latex
    assert "M^{ask}" in latex
    assert "\\tag{20}" in latex


def test_candidate_latex_normalizes_mathematical_unicode():
    latex, confidence, reasons = candidate_latex("𝐶𝑜𝑠𝑡𝑗= 𝛼+ 𝛽1 × 𝐶𝑟𝑖𝑠𝑖𝑠𝑡+ 𝜀𝑗. (2)", "2")

    assert confidence == "high"
    assert reasons == []
    assert latex is not None
    assert "\\alpha" in latex
    assert "\\beta" in latex
    assert "\\times" in latex
    assert "\\tag{2}" in latex


def test_candidate_latex_rejects_stacked_fraction_layout():
    latex, confidence, reasons = candidate_latex("V no = V b\nη\nr + η,\n(23)\n", "23")

    assert latex is None
    assert confidence == "low"
    assert "multi-line-stacked-equation" in reasons
