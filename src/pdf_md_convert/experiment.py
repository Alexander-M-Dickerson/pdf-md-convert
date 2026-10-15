from __future__ import annotations

import argparse
import json
import random
import re
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

from pdf_md_convert.hybrid_backend import convert_hybrid, sha256_file, suspicious_pipe_rows


DEFAULT_ROOT = Path.cwd()
EXCLUDED_PARTS = {
    ".git",
    ".venv-marker",
    "_archive",
    "artifacts",
    "figures",
    "playground",
    "slides",
    "split",
    "split_pp112_160",
}


@dataclass(frozen=True)
class CandidatePdf:
    path: Path
    source_kind: str


def slugify(value: str) -> str:
    slug = re.sub(r"[^A-Za-z0-9]+", "-", value).strip("-").lower()
    return slug[:48] or "paper"


def excluded(path: Path) -> bool:
    parts = {part.lower() for part in path.parts}
    if parts & EXCLUDED_PARTS:
        return True
    lowered = path.name.lower()
    return any(token in lowered for token in ["chunk_pp", "_style", "_slides", "highlight", "premium_bar"])


def discover_candidates(roots: list[Path]) -> list[CandidatePdf]:
    candidates: dict[Path, CandidatePdf] = {}
    for root in roots:
        root = root.expanduser().resolve()
        if root.is_file() and root.suffix.lower() == ".pdf" and not excluded(root):
            candidates[root] = CandidatePdf(root, "direct-pdf")
            continue
        if not root.is_dir():
            continue
        for pdf in root.glob("*.pdf"):
            if not excluded(pdf):
                candidates[pdf.resolve()] = CandidatePdf(pdf.resolve(), "top-level-pdf")
        for base_name in ["papers", "articles"]:
            base = root / base_name
            if not base.exists():
                continue
            for pdf in base.rglob("manuscript.pdf"):
                if not excluded(pdf):
                    candidates[pdf.resolve()] = CandidatePdf(pdf.resolve(), f"{base_name}-manuscript")
    return sorted(candidates.values(), key=lambda item: str(item.path).lower())


def comparison_metrics(path: Path | None) -> dict:
    if path is None or not path.exists():
        return {}
    markdown_files = sorted(path.glob("*.md")) if path.is_dir() else [path]
    total_suspicious = 0
    total_files = 0
    for md in markdown_files:
        try:
            total_suspicious += suspicious_pipe_rows(md.read_text(encoding="utf-8", errors="replace"))
            total_files += 1
        except OSError:
            continue
    return {"comparison_markdown_files": total_files, "comparison_suspicious_pipe_rows": total_suspicious}


def run_experiment(
    roots: list[Path],
    *,
    output_dir: Path | None,
    count: int,
    seed: int | None,
    comparison_dir: Path | None,
    local_repair_tier: str,
    max_total_repair_seconds: float,
) -> dict:
    candidates = discover_candidates(roots)
    rng = random.Random(seed)
    sample = rng.sample(candidates, k=min(max(count, 0), len(candidates))) if candidates else []
    if output_dir is None:
        stamp = time.strftime("%Y%m%d-%H%M%S")
        output_dir = Path("outputs") / "experiments" / stamp
    output_dir = output_dir.resolve()
    output_dir.mkdir(parents=True, exist_ok=True)

    events_path = output_dir / "_experiment_events.jsonl"
    results: list[dict] = []
    started = time.perf_counter()
    for idx, candidate in enumerate(sample, start=1):
        paper_dir = output_dir / f"{idx:02d}_{slugify(candidate.path.parent.name if candidate.path.name == 'manuscript.pdf' else candidate.path.stem)}"
        summary = convert_hybrid(
            str(candidate.path),
            output_dir_arg=str(paper_dir),
            max_repair_pages=0,
            repair_equations=True,
            max_equation_crops=40,
            repair_tables=True,
            max_table_crops=35,
            time_budget=10.0,
            local_repair_tier=local_repair_tier,
            max_total_repair_seconds=max_total_repair_seconds,
        )
        batch_path = paper_dir / "_batch_qa_summary.json"
        batch = json.loads(batch_path.read_text(encoding="utf-8")) if batch_path.exists() else {}
        record = {
            "index": idx,
            "source": str(candidate.path),
            "source_kind": candidate.source_kind,
            "sha256": sha256_file(candidate.path),
            "output_dir": str(paper_dir),
            "summary": summary,
            "batch_qa": batch,
        }
        results.append(record)
        with events_path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")

    aggregate = {
        "roots": [str(root) for root in roots],
        "candidate_count": len(candidates),
        "sample_count": len(sample),
        "seed": seed,
        "output_dir": str(output_dir),
        "local_repair_tier": local_repair_tier,
        "max_total_repair_seconds": max_total_repair_seconds,
        "elapsed_seconds": time.perf_counter() - started,
        "comparison": comparison_metrics(comparison_dir),
        "results": results,
    }
    (output_dir / "_experiment_summary.json").write_text(
        json.dumps(aggregate, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    write_experiment_markdown(output_dir / "_experiment_summary.md", aggregate)
    return aggregate


def write_experiment_markdown(path: Path, summary: dict) -> None:
    lines = [
        "# PDF Conversion Experiment",
        "",
        f"- Roots: {', '.join(summary['roots'])}",
        f"- Candidates: {summary['candidate_count']}",
        f"- Sample: {summary['sample_count']}",
        f"- Seed: {summary['seed'] if summary['seed'] is not None else 'random'}",
        f"- Local repair tier: {summary['local_repair_tier']}",
        f"- Max repair seconds: {summary['max_total_repair_seconds']}",
        f"- Elapsed seconds: {summary['elapsed_seconds']:.2f}",
    ]
    if summary["comparison"]:
        lines.append(
            f"- Comparison suspicious pipe rows: {summary['comparison'].get('comparison_suspicious_pipe_rows', 0)}"
        )
    lines.extend(["", "## Papers", ""])
    for record in summary["results"]:
        totals = record.get("batch_qa", {}).get("totals", {})
        lines.append(
            "- "
            f"{record['index']}. `{record['source']}`: "
            f"{totals.get('pages', 0)} pages; "
            f"{totals.get('tables', 0)} tables; "
            f"{totals.get('equation_crops', 0)} equation crops; "
            f"{totals.get('high_confidence_equation_repairs', 0)} high-confidence equation repairs; "
            f"{totals.get('suspicious_pipe_rows', 0)} suspicious pipe rows"
        )
    path.write_text("\n".join(lines).rstrip() + "\n", encoding="utf-8")


def main(argv: Iterable[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Sample PDFs and benchmark hybrid equation/table repair.")
    parser.add_argument("roots", nargs="*", help="Root folders or PDFs. Defaults to the current directory.")
    parser.add_argument("--output-dir")
    parser.add_argument("--count", type=int, default=3)
    parser.add_argument("--seed", type=int)
    parser.add_argument("--comparison-dir")
    parser.add_argument(
        "--local-repair-tier",
        choices=["off", "deterministic", "marker-page"],
        default="deterministic",
    )
    parser.add_argument("--max-total-repair-seconds", type=float, default=30.0)
    args = parser.parse_args(argv)

    roots = [Path(root) for root in args.roots] if args.roots else [DEFAULT_ROOT]
    summary = run_experiment(
        roots,
        output_dir=Path(args.output_dir) if args.output_dir else None,
        count=args.count,
        seed=args.seed,
        comparison_dir=Path(args.comparison_dir) if args.comparison_dir else None,
        local_repair_tier=args.local_repair_tier,
        max_total_repair_seconds=max(args.max_total_repair_seconds, 0.0),
    )
    print(json.dumps(summary, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
