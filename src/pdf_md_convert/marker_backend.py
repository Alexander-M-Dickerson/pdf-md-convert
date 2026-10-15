from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
import tempfile
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable


@dataclass(frozen=True)
class MarkerResult:
    pdf: Path
    markdown: Path
    meta_json: Path | None
    output_markdown: Path
    page_range: str | None
    elapsed_seconds: float | None


def find_marker_single(explicit: str | None = None) -> str:
    if explicit:
        marker = Path(explicit).expanduser()
        if marker.exists():
            return str(marker)
        resolved = shutil.which(explicit)
        if resolved:
            return resolved
        raise SystemExit(f"marker_single executable not found: {explicit}")

    local = Path(sys.executable).resolve().parent / ("marker_single.exe" if sys.platform == "win32" else "marker_single")
    if local.exists():
        return str(local)

    resolved = shutil.which("marker_single")
    if resolved:
        return resolved
    resolved_exe = shutil.which("marker_single.exe")
    if resolved_exe:
        return resolved_exe

    raise SystemExit("marker_single was not found. Install with: pip install pdf-md-convert[ocr]")


def discover_pdfs(input_path: Path) -> list[Path]:
    source = input_path.expanduser().resolve()
    if source.is_file():
        if source.suffix.lower() != ".pdf":
            raise SystemExit(f"Input file is not a PDF: {source}")
        return [source]
    if source.is_dir():
        pdfs = sorted(p for p in source.iterdir() if p.is_file() and p.suffix.lower() == ".pdf")
        if pdfs:
            return pdfs
    raise SystemExit(f"No PDFs found: {source}")


def default_output_dir(input_path: Path) -> Path:
    source = input_path.expanduser().resolve()
    if source.is_dir():
        return source / f"{source.name}_md"
    return source.parent


def _marker_markdown_path(marker_root: Path, pdf: Path) -> tuple[Path, Path | None]:
    candidates = sorted(marker_root.rglob("*.md"))
    if not candidates:
        raise SystemExit(f"Marker did not create a markdown file under {marker_root}")

    exact = [p for p in candidates if p.stem == pdf.stem]
    markdown = exact[0] if exact else candidates[0]

    meta_candidates = sorted(markdown.parent.glob("*_meta.json"))
    meta_json = meta_candidates[0] if meta_candidates else None
    return markdown, meta_json


def normalize_marker_markdown(text: str) -> str:
    import re

    lines = text.splitlines()
    out: list[str] = []
    for line in lines:
        match = re.fullmatch(r"\s*\((\d{1,3})\)\s*", line)
        if not match or not out:
            out.append(line)
            continue

        end = len(out) - 1
        while end >= 0 and not out[end].strip():
            end -= 1
        if end < 0 or not out[end].rstrip().endswith("$$"):
            out.append(line)
            continue

        start = end
        if not out[start].lstrip().startswith("$$"):
            while start >= 0 and not out[start].lstrip().startswith("$$"):
                start -= 1
        if start < 0:
            out.append(line)
            continue

        block = "\n".join(out[start : end + 1])
        if r"\tag{" in block:
            out.append(line)
            continue

        tag = match.group(1)
        prefix = out[end].rsplit("$$", 1)[0].rstrip()
        out[end] = f"{prefix}\n\\tag{{{tag}}}\n$$"

    trailing_newline = "\n" if text.endswith("\n") else ""
    return "\n".join(out) + trailing_newline


def choose_torch_device(explicit: str | None = None) -> str | None:
    if explicit:
        return explicit
    try:
        import torch
    except Exception:
        return None
    if torch.cuda.is_available():
        return "cuda"
    return None


def run_marker_single(
    pdf: Path,
    output_dir: Path,
    *,
    page_range: str | None = None,
    use_llm: bool = False,
    force_ocr: bool = False,
    marker_bin: str | None = None,
    torch_device: str | None = None,
    keep_marker_output: bool = False,
) -> MarkerResult:
    marker = find_marker_single(marker_bin)
    output_dir.mkdir(parents=True, exist_ok=True)
    target = output_dir / f"{pdf.stem}.md"

    with tempfile.TemporaryDirectory(prefix="marker-", dir=str(output_dir)) as tmp:
        marker_root = Path(tmp)
        cmd = [
            marker,
            str(pdf),
            "--output_dir",
            str(marker_root),
            "--output_format",
            "markdown",
            "--disable_tqdm",
        ]
        if page_range:
            cmd.extend(["--page_range", page_range])
        if use_llm:
            cmd.append("--use_llm")
        if force_ocr:
            cmd.append("--force_ocr")

        env = os.environ.copy()
        selected_device = choose_torch_device(torch_device)
        if selected_device:
            env["TORCH_DEVICE"] = selected_device

        proc = subprocess.run(cmd, text=True, capture_output=True, env=env)
        if proc.returncode != 0:
            raise SystemExit(
                f"Marker failed for {pdf} ({proc.returncode})\n\nSTDOUT:\n{proc.stdout}\n\nSTDERR:\n{proc.stderr}"
            )

        markdown, meta_json = _marker_markdown_path(marker_root, pdf)
        marker_text = markdown.read_text(encoding="utf-8")
        target.write_text(normalize_marker_markdown(marker_text), encoding="utf-8")

        copied_meta = None
        if meta_json is not None:
            copied_meta = output_dir / f"{pdf.stem}_marker_meta.json"
            shutil.copy2(meta_json, copied_meta)

        if keep_marker_output:
            preserved = output_dir / f"{pdf.stem}_marker_raw"
            if preserved.exists():
                shutil.rmtree(preserved)
            shutil.copytree(markdown.parent, preserved)

    elapsed = None
    for line in reversed((proc.stdout + "\n" + proc.stderr).splitlines()):
        if "Total time:" in line:
            try:
                elapsed = float(line.rsplit("Total time:", 1)[1].strip())
            except ValueError:
                elapsed = None
            break

    return MarkerResult(
        pdf=pdf,
        markdown=markdown,
        meta_json=copied_meta,
        output_markdown=target,
        page_range=page_range,
        elapsed_seconds=elapsed,
    )


def convert_marker(
    input_arg: str,
    *,
    output_dir_arg: str | None = None,
    page_range: str | None = None,
    use_llm: bool = False,
    force_ocr: bool = False,
    marker_bin: str | None = None,
    torch_device: str | None = None,
    keep_marker_output: bool = False,
) -> dict:
    source = Path(input_arg).expanduser().resolve()
    pdfs = discover_pdfs(source)
    output_dir = Path(output_dir_arg).expanduser().resolve() if output_dir_arg else default_output_dir(source)
    output_dir.mkdir(parents=True, exist_ok=True)

    results = []
    for pdf in pdfs:
        result = run_marker_single(
            pdf,
            output_dir,
            page_range=page_range,
            use_llm=use_llm,
            force_ocr=force_ocr,
            marker_bin=marker_bin,
            torch_device=torch_device,
            keep_marker_output=keep_marker_output,
        )
        results.append(
            {
                "pdf": str(result.pdf),
                "output_markdown": str(result.output_markdown),
                "meta_json": str(result.meta_json) if result.meta_json else None,
                "page_range": result.page_range,
                "elapsed_seconds": result.elapsed_seconds,
                "status": "converted_marker",
            }
        )

    summary = {
        "backend": "marker",
        "input": str(source),
        "output_dir": str(output_dir),
        "count": len(results),
        "results": results,
    }
    (output_dir / "_conversion_summary.json").write_text(json.dumps(summary, indent=2, ensure_ascii=False), encoding="utf-8")
    return summary


def main(argv: Iterable[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Convert PDFs to Markdown using Marker OCR/layout/equation recognition.")
    parser.add_argument("input", help="PDF file or folder containing PDFs")
    parser.add_argument("--output-dir", help="Flat markdown output folder")
    parser.add_argument("--page-range", help="Optional Marker page range, e.g. 0-2")
    parser.add_argument("--use-llm", action="store_true", help="Enable Marker's configured LLM cleanup service")
    parser.add_argument("--force-ocr", action="store_true", help="Force OCR even when embedded text exists")
    parser.add_argument("--marker-bin", help="Path to marker_single executable")
    parser.add_argument("--torch-device", help="Force Marker/Surya device, e.g. cuda or cpu")
    parser.add_argument("--keep-marker-output", action="store_true", help="Preserve raw Marker output folders next to markdown")
    args = parser.parse_args(argv)

    summary = convert_marker(
        args.input,
        output_dir_arg=args.output_dir,
        page_range=args.page_range,
        use_llm=args.use_llm,
        force_ocr=args.force_ocr,
        marker_bin=args.marker_bin,
        torch_device=args.torch_device,
        keep_marker_output=args.keep_marker_output,
    )
    print(json.dumps(summary, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
