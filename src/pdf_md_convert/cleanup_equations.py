#!/usr/bin/env python
"""Convert raw equation-like blocks in manuscript.md into tagged LaTeX blocks."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable


WORKSPACE = Path.cwd()

GREEK = {
    "\u03b1": r"\alpha",
    "\u03b2": r"\beta",
    "\u03b3": r"\gamma",
    "\u03b4": r"\delta",
    "\u03b5": r"\varepsilon",
    "\u03b7": r"\eta",
    "\u03b8": r"\theta",
    "\u03ba": r"\kappa",
    "\u03bb": r"\lambda",
    "\u03bc": r"\mu",
    "\u03c0": r"\pi",
    "\u03c1": r"\rho",
    "\u03c3": r"\sigma",
    "\u03c4": r"\tau",
    "\u03c6": r"\phi",
    "\u03c7": r"\chi",
    "\u03c9": r"\omega",
    "\u0394": r"\Delta",
    "\u0398": r"\Theta",
    "\u03a6": r"\Phi",
    "\u03a9": r"\Omega",
}

SYMBOLS = {
    "\u2212": "-",
    "\u2013": "-",
    "\u2014": "-",
    "\u00d7": r"\times",
    "\u2264": r"\le",
    "\u2265": r"\ge",
    "\u2261": r"\equiv",
    "\u221d": r"\propto",
    "\u2208": r"\in",
    "\u2211": r"\sum",
    "\u221e": r"\infty",
    "\u2032": "'",
    "\u1d40": r"^{T}",
}

MATH_TOKEN_RE = re.compile(
    r"[=+\-*/\u00d7\u2264\u2265\u2261\u221d\u2208\u2211\u0394\u0398\u03a6\u03a9"
    r"\u03b1\u03b2\u03b3\u03b4\u03b5\u03b7\u03b8\u03ba\u03bb\u03bc\u03c0\u03c1\u03c3\u03c4\u03c6\u03c7\u03c9]"
    r"|\b(Pr|ln|log|exp|min|max|arg|E|Var|Cov|alpha|beta|gamma|delta|epsilon)\b"
)
TABLE_WORD_RE = re.compile(
    r"\b(Determinant|Panel|Column|Standard errors|significance|Client buy|Client sell|Event-time|Dependent variable)\b",
    re.IGNORECASE,
)


@dataclass
class Change:
    tag: str
    start_line: int
    raw: str
    latex: str


def safe_folder(path: Path) -> Path:
    folder = path.expanduser().resolve()
    return folder


def has_math(line: str) -> bool:
    return bool(MATH_TOKEN_RE.search(line))


def is_false_positive(block: list[str]) -> bool:
    joined = " ".join(block)
    if TABLE_WORD_RE.search(joined) and not any("=" in line or "\u2261" in line or "\u221d" in line for line in block):
        return True
    meaningful = [line for line in block if line.strip() and not re.fullmatch(r"\(\d+\)", line.strip())]
    return not any(has_math(line) for line in meaningful)


def block_start(lines: list[str], tag_index: int) -> int:
    start = tag_index
    seen_math = False
    for i in range(tag_index - 1, max(-1, tag_index - 8), -1):
        s = lines[i].strip()
        if not s or s.startswith("*(Page ") or s.startswith("#") or s.startswith("<!--"):
            break
        line_has_math = has_math(s)
        if seen_math and not line_has_math:
            break
        if len(s) > 180 and not line_has_math:
            break
        if TABLE_WORD_RE.search(s) and not line_has_math:
            break
        if line_has_math:
            seen_math = True
        start = i
    return start


def cleanup_line(line: str) -> str:
    s = " ".join(line.strip().split())
    for src, dst in SYMBOLS.items():
        s = s.replace(src, dst)
    for src, dst in GREEK.items():
        s = s.replace(src, dst)
    for word, latex in {
        "alpha": r"\alpha",
        "beta": r"\beta",
        "gamma": r"\gamma",
        "delta": r"\delta",
        "epsilon": r"\varepsilon",
    }.items():
        s = re.sub(rf"\b{word}(?=_|\b)", lambda _m, repl=latex: repl, s)
    s = re.sub(r"\b([A-Za-z])it\b", r"\1_{it}", s)
    s = re.sub(r"\b([A-Za-z])ijbt\b", r"\1_{ijbt}", s)
    s = re.sub(r"\b([A-Za-z])bt\b", r"\1_{bt}", s)
    s = re.sub(r"\bln([A-Za-z])", r"\\ln \1", s)
    s = s.replace("Pr(", r"\Pr(")
    s = s.replace("E[", r"\mathbb{E}[")
    return re.sub(r" {2,}", " ", s)


def latex_block(raw_lines: list[str], tag: str) -> str:
    body = [cleanup_line(line) for line in raw_lines if line.strip() and not re.fullmatch(r"\(\d+\)", line.strip())]
    if len(body) >= 2:
        expr = " \\\\\n".join(body)
        return "$$\n\\begin{aligned}\n" + expr + f"\n\\end{{aligned}}\n\\tag{{{tag}}}\n$$"
    return "$$\n" + (body[0] if body else "") + f"\n\\tag{{{tag}}}\n$$"


def process_text(text: str) -> tuple[str, list[Change]]:
    lines = text.splitlines()
    out: list[str] = []
    changes: list[Change] = []
    i = 0
    while i < len(lines):
        s = lines[i].strip()
        m = re.fullmatch(r"\((\d{1,3})\)", s)
        if not m:
            out.append(lines[i])
            i += 1
            continue
        start = block_start(lines, i)
        block = lines[start : i + 1]
        if start == i or is_false_positive(block):
            out.append(lines[i])
            i += 1
            continue
        remove_count = i - start
        if remove_count:
            out = out[:-remove_count]
        tag = m.group(1)
        latex = latex_block(block, tag)
        out.extend(["", latex, ""])
        changes.append(Change(tag=tag, start_line=start + 1, raw="\n".join(block), latex=latex))
        i += 1
    return "\n".join(out).rstrip() + "\n", changes


def update_handover(folder: Path, changes: list[Change]) -> None:
    path = folder / "handover.md"
    if not path.exists():
        return
    text = path.read_text(encoding="utf-8")
    marker = "\n## Equation Cleanup\n"
    section = [
        "## Equation Cleanup",
        f"- Converted {len(changes)} raw equation-like blocks into tagged LaTeX display equations.",
        "- Equations were converted from extracted PDF text and remain flagged for visual audit against rendered pages when mathematical precision is critical.",
    ]
    if marker in text:
        text = text.split(marker)[0].rstrip() + "\n\n" + "\n".join(section) + "\n"
    else:
        text = text.rstrip() + "\n\n" + "\n".join(section) + "\n"
    path.write_text(text, encoding="utf-8")


def process_folder(folder: Path, flat_output: Path | None = None) -> dict:
    folder = safe_folder(folder)
    manuscript = folder / "manuscript.md"
    if not manuscript.exists():
        return {"folder": str(folder), "changed": 0, "error": "missing manuscript.md"}
    text = manuscript.read_text(encoding="utf-8")
    new_text, changes = process_text(text)
    if changes:
        manuscript.write_text(new_text, encoding="utf-8")
        update_handover(folder, changes)
        audit_dir = folder / "conversion_work"
        audit_dir.mkdir(parents=True, exist_ok=True)
        audit = audit_dir / "equation_cleanup_audit.json"
        audit.write_text(json.dumps([c.__dict__ for c in changes], indent=2, ensure_ascii=False), encoding="utf-8")
        if flat_output is not None:
            flat_output.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(manuscript, flat_output)
    return {"folder": str(folder), "changed": len(changes), "flat_output": str(flat_output) if flat_output else None}


def slugify(stem: str) -> str:
    stem = stem.lower().replace("&", " and ")
    stem = re.sub(r"['`]", "", stem)
    words = re.findall(r"[a-z0-9]+", stem)
    words = [w for w in words if w not in {"a", "an", "in"}]
    return "-".join(words[:10]) or "article"


def main(argv: Iterable[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("input", help="Article folder or folder containing PDFs")
    parser.add_argument("--workspace", type=Path, help="Root used by the convert workpack command")
    parser.add_argument("--flat-output-dir", help="Flat md directory to update when input is a PDF folder")
    args = parser.parse_args(argv)

    source = Path(args.input).expanduser().resolve()
    if source.is_dir() and not (source / "manuscript.md").exists():
        out_dir = Path(args.flat_output_dir).expanduser().resolve() if args.flat_output_dir else source / f"{source.name}_md"
        results = []
        for pdf in sorted(source.glob("*.pdf")):
            if args.workspace is None:
                parser.error("Folder batches require --workspace pointing to their prepared workpacks")
            source_hash = hashlib.sha256(pdf.read_bytes()).hexdigest()
            matches = [p.parent.parent for p in args.workspace.glob("articles/*/conversion_work/manifest.json")
                       if json.loads(p.read_text(encoding="utf-8")).get("source_sha256") == source_hash]
            if len(matches) != 1:
                results.append({"source": str(pdf), "error": "missing or ambiguous prepared workpack"})
                continue
            folder = matches[0]
            results.append(process_folder(folder, out_dir / f"{pdf.stem}.md"))
    else:
        results = [process_folder(source)]
    print(json.dumps({"results": results}, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
