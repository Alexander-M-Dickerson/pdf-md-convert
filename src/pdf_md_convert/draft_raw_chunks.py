#!/usr/bin/env python
"""Create page-marked raw chunk drafts from a conversion workpack."""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path
from typing import Iterable


WORKSPACE = Path.cwd()


def safe_folder(path: Path) -> Path:
    folder = path.expanduser().resolve()
    return folder


def load_manifest(folder: Path) -> dict:
    manifest_path = folder / "conversion_work" / "manifest.json"
    if not manifest_path.exists():
        raise SystemExit(f"Missing workpack manifest: {manifest_path}")
    return json.loads(manifest_path.read_text(encoding="utf-8"))


def heading_level(line: str, page_headings: set[str]) -> str | None:
    s = " ".join(line.strip().split())
    if not s or s not in page_headings:
        return None
    if re.match(r"^(Table|Figure)\s+", s, flags=re.IGNORECASE):
        return "##"
    if re.match(r"^(Abstract|References|Appendix\b)", s, flags=re.IGNORECASE):
        return "#"
    if re.match(r"^[0-9]+\.?\s+", s):
        return "#"
    return None


def section_type(line: str) -> str:
    s = line.strip().lower()
    if s.startswith("table "):
        return "table"
    if s.startswith("figure "):
        return "figure"
    if s.startswith("abstract"):
        return "introduction"
    if s.startswith("references"):
        return "references"
    if s.startswith("appendix"):
        return "appendix"
    if any(word in s for word in ("data", "sample")):
        return "data"
    if any(word in s for word in ("model", "estimation", "method", "equilibrium")):
        return "methodology"
    if any(word in s for word in ("result", "fit", "counterfactual", "cost")):
        return "results"
    if any(word in s for word in ("conclusion", "discussion")):
        return "conclusion"
    return "section"


def tag_for_heading(line: str) -> str:
    stype = section_type(line)
    return (
        "<!-- @section-type: "
        f"{stype}\n"
        f"  @key-claim: raw heading marker for {line.strip()}\n"
        "  @importance: supporting\n"
        "  @data-source: to be completed\n"
        "  @depends-on: to be completed\n"
        "  @equations: to be completed\n"
        "-->"
    )


def render_page_text(folder: Path, page_info: dict) -> str:
    page = page_info["page"]
    text_path = folder / page_info["text_path"]
    text = text_path.read_text(encoding="utf-8")
    headings = set(page_info.get("headings") or [])
    out = [f"*(Page {page})*", ""]
    for line in text.splitlines():
        s = line.rstrip()
        level = heading_level(s, headings)
        if level:
            clean = " ".join(s.split())
            out.extend([tag_for_heading(clean), f"{level} {clean}", ""])
        else:
            out.append(s)
    return "\n".join(out).strip() + "\n"


def write_handover(folder: Path, manifest: dict, force: bool) -> None:
    out = folder / "handover.md"
    if out.exists() and not force:
        return
    title = (manifest.get("metadata") or {}).get("title") or folder.name.replace("-", " ").title()
    counts = manifest.get("counts") or {}
    limitations = [
        "This handover was generated from the raw workpack draft and must be updated after semantic table, figure, equation, and AI Reading Guide refinement.",
    ]
    if counts.get("low_text_pages", 0):
        limitations.append(f"{counts['low_text_pages']} low-text pages require visual/OCR review.")
    if counts.get("table_refs", 0):
        limitations.append("Tables require visual verification because PDF text extraction may emit cells line-by-line.")
    if counts.get("figure_refs", 0):
        limitations.append("Figures require rendered-page review for axes, series, and visual estimates.")
    body = [
        f"# {title} -- Handover Document",
        "",
        "## What this is",
        f"A raw local workpack draft for a {manifest.get('page_count', 'unknown')}-page academic paper. The original PDF is authoritative.",
        "",
        "## File structure",
        "- `manuscript.pdf`: copied source PDF.",
        "- `conversion_work/manifest.json`: extraction inventory and QA counts.",
        "- `conversion_work/source_pages.md`: normalized page text.",
        "- `manuscript_ppX_Y.md`: raw page-range drafts.",
        "- `manuscript.md`: assembled markdown draft when `assemble_markdown.py` has been run.",
        "",
        "## Navigation system",
        "Raw drafts include page markers and promoted section/exhibit headings. Section tags and the AI Reading Guide must be completed during semantic refinement.",
        "",
        "## Known limitations",
    ]
    body.extend(f"- {item}" for item in limitations)
    body.extend(
        [
            "",
            "## Workpack counts",
            f"- Pages: {manifest.get('page_count', 'unknown')}",
            f"- Table references: {counts.get('table_refs', 0)}",
            f"- Figure references: {counts.get('figure_refs', 0)}",
            f"- Equation-like lines: {counts.get('equation_like_lines', 0)}",
            f"- Rendered pages: {counts.get('rendered_pages', 0)}",
        ]
    )
    out.write_text("\n".join(body) + "\n", encoding="utf-8")


def main(argv: Iterable[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("folder", help="Prepared paper/article folder")
    parser.add_argument("--force", action="store_true", help="Overwrite existing chunk drafts and optional handover")
    parser.add_argument("--handover", action="store_true", help="Write a raw-draft handover if missing")
    args = parser.parse_args(argv)

    folder = safe_folder(Path(args.folder))
    manifest = load_manifest(folder)
    pages = {p["page"]: p for p in manifest["pages"]}
    written = []

    for chunk in manifest["chunk_ranges"]:
        out = folder / chunk["file"]
        if out.exists() and not args.force:
            continue
        parts = [f"# Manuscript pp. {chunk['start']}-{chunk['end']}: raw draft", "", "---", ""]
        for page_num in range(chunk["start"], chunk["end"] + 1):
            parts.append(render_page_text(folder, pages[page_num]))
            parts.append("")
        out.write_text("\n".join(parts).strip() + "\n", encoding="utf-8")
        written.append(out.name)

    if args.handover:
        write_handover(folder, manifest, force=args.force)

    print(json.dumps({"written_chunks": written, "handover": args.handover}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

