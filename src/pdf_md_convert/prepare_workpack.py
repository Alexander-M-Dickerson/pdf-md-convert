#!/usr/bin/env python
"""Prepare a local PDF conversion workpack for Codex."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import shutil
import sys
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Iterable

try:
    import fitz  # PyMuPDF
except Exception as exc:  # pragma: no cover
    raise SystemExit("PyMuPDF is required. Install the base pdf-md-convert package.") from exc


WORKSPACE = Path.cwd() / "pdf-md-workspace"
LIGATURES = {
    "\ufb00": "ff",
    "\ufb01": "fi",
    "\ufb02": "fl",
    "\ufb03": "ffi",
    "\ufb04": "ffl",
    "\u00ad": "",
}


@dataclass
class PageInfo:
    page: int
    chars: int
    words: int
    low_text: bool
    table_refs: int
    figure_refs: int
    equation_like_lines: int
    headings: list[str]
    render_path: str | None
    text_path: str


def safe_resolve(path: Path) -> Path:
    try:
        return path.expanduser().resolve()
    except FileNotFoundError:
        return path.expanduser().absolute()


def ensure_under_workspace(path: Path) -> None:
    resolved = safe_resolve(path)
    root = safe_resolve(WORKSPACE)
    if root not in [resolved, *resolved.parents]:
        raise SystemExit(f"Refusing to write outside workspace: {resolved}")


def slugify(stem: str) -> str:
    stem = stem.lower().replace("&", " and ")
    stem = re.sub(r"['`]", "", stem)
    words = re.findall(r"[a-z0-9]+", stem)
    words = [w for w in words if w not in {"a", "an", "in"}]
    return "-".join(words[:10]) or "article"


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for block in iter(lambda: fh.read(1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()


def normalize_text(text: str) -> str:
    for src, dst in LIGATURES.items():
        text = text.replace(src, dst)
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    text = re.sub(r"(?<=[A-Za-z])-\n(?=[a-z])", "", text)
    text = re.sub(r"[ \t]+\n", "\n", text)
    return text


def chunk_size(page_count: int) -> int:
    if page_count < 12:
        return page_count
    if page_count <= 25:
        return 8
    if page_count <= 45:
        return 6
    if page_count <= 80:
        return 5
    return 4


def resolve_pdf_input(input_path: Path, force: bool) -> list[Path]:
    src = safe_resolve(input_path)
    if not src.exists():
        raise SystemExit(f"Input does not exist: {src}")

    if src.is_dir() and (src / "manuscript.pdf").exists():
        return [copy_direct_pdf(src / "manuscript.pdf", force=force)]

    if src.is_dir():
        pdfs = sorted(p for p in src.glob("*.pdf") if p.is_file())
        if not pdfs:
            raise SystemExit(f"No PDFs found in {src}")
        folders = []
        for pdf in pdfs:
            folders.append(copy_direct_pdf(pdf, force=force))
        return folders

    if src.suffix.lower() != ".pdf":
        raise SystemExit(f"Input is not a PDF or paper folder: {src}")

    if src.name.lower() == "manuscript.pdf" and src.parent.name and src.parent.parent.name in {"papers", "articles"}:
        return [copy_direct_pdf(src, force=force)]

    return [copy_direct_pdf(src, force=force)]


def copy_direct_pdf(src: Path, force: bool) -> Path:
    folder = WORKSPACE / "articles" / (slugify(src.parent.name if src.stem == "manuscript" else src.stem) + "-" + sha256(src)[:12])
    ensure_under_workspace(folder)
    folder.mkdir(parents=True, exist_ok=True)
    dst = folder / "manuscript.pdf"
    if dst.exists():
        if sha256(dst) == sha256(src):
            return folder
        if not force:
            raise SystemExit(f"{dst} already exists with different content. Re-run with --force to replace it.")
    shutil.copy2(src, dst)
    return folder


def iter_headings(text: str) -> list[str]:
    out: list[str] = []
    section_pattern = re.compile(
        r"^(Abstract|References|Appendix\b.*|[0-9]+\.?\s+[A-Z][A-Za-z0-9 ,:;&()/-]{3,})$",
        re.IGNORECASE,
    )
    exhibit_pattern = re.compile(
        r"^(Table\s+(?:IA\.)?[0-9IVX]+|Figure\s+[0-9IVX]+)(?:[.:]\s+.*)?$",
        re.IGNORECASE,
    )
    for line in text.splitlines():
        s = " ".join(line.strip().split())
        if len(s) > 160:
            continue
        if section_pattern.match(s) or exhibit_pattern.match(s):
            out.append(s)
    return out[:30]


def needs_render(
    mode: str,
    low_text: bool,
    headings: list[str],
    table_refs: int,
    figure_refs: int,
    equation_like_lines: int,
    page: int,
) -> bool:
    if mode == "audit":
        return True
    if low_text:
        return True
    if mode == "fast":
        return page == 1
    exhibit_heading = any(h.lower().startswith(("table ", "figure ")) for h in headings)
    return page == 1 or exhibit_heading or table_refs >= 2 or figure_refs >= 2 or equation_like_lines > 0


def render_page(page: fitz.Page, out: Path, dpi: int) -> None:
    zoom = dpi / 72
    pix = page.get_pixmap(matrix=fitz.Matrix(zoom, zoom), alpha=False)
    pix.save(out)


def prepare_folder(folder: Path, mode: str, render_dpi: int, force: bool) -> dict:
    folder = safe_resolve(folder)
    ensure_under_workspace(folder)
    pdf = folder / "manuscript.pdf"
    if not pdf.exists():
        raise SystemExit(f"Missing manuscript.pdf in {folder}")

    work = folder / "conversion_work"
    pages_dir = work / "pages"
    renders_dir = work / "renders"
    chunks_dir = work / "chunk_prompts"
    for d in (pages_dir, renders_dir, chunks_dir):
        d.mkdir(parents=True, exist_ok=True)

    doc = fitz.open(pdf)
    page_count = doc.page_count
    csize = chunk_size(page_count)
    page_infos: list[PageInfo] = []
    source_md: list[str] = []

    for index, page in enumerate(doc, start=1):
        raw = page.get_text("text")
        text = normalize_text(raw)
        words = re.findall(r"\w+", text)
        headings = iter_headings(text)
        table_refs = len(re.findall(r"\bTable\s+(?:IA\.)?[0-9IVX]+", text, flags=re.IGNORECASE))
        figure_refs = len(re.findall(r"\bFigure\s+[0-9IVX]+", text, flags=re.IGNORECASE))
        eq_lines = sum(1 for line in text.splitlines() if re.search(r"\([0-9]{1,2}\)\s*$", line.strip()))
        low_text = len(words) < 80
        text_path = pages_dir / f"page_{index:03d}.txt"
        text_path.write_text(text, encoding="utf-8")
        render_rel = None
        if needs_render(mode, low_text, headings, table_refs, figure_refs, eq_lines, index):
            render_path = renders_dir / f"page_{index:03d}.png"
            render_page(page, render_path, render_dpi)
            render_rel = str(render_path.relative_to(folder)).replace("\\", "/")
        page_infos.append(
            PageInfo(
                page=index,
                chars=len(text),
                words=len(words),
                low_text=low_text,
                table_refs=table_refs,
                figure_refs=figure_refs,
                equation_like_lines=eq_lines,
                headings=headings,
                render_path=render_rel,
                text_path=str(text_path.relative_to(folder)).replace("\\", "/"),
            )
        )
        source_md.append(f"*(Page {index})*\n\n{text.strip()}\n")

    chunk_ranges = []
    for start in range(1, page_count + 1, csize):
        end = min(start + csize - 1, page_count)
        chunk_ranges.append({"start": start, "end": end, "file": f"manuscript_pp{start}_{end}.md"})
        prompt = chunks_dir / f"pp{start:03d}_{end:03d}.md"
        prompt.write_text(make_chunk_prompt(folder, start, end, page_infos), encoding="utf-8")

    manifest = {
        "schema_version": 1,
        "source_pdf": str(pdf.relative_to(folder)).replace("\\", "/"),
        "source_sha256": sha256(pdf),
        "metadata": doc.metadata,
        "page_count": page_count,
        "mode": mode,
        "chunk_size": csize,
        "chunk_ranges": chunk_ranges,
        "counts": {
            "low_text_pages": sum(p.low_text for p in page_infos),
            "table_refs": sum(p.table_refs for p in page_infos),
            "figure_refs": sum(p.figure_refs for p in page_infos),
            "equation_like_lines": sum(p.equation_like_lines for p in page_infos),
            "rendered_pages": sum(1 for p in page_infos if p.render_path),
        },
        "pages": [asdict(p) for p in page_infos],
    }

    (work / "manifest.json").write_text(json.dumps(manifest, indent=2, ensure_ascii=False), encoding="utf-8")
    (work / "source_pages.md").write_text("\n---\n\n".join(source_md), encoding="utf-8")
    (work / "equation_inventory.md").write_text(make_equation_inventory(folder, manifest), encoding="utf-8")
    (work / "semantic_tasks.md").write_text(make_semantic_tasks(manifest), encoding="utf-8")
    return {"folder": str(folder), **manifest["counts"], "page_count": page_count, "chunk_ranges": chunk_ranges}


def make_equation_inventory(folder: Path, manifest: dict) -> str:
    lines = ["# Equation Inventory", ""]
    lines.append("Use rendered page images to transcribe these equations into LaTeX; raw text is only a locator.")
    lines.append("")
    for p in manifest["pages"]:
        if not p["equation_like_lines"]:
            continue
        page_text = (folder / p["text_path"]).read_text(encoding="utf-8")
        candidates = []
        text_lines = page_text.splitlines()
        for i, line in enumerate(text_lines):
            if re.search(r"\([0-9]{1,2}\)\s*$", line.strip()):
                start = max(0, i - 3)
                end = min(len(text_lines), i + 2)
                candidates.append("\n".join(text_lines[start:end]).strip())
        lines.append(f"## Page {p['page']}")
        if p.get("render_path"):
            lines.append(f"Rendered page: `{p['render_path']}`")
        lines.append("")
        for n, candidate in enumerate(candidates, start=1):
            lines.append(f"### Candidate {n}")
            lines.append("```text")
            lines.append(candidate)
            lines.append("```")
            lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def make_semantic_tasks(manifest: dict) -> str:
    counts = manifest["counts"]
    lines = [
        "# Semantic Conversion Tasks",
        "",
        "Codex must complete these tasks after the raw draft is assembled. Do not ask the user to run commands.",
        "",
        "## Priority 1: Equations",
    ]
    equation_pages = [p for p in manifest["pages"] if p["equation_like_lines"]]
    if equation_pages:
        lines.append("Transcribe equations from rendered page images, using `equation_inventory.md` only as a locator.")
        for p in equation_pages:
            lines.append(
                f"- Page {p['page']}: {p['equation_like_lines']} equation-like lines; render `{p.get('render_path') or 'missing render'}`."
            )
    else:
        lines.append("- No equation-like pages detected.")

    lines.extend(["", "## Priority 2: Tables"])
    table_pages = [p for p in manifest["pages"] if any(h.lower().startswith("table ") for h in p["headings"])]
    if table_pages:
        lines.append("Reconstruct tables from rendered page images; raw text is often one cell per line.")
        for p in table_pages:
            heads = "; ".join(h for h in p["headings"] if h.lower().startswith("table "))
            lines.append(f"- Page {p['page']}: {heads}; render `{p.get('render_path') or 'missing render'}`.")
    else:
        lines.append(f"- No table headings detected, but raw text has {counts.get('table_refs', 0)} table references. Inspect rendered reference pages.")

    lines.extend(["", "## Priority 3: Figures"])
    figure_pages = [p for p in manifest["pages"] if any(h.lower().startswith("figure ") for h in p["headings"])]
    if figure_pages:
        lines.append("Describe figures from rendered pages, including axes, series, trends, and precision notes for visual estimates.")
        for p in figure_pages:
            heads = "; ".join(h for h in p["headings"] if h.lower().startswith("figure "))
            lines.append(f"- Page {p['page']}: {heads}; render `{p.get('render_path') or 'missing render'}`.")
    else:
        lines.append(f"- No figure headings detected, but raw text has {counts.get('figure_refs', 0)} figure references. Inspect rendered reference pages.")

    lines.extend(
        [
            "",
            "## Priority 4: Reading Guide And Handover",
            "- Complete document metadata, notation glossary, equation quick-reference, claims map, and reading paths.",
            "- Update `handover.md` so validation warnings are either resolved or documented as source limitations.",
            "- Rerun `validate_conversion.py` after edits.",
        ]
    )
    return "\n".join(lines).rstrip() + "\n"


def make_chunk_prompt(folder: Path, start: int, end: int, pages: list[PageInfo]) -> str:
    rel = folder.relative_to(WORKSPACE)
    lines = [
        f"# Conversion workpack pp. {start}-{end}",
        "",
        f"Folder: `{rel}`",
        f"Write output to: `{rel / f'manuscript_pp{start}_{end}.md'}`",
        "",
        "Use `references/conversion-contract.md`. Never write chunk-relative language.",
        "",
        "Pages:",
    ]
    for p in pages[start - 1 : end]:
        render = f", render `{p.render_path}`" if p.render_path else ""
        headings = "; ".join(p.headings[:4])
        lines.append(f"- Page {p.page}: `{p.text_path}`{render}. Headings: {headings or 'none'}")
    return "\n".join(lines) + "\n"


def main(argv: Iterable[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("input", help="Paper folder, direct PDF, or folder of PDFs")
    parser.add_argument("--mode", choices=["fast", "balanced", "audit"], default="balanced")
    parser.add_argument("--workspace", type=Path, default=None, help="Explicit writable workpack root")
    parser.add_argument("--render-dpi", type=int, default=160)
    parser.add_argument("--force", action="store_true", help="Replace existing copied manuscript.pdf for direct PDF inputs")
    args = parser.parse_args(argv)

    global WORKSPACE
    WORKSPACE = (args.workspace or WORKSPACE).expanduser().resolve()
    folders = resolve_pdf_input(Path(args.input), force=args.force)
    results = [prepare_folder(folder, mode=args.mode, render_dpi=args.render_dpi, force=args.force) for folder in folders]
    print(json.dumps({"folders": results}, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

