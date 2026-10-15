#!/usr/bin/env python
"""One-command local PDF-to-Markdown conversion orchestrator."""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Iterable


PACKAGE_DIR = Path(__file__).resolve().parent
WORKSPACE = Path.cwd()
PYTHON = sys.executable


def run(args: list[str], check: bool = True) -> subprocess.CompletedProcess[str]:
    proc = subprocess.run(args, cwd=WORKSPACE, text=True, capture_output=True)
    if check and proc.returncode != 0:
        raise SystemExit(
            f"Command failed ({proc.returncode}): {' '.join(args)}\n\nSTDOUT:\n{proc.stdout}\n\nSTDERR:\n{proc.stderr}"
        )
    return proc


def parse_json(stdout: str, command_name: str) -> dict:
    payload = stdout.strip()
    if not payload.startswith("{"):
        start = payload.find("{")
        end = payload.rfind("}")
        if start != -1 and end != -1 and end > start:
            payload = payload[start : end + 1]
    try:
        return json.loads(payload)
    except json.JSONDecodeError as exc:
        raise SystemExit(f"{command_name} did not emit JSON:\n{stdout}") from exc


def is_paper_folder(path: Path) -> bool:
    return path.is_dir() and (path / "manuscript.pdf").exists()


def safe_output_dir(output_dir: Path) -> Path:
    out = output_dir.expanduser().resolve()
    out.mkdir(parents=True, exist_ok=True)
    return out


def convert_single(input_arg: str, mode: str, force: bool, output_dir: Path | None = None, output_name: str | None = None, workspace: Path | None = None) -> list[dict]:
    prepare_cmd = [
        PYTHON,
        str(PACKAGE_DIR / "prepare_workpack.py"),
        input_arg,
        "--mode",
        mode,
        "--workspace",
        str(workspace or (output_dir or Path.cwd()) / "_workpacks"),
    ]
    if force:
        prepare_cmd.append("--force")

    prepared = parse_json(run(prepare_cmd).stdout, "prepare_workpack.py")
    results = []

    for item in prepared["folders"]:
        folder = Path(item["folder"])
        run([PYTHON, str(PACKAGE_DIR / "draft_raw_chunks.py"), str(folder), "--handover", "--force"])
        run([PYTHON, str(PACKAGE_DIR / "assemble_markdown.py"), str(folder), "--force"])

        validation_proc = run(
            [PYTHON, str(PACKAGE_DIR / "validate_conversion.py"), str(folder), "--json"],
            check=False,
        )
        validation = parse_json(validation_proc.stdout, "validate_conversion.py")
        validation_path = folder / "conversion_work" / "validation.json"
        validation_path.write_text(json.dumps(validation, indent=2, ensure_ascii=False), encoding="utf-8")

        counts = validation["counts"]
        output_markdown = None
        if output_dir is not None:
            out_name = output_name or f"{folder.name}.md"
            output_markdown = output_dir / out_name
            shutil.copy2(folder / "manuscript.md", output_markdown)

        results.append(
            {
                "folder": str(folder),
                "manuscript": str(folder / "manuscript.md"),
                "output_markdown": str(output_markdown) if output_markdown else None,
                "handover": str(folder / "handover.md"),
                "validation_json": str(validation_path),
                "page_count": item["page_count"],
                "table_refs": item["table_refs"],
                "figure_refs": item["figure_refs"],
                "equation_like_lines": item["equation_like_lines"],
                "rendered_pages": item["rendered_pages"],
                "validation": counts,
                "status": "failed" if counts.get("fail", 0) else ("needs_semantic_refinement" if counts.get("warn", 0) else "validated"),
            }
        )

    return results


def convert(input_arg: str, mode: str, force: bool, output_dir_arg: str | None = None, workspace_arg: str | None = None) -> dict:
    workspace = Path(workspace_arg).expanduser().resolve() if workspace_arg else None
    source = Path(input_arg).expanduser().resolve()
    output_dir = safe_output_dir(Path(output_dir_arg)) if output_dir_arg else None

    if source.is_dir() and not is_paper_folder(source):
        pdfs = sorted(p for p in source.glob("*.pdf") if p.is_file())
        if not pdfs:
            raise SystemExit(f"No PDFs found in {source}")
        if output_dir is None:
            output_dir = safe_output_dir(source / f"{source.name}_md")
        results = []
        for pdf in pdfs:
            results.extend(convert_single(str(pdf), mode=mode, force=force, output_dir=output_dir, output_name=f"{pdf.stem}.md", workspace=workspace))
        return {"folders": results, "flat_output_dir": str(output_dir)}

    results = convert_single(str(source), mode=mode, force=force, output_dir=output_dir, workspace=workspace)
    return {"folders": results, "flat_output_dir": str(output_dir) if output_dir else None}


def main(argv: Iterable[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("input", help="Paper folder, direct PDF, or folder of PDFs")
    parser.add_argument("--mode", choices=["fast", "balanced", "audit"], default="balanced")
    parser.add_argument("--workspace")
    parser.add_argument("--force", action="store_true", help="Replace copied direct PDFs when content differs")
    parser.add_argument("--output-dir", help="For batch folders, write flat markdown files here using original PDF names")
    args = parser.parse_args(argv)

    summary = convert(args.input, mode=args.mode, force=args.force, output_dir_arg=args.output_dir, workspace_arg=args.workspace)
    print(json.dumps(summary, indent=2, ensure_ascii=False))
    failures = sum(folder["validation"].get("fail", 0) for folder in summary["folders"])
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())

