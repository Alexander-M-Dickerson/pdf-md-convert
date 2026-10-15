"""Frozen local corpora, reference registry, repeated conversion and accuracy gates."""
from __future__ import annotations

import argparse
import json
import os
import random
import re
import statistics
import subprocess
import sys
import hashlib
from pathlib import Path

import fitz

from pdf_md_convert.reference_validation import digest, read_json, write_json, compare_reference


def fingerprint(pdf: Path) -> set[tuple[str, ...]]:
    with fitz.open(pdf) as doc:
        words = re.findall(r"[a-z0-9]+", doc[0].get_text().lower())[:250] if len(doc) else []
    return set(zip(words, words[1:], words[2:]))


def similar(a: set, b: set) -> bool:
    return bool(a and b and len(a & b) / len(a | b) >= .65)


def build_registry(root: Path) -> dict:
    records = []; errors = []
    for kind in ["articles", "papers"]:
        for pdf in sorted((root / kind).rglob("manuscript.pdf")):
            md = pdf.with_suffix(".md")
            if not md.exists(): continue
            try:
                source_hash = digest(pdf); manifest = pdf.parent / "conversion_work" / "manifest.json"
                expected = read_json(manifest).get("source_sha256") if manifest.exists() else None
                records.append({"pdf": str(pdf.resolve()), "reference": str(md.resolve()),
                                "source_sha256": source_hash, "reference_sha256": digest(md),
                                "provenance": "hash-mismatch" if expected and expected != source_hash else
                                              "hash-matched" if expected else "adjacent-source-matched",
                                "handover": str(pdf.parent / "handover.md") if (pdf.parent / "handover.md").exists() else None,
                                "manifest": str(manifest) if manifest.exists() else None})
            except (OSError, ValueError) as exc: errors.append({"pdf": str(pdf), "error": str(exc)})
    return {"schema_version": 1, "root": str(root.resolve()), "records": records, "errors": errors}


def build_manifest(root: Path, registry: dict, history_roots: list[Path], count=20, paired_count=12,
                   seed=20261008, paired_seed=20261009) -> dict:
    from pdf_md_convert.experiment import discover_candidates
    history = set(); history_hashes = set(); errors = []
    for history_root in history_roots:
        for base, dirs, files in os.walk(history_root):
            dirs[:] = [d for d in dirs if not d.startswith(("marker-", "_equation", "_table", "_local_marker", "baseline_code"))]
            for name in files:
                if name not in {"_experiment_summary.json", "_hybrid_conversion_summary.json", "_conversion_summary.json"}: continue
                try: data = read_json(Path(base) / name)
                except (OSError, ValueError) as exc:
                    errors.append({"path": str(Path(base) / name), "error": str(exc)}); continue
                for record in data.get("results", []):
                    source = record.get("pdf", record.get("source"))
                    if source: history.add(Path(source))
                    if record.get("sha256"): history_hashes.add(record["sha256"])
        if history_root.name == "bond_close": history.update(history_root.glob("*.pdf"))
    history_fps = []
    for p in sorted(history):
        if p.is_file():
            try: history_hashes.add(digest(p)); history_fps.append(fingerprint(p))
            except (OSError, ValueError, RuntimeError) as exc: errors.append({"path": str(p), "error": str(exc)})
    by_hash = {}
    for record in registry["records"]:
        if record["provenance"] != "hash-mismatch": by_hash.setdefault(record["source_sha256"], []).append(record)
    unique = {}; excluded = []; candidates = discover_candidates([root])
    for candidate in candidates:
        try:
            h = digest(candidate.path); fp = fingerprint(candidate.path)
            reason = ""
            if h in history_hashes: reason = "previous-source-hash"
            elif h in unique: reason = "exact-duplicate"
            elif any(similar(fp, other) for other in history_fps): reason = "likely-previous-paper-version"
            elif any(similar(fp, other[1]) for other in unique.values()): reason = "likely-duplicate-paper"
            if reason:
                excluded.append({"source": str(candidate.path), "reason": reason}); continue
            unique[h] = (candidate, fp)
        except (OSError, ValueError, RuntimeError) as exc: errors.append({"path": str(candidate.path), "error": str(exc)})
    ordered = sorted(unique.items(), key=lambda x:str(x[1][0].path).lower())
    if len(ordered) < count: raise ValueError("Insufficient unique unused papers")
    general = random.Random(seed).sample(ordered, count); selected = {h for h,_ in general}
    paired_pool = [item for item in ordered if item[0] not in selected and item[0] in by_hash]
    if len(paired_pool) < paired_count: raise ValueError("Insufficient unused paired papers")
    paired = random.Random(paired_seed).sample(paired_pool, paired_count)
    records = []
    for cohort, items, dev_count in [("general", general, count * 3 // 5), ("paired", paired, paired_count * 2 // 3)]:
        for i,(h,(candidate,_)) in enumerate(items):
            refs = by_hash.get(h, [])
            with fitz.open(candidate.path) as doc: pages = len(doc)
            records.append({"id": h[:16], "source": str(candidate.path), "sha256": h, "pages": pages,
                            "source_kind": candidate.source_kind, "cohort": cohort,
                            "split": "development" if i < dev_count else "holdout",
                            "references": refs})
    return {"schema_version": 1, "root": str(root), "seed": seed, "paired_seed": paired_seed,
            "candidate_count": len(candidates), "eligible_groups": len(ordered), "records": records,
            "excluded": excluded, "errors": errors, "history_hashes": sorted(history_hashes)}


def index_main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Index read-only reference manuscripts and freeze random benchmark inputs")
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--history-root", type=Path, action="append", default=[])
    parser.add_argument("--count", type=int, default=20)
    parser.add_argument("--paired-count", type=int, default=12)
    args = parser.parse_args(argv)
    paths = [args.output_dir / "reference_registry.json", args.output_dir / "benchmark_manifest.json"]
    if any(p.exists() for p in paths): raise ValueError("Frozen catalog already exists; use a new output directory")
    registry = build_registry(args.root)
    manifest = build_manifest(args.root, registry, args.history_root, args.count, args.paired_count)
    write_json(paths[0], registry); write_json(paths[1], manifest)
    print(json.dumps({"pairs": len(registry["records"]), "sample": len(manifest["records"]),
                      "eligible_groups": manifest["eligible_groups"], "output": str(args.output_dir)}, indent=2))
    return 0


WORKER = r'''
import sys,json,time,inspect
from pathlib import Path
config=json.load(sys.stdin)
sys.path.insert(0,config['import_root'])
from pdf_md_convert.hybrid_backend import convert_hybrid
results=[]
for record in config['records']:
 out=Path(config['output_dir'])/record['id']
 options=dict(output_dir_arg=str(out),repair_equations=True,max_equation_crops=40,
              repair_tables=True,max_table_crops=35,time_budget=10.0,max_total_repair_seconds=30.0,
              max_repair_pages=0,local_repair_tier='deterministic')
 if 'repair_placement' in inspect.signature(convert_hybrid).parameters:
  options['repair_placement']=config['repair_placement']
 started=time.perf_counter()
 try:
  summary=convert_hybrid(record['source'],**options)
  results.append(dict(id=record['id'],seconds=time.perf_counter()-started,summary=summary))
 except Exception as exc:
  results.append(dict(id=record['id'],seconds=time.perf_counter()-started,error=repr(exc)))
Path(config['output_dir']).mkdir(parents=True,exist_ok=True)
(Path(config['output_dir'])/'run.json').write_text(json.dumps(results,indent=2),encoding='utf-8')
'''


def run_conversion(records: list[dict], code_root: Path, output: Path, placement: str) -> list[dict]:
    if output.exists(): raise ValueError(f"Timing output must be fresh: {output}")
    config = {"records": records, "code_root": str(code_root.resolve()), "output_dir": str(output.resolve()),
              "repair_placement": placement, "import_root": str((code_root / "src") if (code_root / "src" / "pdf_md_convert").is_dir() else code_root)}
    proc = subprocess.run([sys.executable, "-B", "-c", WORKER], input=json.dumps(config), text=True, capture_output=True)
    if proc.returncode: raise RuntimeError(f"Benchmark worker failed: {proc.stderr[-4000:]}")
    return json.loads((output / "run.json").read_text(encoding="utf-8"))


def timing_summary(runs: list[list[dict]]) -> dict:
    totals = [sum(r["seconds"] for r in run) for run in runs]
    per_paper = [statistics.median(run[i]["seconds"] for run in runs) for i in range(len(runs[0]))]
    # Nearest-rank p95, well-defined for small cohorts.
    import math
    ordered = sorted(per_paper)
    return {"batch_seconds": totals, "median_batch_seconds": statistics.median(totals),
            "p95_paper_seconds": ordered[max(0, math.ceil(.95*len(ordered))-1)],
            "per_paper_seconds": per_paper, "errors": [r for run in runs for r in run if "error" in r]}


def code_digest(root: Path) -> str:
    return hashlib.sha256(b"".join(p.name.encode() + p.read_bytes()
        for p in sorted(((root / "src" / "pdf_md_convert") if (root / "src" / "pdf_md_convert").is_dir() else root / "pdf_md_convert").glob("*.py")))).hexdigest()


def benchmark_main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--split", choices=["development", "holdout", "all"], default="development")
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--baseline-code", type=Path)
    parser.add_argument("--repeats", type=int, default=3)
    parser.add_argument("--repair-placement", choices=["inline", "appendix"], default="inline")
    parser.add_argument("--compare-references", action="store_true")
    parser.add_argument("--verified-references", type=Path)
    args = parser.parse_args(argv)
    if args.repeats < 1: raise ValueError("repeats must be positive")
    manifest = read_json(args.manifest)
    records = [r for r in manifest["records"] if args.split == "all" or r["split"] == args.split]
    if not records: raise ValueError("Empty benchmark split")
    for r in records:
        if digest(Path(r["source"])) != r["sha256"]: raise ValueError(f"Changed source: {r['source']}")
        for ref in r.get("references", []):
            if digest(Path(ref["reference"])) != ref["reference_sha256"]: raise ValueError("Reference changed; review and re-freeze")
    current = Path(__file__).resolve().parent.parent; candidate_runs = []; baseline_runs = []
    initial_code_hash = code_digest(current)
    for repeat in range(args.repeats):
        order = ["baseline", "candidate"] if repeat % 2 == 0 else ["candidate", "baseline"]
        for label in order:
            if label == "baseline" and not args.baseline_code: continue
            print(f"{label} repeat {repeat+1}: {len(records)} papers", flush=True)
            run = run_conversion(records, args.baseline_code if label == "baseline" else current,
                                 args.output_dir / f"{label}_{repeat+1}", args.repair_placement)
            (baseline_runs if label == "baseline" else candidate_runs).append(run)
    summary = {"manifest_sha256": digest(args.manifest), "split": args.split,
               "candidate": timing_summary(candidate_runs), "records": records,
               "accuracy_status": "not-verified", "promotion": "experimental"}
    summary["candidate_code_sha256"] = initial_code_hash
    summary["code_unchanged_gate"] = code_digest(current) == initial_code_hash
    coverage = []
    for r in records:
        report = args.output_dir / "candidate_1" / r["id"] / "_batch_qa_summary.json"
        if not report.exists():
            coverage.append({"id":r["id"],"error":"missing QA report"}); continue
        qa = read_json(report)
        coverage.append({"id":r["id"],"qa":qa})
    summary["coverage"] = coverage
    summary["conversion_error_gate"] = not summary["candidate"]["errors"] and all("error" not in r for r in coverage)
    if baseline_runs:
        summary["baseline"] = timing_summary(baseline_runs)
        summary["runtime_ratios"] = {key: summary["candidate"][key] / max(summary["baseline"][key], 1e-9)
                                     for key in ["median_batch_seconds", "p95_paper_seconds"]}
        summary["speed_gate"] = all(v <= 1.2 for v in summary["runtime_ratios"].values())
    if args.compare_references:
        comparisons = []
        for r in records:
            if not r.get("references"): continue
            for label in ["baseline", "candidate"] if baseline_runs else ["candidate"]:
                candidate = args.output_dir / f"{label}_1" / r["id"] / (Path(r["source"]).stem + ".md")
                if not candidate.exists(): continue
                result = compare_reference(candidate, Path(r["references"][0]["reference"]), Path(r["source"]),
                                           args.output_dir / "comparisons" / label / r["id"])
                comparisons.append({"id": r["id"], "version": label, "status": result["status"], "metrics": result.get("metrics", {})})
        summary["reference_agreement"] = comparisons
    if args.verified_references:
        from pdf_md_convert.verified_accuracy import evaluate_suite
        summary["verified_accuracy"] = evaluate_suite(args.verified_references, records, args.output_dir, bool(baseline_runs))
        summary["accuracy_status"] = summary["verified_accuracy"]["status"]
    write_json(args.output_dir / "benchmark_summary.json", summary)
    lines = ["# Conversion benchmark", "", f"Split: {args.split}; papers: {len(records)}; repeats: {args.repeats}.",
             "", "| Version | Median batch seconds | p95 paper seconds |", "|---|---:|---:|"]
    for label in ["baseline", "candidate"]:
        if label in summary:
            item = summary[label]
            lines.append(f"| {label} | {item['median_batch_seconds']:.3f} | {item['p95_paper_seconds']:.3f} |")
    lines += ["", f"Speed gate: {summary.get('speed_gate', 'no baseline')}; unchanged code: {summary['code_unchanged_gate']}.",
              f"Source-verified accuracy: {summary['accuracy_status']}. Promotion: experimental.", "",
              "Reference agreement is a diagnostic, not PDF ground truth. See benchmark_summary.json for per-paper coverage, errors and comparisons."]
    (args.output_dir / "benchmark_summary.md").write_text("\n".join(lines)+"\n", encoding="utf-8")
    print(json.dumps({k:v for k,v in summary.items() if k not in {"records", "coverage", "reference_agreement", "verified_accuracy"}}, indent=2))
    return 0
