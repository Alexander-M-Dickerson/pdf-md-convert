from __future__ import annotations

import argparse
import sys
from typing import Iterable

from pdf_md_convert import __version__
from pdf_md_convert.convert_pdf import main as convert_main
from pdf_md_convert.cleanup_equations import main as cleanup_equations_main
from pdf_md_convert.experiment import main as experiment_main
from pdf_md_convert.hybrid_backend import main as hybrid_main
from pdf_md_convert.marker_backend import main as marker_main
from pdf_md_convert.validate_conversion import main as validate_main


def main(argv: Iterable[str] | None = None) -> int:
    argv = list(argv) if argv is not None else sys.argv[1:]
    # Benchmark-only dependencies stay outside the production import path.
    if argv and argv[0] == "compare-reference":
        from pdf_md_convert.reference_validation import main as compare_main
        return compare_main(argv[1:])
    if argv and argv[0] == "reference-index":
        from pdf_md_convert.benchmark import index_main
        return index_main(argv[1:])
    if argv and argv[0] == "prepare-review":
        from pdf_md_convert.review_pack import main as review_main
        return review_main(argv[1:])
    if argv and argv[0] == "experiment" and "--manifest" in argv:
        from pdf_md_convert.benchmark import benchmark_main
        return benchmark_main(argv[1:])
    parser = argparse.ArgumentParser(prog="pdf-md-convert")
    parser.add_argument("--version", action="version", version=f"pdf-md-convert {__version__}")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("compare-reference", help="Compare source content against an enriched reference manuscript")
    sub.add_parser("reference-index", help="Index reference manuscripts and freeze benchmark inputs")
    sub.add_parser("prepare-review", help="Prepare source images and unverified accuracy-reference drafts")

    convert = sub.add_parser("convert", help="Convert a PDF, paper folder, or folder of PDFs")
    convert.add_argument("input")
    convert.add_argument("--mode", choices=["fast", "balanced", "audit"], default="balanced")
    convert.add_argument("--workspace")
    convert.add_argument("--force", action="store_true")
    convert.add_argument("--output-dir")

    marker = sub.add_parser("marker", help="Convert PDFs with Marker OCR/layout/equation recognition")
    marker.add_argument("input")
    marker.add_argument("--output-dir")
    marker.add_argument("--page-range")
    marker.add_argument("--use-llm", action="store_true")
    marker.add_argument("--force-ocr", action="store_true")
    marker.add_argument("--marker-bin")
    marker.add_argument("--torch-device")
    marker.add_argument("--keep-marker-output", action="store_true")

    hybrid = sub.add_parser("hybrid", help="Fast-first conversion with residual page detection and capped repair")
    hybrid.add_argument("input")
    hybrid.add_argument("--output-dir")
    hybrid.add_argument("--repair-placement", choices=["inline", "appendix"], default="appendix")
    hybrid.add_argument("--max-repair-pages", type=int, default=0)
    hybrid.add_argument("--torch-device", default="cuda")
    hybrid.add_argument("--repair-equations", action="store_true")
    hybrid.add_argument("--max-equation-crops", type=int, default=10)
    hybrid.add_argument("--repair-tables", action="store_true")
    hybrid.add_argument("--max-table-crops", type=int, default=10)
    hybrid.add_argument("--time-budget", type=float, default=30.0)
    hybrid.add_argument("--fast-backend", choices=["pymupdf4llm", "pymupdf"], default="pymupdf")
    hybrid.add_argument("--local-repair-tier", choices=["off", "deterministic", "marker-page"], default="deterministic")
    hybrid.add_argument("--max-total-repair-seconds", type=float, default=30.0)

    experiment = sub.add_parser("experiment", help="Sample PDFs and benchmark hybrid equation/table repair")
    experiment.add_argument("roots", nargs="*")
    experiment.add_argument("--output-dir")
    experiment.add_argument("--count", type=int, default=3)
    experiment.add_argument("--seed", type=int)
    experiment.add_argument("--comparison-dir")
    experiment.add_argument("--local-repair-tier", choices=["off", "deterministic", "marker-page"], default="deterministic")
    experiment.add_argument("--max-total-repair-seconds", type=float, default=30.0)

    cleanup = sub.add_parser("cleanup-equations", help="Mechanically clean raw equation blocks")
    cleanup.add_argument("input")
    cleanup.add_argument("--flat-output-dir")
    cleanup.add_argument("--workspace")

    validate = sub.add_parser("validate", help="Validate a prepared conversion folder")
    validate.add_argument("folder")
    validate.add_argument("--json", action="store_true")

    args = parser.parse_args(argv)
    if args.command == "convert":
        forwarded = [args.input, "--mode", args.mode]
        if args.workspace:
            forwarded.extend(["--workspace", args.workspace])
        if args.force:
            forwarded.append("--force")
        if args.output_dir:
            forwarded.extend(["--output-dir", args.output_dir])
        return convert_main(forwarded)
    if args.command == "marker":
        forwarded = [args.input]
        if args.output_dir:
            forwarded.extend(["--output-dir", args.output_dir])
        if args.page_range:
            forwarded.extend(["--page-range", args.page_range])
        if args.use_llm:
            forwarded.append("--use-llm")
        if args.force_ocr:
            forwarded.append("--force-ocr")
        if args.marker_bin:
            forwarded.extend(["--marker-bin", args.marker_bin])
        if args.torch_device:
            forwarded.extend(["--torch-device", args.torch_device])
        if args.keep_marker_output:
            forwarded.append("--keep-marker-output")
        return marker_main(forwarded)
    if args.command == "hybrid":
        forwarded = [args.input, "--max-repair-pages", str(args.max_repair_pages), "--torch-device", args.torch_device]
        forwarded.extend(["--fast-backend", args.fast_backend])
        forwarded.extend(["--repair-placement", args.repair_placement])
        if args.repair_equations:
            forwarded.append("--repair-equations")
        if args.repair_tables:
            forwarded.append("--repair-tables")
        forwarded.extend(["--max-equation-crops", str(args.max_equation_crops), "--time-budget", str(args.time_budget)])
        forwarded.extend(["--max-table-crops", str(args.max_table_crops)])
        forwarded.extend(["--local-repair-tier", args.local_repair_tier])
        forwarded.extend(["--max-total-repair-seconds", str(args.max_total_repair_seconds)])
        if args.output_dir:
            forwarded.extend(["--output-dir", args.output_dir])
        return hybrid_main(forwarded)
    if args.command == "experiment":
        forwarded = [*args.roots]
        forwarded.extend(["--count", str(args.count)])
        if args.seed is not None:
            forwarded.extend(["--seed", str(args.seed)])
        if args.output_dir:
            forwarded.extend(["--output-dir", args.output_dir])
        if args.comparison_dir:
            forwarded.extend(["--comparison-dir", args.comparison_dir])
        forwarded.extend(["--local-repair-tier", args.local_repair_tier])
        forwarded.extend(["--max-total-repair-seconds", str(args.max_total_repair_seconds)])
        return experiment_main(forwarded)
    if args.command == "cleanup-equations":
        forwarded = [args.input]
        if args.workspace:
            forwarded.extend(["--workspace", args.workspace])
        if args.flat_output_dir:
            forwarded.extend(["--flat-output-dir", args.flat_output_dir])
        return cleanup_equations_main(forwarded)
    if args.command == "validate":
        forwarded = [args.folder]
        if args.json:
            forwarded.append("--json")
        return validate_main(forwarded)
    raise AssertionError(args.command)


if __name__ == "__main__":
    raise SystemExit(main())
