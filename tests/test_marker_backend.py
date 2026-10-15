from pathlib import Path

import pytest

from pdf_md_convert.marker_backend import default_output_dir, discover_pdfs, normalize_marker_markdown


def test_discover_pdfs_preserves_sorted_pdf_paths(tmp_path: Path):
    (tmp_path / "B Paper.pdf").write_bytes(b"%PDF-")
    (tmp_path / "A Paper.pdf").write_bytes(b"%PDF-")
    (tmp_path / "notes.txt").write_text("ignore", encoding="utf-8")

    pdfs = discover_pdfs(tmp_path)

    assert [p.name for p in pdfs] == ["A Paper.pdf", "B Paper.pdf"]


def test_default_batch_output_dir_uses_source_folder_name(tmp_path: Path):
    folder = tmp_path / "bond_close"
    folder.mkdir()

    assert default_output_dir(folder) == folder / "bond_close_md"


def test_discover_pdfs_rejects_non_pdf_file(tmp_path: Path):
    path = tmp_path / "paper.txt"
    path.write_text("not a pdf", encoding="utf-8")

    with pytest.raises(SystemExit):
        discover_pdfs(path)


def test_normalize_marker_markdown_attaches_dangling_equation_tag():
    text = "$$x = y.\n$$\n (19)\n"

    out = normalize_marker_markdown(text)

    assert "\\tag{19}" in out
    assert "(19)" not in out


def test_normalize_marker_markdown_handles_adjacent_tagged_blocks():
    text = "$$a=b, \\tag{18}$$\n\n$$x = y.$$\n (19)\n"

    out = normalize_marker_markdown(text)

    assert "\\tag{18}" in out
    assert "\\tag{19}" in out
    assert "(19)" not in out
