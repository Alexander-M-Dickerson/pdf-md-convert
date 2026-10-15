#!/usr/bin/env python
"""Validate a PDF-to-Markdown conversion against its workpack."""

from __future__ import annotations

import argparse
import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable


WORKSPACE = Path.cwd()
ARTIFACT_PATTERNS = [
    r"\bin this chunk\b",
    r"\bprevious chunk\b",
    r"\bsubsequent chunk\b",
    r"\bbeyond this chunk\b",
    r"\bnot displayed\b",
    r"\bnot shown here\b",
    r"\bnot rendered\b",
    r"\bchunk file\b",
    r"\bchunk pages\b",
]
LIGATURE_RE = re.compile("[\ufb00-\ufb04]")


@dataclass
class Finding:
    level: str
    code: str
    message: str


def safe_folder(path: Path) -> Path:
    folder = path.expanduser().resolve()
    return folder


def load_manifest(folder: Path) -> dict:
    manifest = folder / "conversion_work" / "manifest.json"
    if not manifest.exists():
        return {}
    return json.loads(manifest.read_text(encoding="utf-8"))


def heading_ids(kind: str, text: str) -> set[str]:
    ids = set()
    pattern = re.compile(rf"^#+\s*{kind}\s+([A-Za-z0-9.]+)", re.IGNORECASE | re.MULTILINE)
    for match in pattern.finditer(text):
        ids.add(match.group(1).rstrip(".").upper())
    return ids


def pointer_ids(kind: str, text: str) -> set[str]:
    ids = set()
    pattern = re.compile(rf"\*\[{kind}\s+([A-Za-z0-9.]+)\s+here\]\*", re.IGNORECASE)
    for match in pattern.finditer(text):
        ids.add(match.group(1).rstrip(".").upper())
    return ids


def add(finds: list[Finding], level: str, code: str, message: str) -> None:
    finds.append(Finding(level, code, message))


def validate(folder: Path) -> list[Finding]:
    finds: list[Finding] = []
    manifest = load_manifest(folder)
    manuscript_path = folder / "manuscript.md"
    handover_path = folder / "handover.md"

    if not manifest:
        add(finds, "warn", "missing_manifest", "conversion_work/manifest.json is missing.")
    if not manuscript_path.exists():
        add(finds, "fail", "missing_manuscript", "manuscript.md is missing.")
        return finds

    text = manuscript_path.read_text(encoding="utf-8")
    handover = handover_path.read_text(encoding="utf-8") if handover_path.exists() else ""

    if not handover_path.exists():
        add(finds, "fail", "missing_handover", "handover.md is missing.")

    for pat in ARTIFACT_PATTERNS:
        if re.search(pat, text, flags=re.IGNORECASE):
            add(finds, "fail", "chunk_artifact", f"Found conversion-process phrase matching `{pat}`.")

    page_count = manifest.get("page_count")
    if page_count:
        markers = {int(m.group(1)) for m in re.finditer(r"\*\(Page\s+(\d+)\)\*", text)}
        if len(markers) < max(1, int(page_count) * 0.9):
            add(finds, "fail", "page_coverage", f"Only {len(markers)} page markers found for {page_count} source pages.")
        missing = [p for p in range(1, int(page_count) + 1) if p not in markers]
        if missing:
            add(finds, "warn", "page_gaps", f"Missing page markers: {format_ranges(missing)}.")

    if LIGATURE_RE.search(text):
        add(finds, "warn", "ligatures", "Common PDF ligatures remain in manuscript.md.")

    if re.search(r"[A-Za-z]-\n[a-z]", text):
        add(finds, "warn", "hyphen_breaks", "Line-break hyphenation remains in manuscript.md.")

    section_tags = len(re.findall(r"@section-type", text))
    if section_tags < 5:
        add(finds, "warn", "sparse_tags", f"Only {section_tags} section navigation tags found.")

    counts = manifest.get("counts") or {}
    if counts.get("equation_like_lines", 0) and "\\tag{" not in text:
        add(finds, "warn", "equation_index", "Source has equation-like lines but no LaTeX equation tags were found.")

    for kind in ("Table", "Figure"):
        ptrs = pointer_ids(kind, text)
        heads = heading_ids(kind, text)
        unresolved = sorted(ptrs - heads)
        if unresolved:
            add(finds, "fail", "unresolved_pointer", f"{kind} pointers without matching headings: {', '.join(unresolved)}.")

    table_refs = counts.get("table_refs", 0)
    figure_refs = counts.get("figure_refs", 0)
    table_heads = len(heading_ids("Table", text))
    figure_heads = len(heading_ids("Figure", text))
    if table_refs >= 5 and table_heads == 0:
        add(finds, "warn", "missing_tables", f"Source has {table_refs} table references but no converted table headings.")
    if figure_refs >= 5 and figure_heads == 0:
        add(finds, "warn", "missing_figures", f"Source has {figure_refs} figure references but no converted figure headings.")

    low_text_pages = counts.get("low_text_pages", 0)
    if low_text_pages and "low-text" not in handover.lower() and "ocr" not in handover.lower():
        add(finds, "warn", "low_text_handover", "Low-text pages detected but not documented in handover.md.")

    return finds


def format_ranges(values: list[int]) -> str:
    if not values:
        return "none"
    ranges = []
    start = prev = values[0]
    for value in values[1:]:
        if value == prev + 1:
            prev = value
            continue
        ranges.append(f"{start}-{prev}" if start != prev else str(start))
        start = prev = value
    ranges.append(f"{start}-{prev}" if start != prev else str(start))
    return ", ".join(ranges)


def main(argv: Iterable[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("folder", help="Paper/article folder")
    parser.add_argument("--json", action="store_true", help="Emit JSON")
    args = parser.parse_args(argv)

    folder = safe_folder(Path(args.folder))
    finds = validate(folder)
    counts = {level: sum(1 for f in finds if f.level == level) for level in ("fail", "warn")}
    if args.json:
        print(json.dumps({"counts": counts, "findings": [f.__dict__ for f in finds]}, indent=2))
    else:
        print(f"Validation: {counts['fail']} fail, {counts['warn']} warn")
        for f in finds:
            print(f"[{f.level.upper()}] {f.code}: {f.message}")
    return 1 if counts["fail"] else 0


if __name__ == "__main__":
    raise SystemExit(main())

