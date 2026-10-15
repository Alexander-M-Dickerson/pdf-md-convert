#!/usr/bin/env python
"""Assemble manuscript_ppX_Y.md chunks into manuscript.md."""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path
from typing import Iterable


WORKSPACE = Path.cwd()
RANGE_RE = re.compile(r"manuscript_pp(\d+)[_-](\d+)\.md$", re.IGNORECASE)


def safe_folder(path: Path) -> Path:
    folder = path.expanduser().resolve()
    return folder


def load_manifest(folder: Path) -> dict:
    manifest_path = folder / "conversion_work" / "manifest.json"
    if manifest_path.exists():
        return json.loads(manifest_path.read_text(encoding="utf-8"))
    return {}


def chunk_key(path: Path) -> tuple[int, int]:
    m = RANGE_RE.match(path.name)
    if not m:
        return (10**9, 10**9)
    return (int(m.group(1)), int(m.group(2)))


def strip_chunk_header(text: str) -> str:
    lines = text.replace("\r\n", "\n").replace("\r", "\n").splitlines()
    if lines and lines[0].lower().startswith("# manuscript pp"):
        lines = lines[1:]
    while lines and lines[0].strip() in {"", "---"}:
        lines = lines[1:]
    return "\n".join(lines).strip() + "\n"


def title_from_manifest(manifest: dict, fallback: str) -> str:
    meta = manifest.get("metadata") or {}
    title = (meta.get("title") or "").strip()
    return title or fallback


def main(argv: Iterable[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("folder", help="Paper/article folder containing manuscript_ppX_Y.md files")
    parser.add_argument("--force", action="store_true", help="Overwrite existing manuscript.md")
    args = parser.parse_args(argv)

    folder = safe_folder(Path(args.folder))
    out = folder / "manuscript.md"
    if out.exists() and not args.force:
        raise SystemExit(f"{out} already exists. Re-run with --force to overwrite.")

    chunks = sorted((p for p in folder.glob("manuscript_pp*.md") if RANGE_RE.match(p.name)), key=chunk_key)
    if not chunks:
        raise SystemExit(f"No manuscript_ppX_Y.md files found in {folder}")

    manifest = load_manifest(folder)
    title = title_from_manifest(manifest, folder.name.replace("_", " ").replace("-", " ").title())
    page_count = manifest.get("page_count", "unknown")
    sha = manifest.get("source_sha256", "unknown")

    parts = [
        f"# {title}",
        "",
        "<!-- @document-metadata",
        f"  @title: {title}",
        "  @type: academic-paper",
        "  @core-question: to be completed from converted manuscript",
        "  @core-answer: to be completed from converted manuscript",
        "  @datasets: to be completed from converted manuscript",
        "  @key-equations: to be completed from converted manuscript",
        "  @key-tables: to be completed from converted manuscript",
        "  @key-figures: to be completed from converted manuscript",
        f"  @source-pages: {page_count}",
        f"  @source-sha256: {sha}",
        "-->",
        "",
        "---",
        "",
        "## AI Reading Guide",
        "",
        "To be completed after body assembly: notation glossary, equation quick-reference, core claims map, dataset divergence log if applicable, and reading paths.",
        "",
        "---",
        "",
    ]
    for chunk in chunks:
        parts.append(strip_chunk_header(chunk.read_text(encoding="utf-8")))
        parts.append("")

    manuscript = "\n".join(parts)
    out.write_text(manuscript, encoding="utf-8")
    print(f"Wrote {out} from {len(chunks)} chunks.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

