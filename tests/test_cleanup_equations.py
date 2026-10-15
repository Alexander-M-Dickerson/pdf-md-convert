from pathlib import Path

from pdf_md_convert.cleanup_equations import process_text


def test_cleanup_equation_block():
    text = "Before\nYit = alpha_i + beta Xit + epsit.\n(3)\nAfter\n"
    out, changes = process_text(text)
    assert len(changes) == 1
    assert "\\tag{3}" in out
    assert "\\alpha" in out


def test_skip_table_column_numbers():
    text = "Determinant\n(1)\n(2)\nLagged volume\n"
    out, changes = process_text(text)
    assert changes == []
    assert out == text
