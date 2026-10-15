"""Score explicitly reviewed PDF regions, separately from derivative agreement."""
from __future__ import annotations

from collections import Counter
from difflib import SequenceMatcher
from pathlib import Path
import re

from pdf_md_convert.reference_validation import (
    Block, compare_parsed, digest, edit_distance, math_tokens, normalize_text, parse_markdown, read_json,
)


def nearest_text(expected: str, page_text: str) -> str:
    """Align a bounded passage without dropping omitted words from its denominator."""
    target = normalize_text(expected).split(); words = normalize_text(page_text).split()
    if not target or not words: return ""
    match = SequenceMatcher(None, target, words, autojunk=False).find_longest_match()
    if match.size < min(3, len(target)): return ""
    start = max(0, match.b - match.a)
    candidates = [words[max(0,start+delta):max(0,start+delta)+len(target)+extra]
                  for delta in range(-2,3) for extra in range(-2,3) if len(target)+extra > 0]
    return " ".join(min(candidates, key=lambda c:edit_distance(target,c)))


def score_regions(regions: list[dict], markdown: str) -> dict:
    parsed = parse_markdown(markdown)
    pages = {int(m[1]): m[2] for m in re.finditer(r"\*\(Page (\d+)\)\*([\s\S]*?)(?=\*\(Page \d+\)\*|\Z)", markdown)}
    metrics = {k:{"units":0,"errors":0,"regions":0,"exact_regions":0} for k in ["text","equation","table"]}
    details = []
    for region in regions:
        kind = region["kind"]; expected = region["expected"]; metric = metrics[kind]
        candidate = ""
        if kind == "text":
            candidate = nearest_text(expected, pages.get(region["page"], ""))
            left = normalize_text(expected).split(); right = normalize_text(candidate).split()
        elif kind == "equation":
            matches = [b for b in parsed.blocks if b.kind == kind and b.key == region.get("key")]
            if len(matches) == 1: candidate = matches[0].text
            left = math_tokens(expected); right = math_tokens(candidate)
        else:
            matches = [b for b in parsed.blocks if b.kind == kind and b.key == region.get("key")]
            left = [f"{i}:{j}:{normalize_text(v)}" for i,row in enumerate(expected) for j,v in enumerate(row)]
            right = []
            if len(matches) == 1:
                cells = matches[0].cells
                selection = region.get("selection")
                if selection:
                    r0,r1,c0,c1 = selection
                    cells = [row[c0:c1] for row in cells[r0:r1]]
                right = [f"{i}:{j}:{normalize_text(v)}" for i,row in enumerate(cells) for j,v in enumerate(row)]
                candidate = cells
        errors = edit_distance(left,right)
        if kind == "equation":
            for variant in region.get("equivalent_forms", []):
                errors = min(errors, edit_distance(math_tokens(variant), right))
        metric["units"] += len(left); metric["errors"] += errors
        metric["regions"] += 1; metric["exact_regions"] += errors == 0
        details.append({"id":region["id"],"kind":kind,"page":region["page"],"errors":errors,"units":len(left),"candidate":candidate})
    for m in metrics.values(): m["error_rate"] = m["errors"]/m["units"] if m["units"] else None
    return {"metrics":metrics,"regions":details}


def evaluate_suite(path: Path, records: list[dict], run_root: Path, has_baseline: bool) -> dict:
    suite = read_json(path); results = []; issues = []
    for record in records:
        reviewed = []
        for r in suite.get("regions", []):
            if r["source_sha256"] != record["sha256"]: continue
            crop = (path.parent / r.get("crop", ""))
            if not r.get("verified") or not r.get("review_note") or not crop.is_file() or digest(crop) != r.get("crop_sha256"):
                issues.append(f"Unverified source region: {r['id']}"); continue
            reviewed.append(r)
        counts = Counter(r["kind"] for r in reviewed)
        unavailable = suite.get("unavailable", {}).get(record["sha256"], {})
        for kind,minimum in {"text":4,"equation":3,"table":2}.items():
            if counts[kind] < minimum and not unavailable.get(kind):
                issues.append(f"{record['id']}: only {counts[kind]} reviewed {kind} regions")
        for label in ["baseline","candidate"] if has_baseline else ["candidate"]:
            md = run_root / f"{label}_1" / record["id"] / (Path(record["source"]).stem + ".md")
            if not md.exists(): issues.append(f"Missing conversion: {md}"); continue
            results.append({"id":record["id"],"version":label,**score_regions(reviewed,md.read_text(encoding="utf-8"))})
    totals = {}
    for label in ["baseline","candidate"]:
        totals[label] = {}
        for kind in ["text","equation","table"]:
            errors = sum(r["metrics"][kind]["errors"] for r in results if r["version"] == label)
            units = sum(r["metrics"][kind]["units"] for r in results if r["version"] == label)
            totals[label][kind] = {"errors":errors,"units":units,"error_rate":errors/units if units else None}
    gates = {}
    for kind in totals["candidate"]:
        a = totals["baseline"][kind]["error_rate"]; b = totals["candidate"][kind]["error_rate"]
        gates[kind] = a is not None and b is not None and (b <= .9*a if a else b == 0)
    return {"status":"incomplete" if issues else "passed" if all(gates.values()) else "failed",
            "coverage_issues":issues,"domain_gates":gates,"totals":totals,"results":results}
