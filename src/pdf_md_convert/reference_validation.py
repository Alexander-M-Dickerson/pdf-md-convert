"""Offline fidelity comparison. Agreement with a derivative is not source accuracy.

The reference and source are read-only. Ambiguous alignment is reported, never
silently interpreted as a correct conversion. No OCR or model imports live here.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import time
import unicodedata
from collections import Counter
from dataclasses import asdict, dataclass, field
from difflib import SequenceMatcher
from pathlib import Path


def digest(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def read_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8-sig"))


def write_json(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def normalize_text(text: str) -> str:
    text = unicodedata.normalize("NFC", text)
    text = re.sub(r"\[([^\]]+)\]\([^)]*\)", r"\1", text)
    text = re.sub(r"\*\*([^*]+)\*\*", r"\1", text)
    text = re.sub(r"(?<!\w)_([^_]+)_(?!\w)", r"\1", text)
    for a, b in {"ﬁ": "fi", "ﬂ": "fl", "ﬀ": "ff", "ﬃ": "ffi", "ﬄ": "ffl", "−": "-"}.items():
        text = text.replace(a, b)
    return re.sub(r"\s+", " ", text).strip()


def math_tokens(text: str) -> list[str]:
    text = re.sub(r"\\tag\*?\{[^}]+\}", "", text)
    text = text.replace("$$", "").replace(r"\[", "").replace(r"\]", "")
    text = re.sub(r"\\(?:left|right)(?![A-Za-z])", "", text)
    text = re.sub(r"\\[,;! ]", "", text)
    from pdf_md_convert.math_geometry import GREEK, OPERATORS
    symbols = "|".join(sorted(set(GREEK.values()) | set(OPERATORS.values()), key=len, reverse=True))
    text = re.sub(r"(\\(?:" + symbols + r"))\{\}", r"\1 ", text)
    text = re.sub(r"\\[dt]frac\b", lambda _:r"\frac", text)
    text = re.sub(r"([_^])\{([A-Za-z0-9])\}", r"\1\2", text)
    # Token boundaries matter: do not collapse \\beta x into \\betax.
    return re.findall(r"\\[A-Za-z]+|\\.|[A-Za-z]|\d+(?:\.\d+)?|[^\s]", normalize_text(text))


def edit_distance(a: list[str], b: list[str]) -> int:
    previous = list(range(len(b) + 1))
    for i, left in enumerate(a, 1):
        row = [i]
        for j, right in enumerate(b, 1):
            row.append(min(row[-1] + 1, previous[j] + 1, previous[j - 1] + (left != right)))
        previous = row
    return previous[-1]


def identity(value: str) -> str:
    value = re.sub(r"[\s()]", "", value).strip(".:").upper()
    # Only whole Roman identifiers are aliases; A.I and 8a remain scoped.
    from pdf_md_convert.table_regions import ROMAN_TO_INT
    return str(ROMAN_TO_INT[value]) if value in ROMAN_TO_INT else value


@dataclass
class Block:
    kind: str
    text: str
    line: int
    end_line: int
    key: str = ""
    section: str = ""
    page: int | None = None
    cells: list[list[str]] = field(default_factory=list)


@dataclass
class Parsed:
    blocks: list[Block] = field(default_factory=list)
    excluded: list[dict] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


HEADING = re.compile(r"^\s{0,3}(#{1,6})\s+(.+?)\s*#*\s*$")
TABLE_HEADING = re.compile(r"^(?:Appendix\s+)?Table\s+([A-Za-z]*\.?\d+[A-Za-z]?|[IVX]+)\b", re.I)
SOURCE_START = re.compile(r"^(?:paper body|abstract|(?:\d+\.?|I\.)?\s*introduction)\b", re.I)
ENRICHMENT = re.compile(r"^(?:AI Reading Guide|Notation Glossary|Equation Quick.Reference|Core Claims|"
                        r"Suggested Reading Paths|Dataset Divergence Log|Reading Paths)\b", re.I)
EDITORIAL = re.compile(r"^\s*>?\s*(?:\*\*)?(?:Reading note|Takeaway|Conversion note|BIAS/GAP|"
                       r"AI commentary|Visual.estimation precision)\b", re.I)
PAGE = re.compile(r"^\s*\*?\(Page\s+(\d+)\)\*?\s*$", re.I)


def split_cells(line: str) -> list[str]:
    # Ignore escaped pipes, including inside TeX; keep genuinely empty cells.
    stripped = line.strip()
    if stripped.startswith("|"): stripped = stripped[1:]
    if stripped.endswith("|"): stripped = stripped[:-1]
    return [normalize_text(v.replace(r"\|", "|")) for v in re.split(r"(?<!\\)\|", stripped)]


def parse_markdown(text: str, annotations: dict | None = None) -> Parsed:
    """A line-preserving block parser for our manuscript dialect.

    Explicit exclusions override classification, not the source document itself.
    Unknown headings and ambiguous content are retained rather than discarded.
    """
    result = Parsed()
    lines = text.splitlines()
    overrides = (annotations or {}).get("exclude_ranges", [])
    section = ""; page = None; table_key = ""; panel = ""; figure_section = False
    guide = False; guide_level = 0; editorial_level = 0
    i = 0
    while i < len(lines):
        line = lines[i]; start = i
        override = next((r for r in overrides if r["start"] <= i + 1 <= r["end"]), None)
        if override:
            result.excluded.append({"line": i + 1, "reason": override.get("reason", "reviewed-enrichment")})
            i += 1; continue
        if "<!--" in line:
            # Strip only the comment, preserving source prose on either side.
            end = i
            while "-->" not in lines[end] and end + 1 < len(lines): end += 1
            joined = "\n".join(lines[i:end + 1])
            cleaned = re.sub(r"<!--[\s\S]*?(?:-->|$)", "", joined).strip()
            result.excluded.append({"line": i + 1, "end_line": end + 1, "reason": "metadata-comment"})
            if cleaned: result.blocks.append(Block("text", cleaned, i + 1, end + 1, section=section, page=page))
            i = end + 1; continue
        heading = HEADING.match(line)
        if heading:
            level, title = len(heading[1]), heading[2]
            if ENRICHMENT.match(title): guide = True; guide_level = level
            elif guide and SOURCE_START.match(title): guide = False
            elif guide and level <= guide_level and not re.match(r"^[IVX]+[.)]", title):
                guide = False
                result.warnings.append(f"Guide boundary inferred at line {i + 1}; inspect classification")
            if guide:
                result.excluded.append({"line": i + 1, "reason": "ai-guide"}); i += 1; continue
            if editorial_level and level <= editorial_level: editorial_level = 0
            if EDITORIAL.match(title): editorial_level = level
            if editorial_level:
                result.excluded.append({"line": i + 1, "reason": "editorial-section"}); i += 1; continue
            section = title
            if re.match(r"(?:Fig\.?|Figure)\s+\w", title, re.I): figure_section = True
            elif level <= 3: figure_section = False
            cap = TABLE_HEADING.match(title)
            if cap: table_key = identity(cap[1]); panel = ""
            elif re.match(r"Panel\b", title, re.I): panel = title
            else: table_key = ""; panel = ""
            result.blocks.append(Block("heading", title, i + 1, i + 1, section=section, page=page))
            i += 1; continue
        if guide or editorial_level:
            result.excluded.append({"line": i + 1, "reason": "ai-guide" if guide else "editorial-section"})
            i += 1; continue
        pm = PAGE.match(line)
        if pm: page = int(pm[1]); i += 1; continue
        if not line.strip() or re.fullmatch(r"\s*[-*_]{3,}\s*", line): i += 1; continue
        if EDITORIAL.match(line):
            while i < len(lines) and lines[i].strip() and not HEADING.match(lines[i]): i += 1
            result.excluded.append({"line": start + 1, "end_line": i, "reason": "editorial-note"}); continue
        if line.lstrip().startswith(("```", "~~~")):
            fence = line.strip()[:3]; i += 1
            while i < len(lines) and not lines[i].strip().startswith(fence): i += 1
            i = min(i + 1, len(lines))
            result.blocks.append(Block("code", "\n".join(lines[start:i]), start + 1, i, section=section, page=page)); continue
        if "$$" in line or line.strip().startswith(r"\["):
            delimiter = "$$" if "$$" in line else r"\]"
            closed = line.count("$$") >= 2 if delimiter == "$$" else r"\]" in line
            i += 1
            while i < len(lines) and not closed:
                closed = delimiter in lines[i]; i += 1
            raw = "\n".join(lines[start:i]); tag = re.search(r"\\tag\*?\{([^}]+)\}", raw)
            if not closed: result.warnings.append(f"Unclosed display equation at line {start + 1}")
            result.blocks.append(Block("equation", raw, start + 1, i, identity(tag[1]) if tag else "", section, page)); continue
        cap = TABLE_HEADING.match(line.strip())
        if cap and not re.search(r"\b(reports|shows|presents|see)\b", line, re.I):
            table_key = identity(cap[1]); panel = ""
        if line.strip().startswith("|") and line.strip().endswith("|"):
            cells = []
            while i < len(lines) and lines[i].strip().startswith("|"):
                row = split_cells(lines[i])
                if not all(re.fullmatch(r":?-+:?", v.replace(" ", "")) for v in row): cells.append(row)
                i += 1
            kind = "figure-table" if figure_section else "table"
            if figure_section:
                result.warnings.append(f"Figure-associated table at line {start+1}: source review required before cell scoring")
            result.blocks.append(Block(kind, "\n".join(lines[start:i]), start + 1, i,
                                       table_key + (":" + panel if panel else ""), section, page, cells)); continue
        i += 1
        while i < len(lines) and lines[i].strip() and not (
            HEADING.match(lines[i]) or PAGE.match(lines[i]) or lines[i].lstrip().startswith(("|", "$$", r"\[", "<!--"))
            or EDITORIAL.match(lines[i])
        ): i += 1
        result.blocks.append(Block("text", "\n".join(lines[start:i]), start + 1, i, section=section, page=page))
    return result


def similarity(a: Block, b: Block) -> float:
    if a.kind != b.kind: return 0.0
    if a.kind in {"equation", "table"} and a.key and b.key:
        if a.key != b.key: return 0.0
        return 1.0
    left = set(re.findall(r"\w+", normalize_text(a.text).lower()))
    right = set(re.findall(r"\w+", normalize_text(b.text).lower()))
    return len(left & right) / max(1, len(left | right))


def compare_parsed(candidate: Parsed, reference: Parsed) -> dict:
    metrics = {kind: {"reference_items": 0, "matched_items": 0, "missing_items": 0,
                      "ambiguous_items": 0, "units": 0, "errors": 0, "exact_items": 0}
               for kind in ["text", "equation", "table"]}
    findings = []; used = set(); positions = []
    text_pages = {}
    for idx,block in enumerate(candidate.blocks):
        if block.kind == "text": text_pages.setdefault(block.page, []).append((idx,block))
    streams = []
    for page,blocks in text_pages.items():
        words = []; origins = []
        for idx,block in blocks:
            tokens = normalize_text(block.text).split()
            words.extend(tokens); origins.extend([idx] * len(tokens))
        streams.append((page, words, origins, set(words)))
    used_tokens = set()
    for ref in reference.blocks:
        if ref.kind not in metrics: continue
        metric = metrics[ref.kind]; metric["reference_items"] += 1
        if ref.kind == "text":
            target = normalize_text(ref.text).split()
            ranked = sorted(enumerate(streams), key=lambda pair:len(set(target) & pair[1][3]), reverse=True)[:3]
            options = []
            for stream_idx,(_,words,origins,_) in ranked:
                match = SequenceMatcher(None,target,words,autojunk=False).find_longest_match()
                if match.size < min(3,len(target)): continue
                start = max(0,match.b-match.a)
                # Preserve paragraph wrapping differences without treating a whole
                # PDF page as one candidate paragraph or trusting page numbering.
                for shift in [-2,0,2]:
                    begin = max(0,start+shift)
                    stop = min(len(words),begin+len(target))
                    value = words[begin:stop]
                    errors = edit_distance(target,value)
                    if errors <= .65*max(1,len(target)):
                        occupied = {(stream_idx,i) for i in range(begin,stop)}
                        if len(occupied & used_tokens) > .4*max(1,len(occupied)): continue
                        options.append((errors,stream_idx,begin,stop,occupied))
            metric["units"] += len(target)
            if options:
                errors,si,begin,stop,occupied = min(options,key=lambda v:v[:4])
                _,words,origins,_ = streams[si]
                cand = candidate.blocks[origins[begin]]
                value = " ".join(words[begin:stop]); used_tokens.update(occupied)
                used.update(origins[begin:stop]); positions.append(origins[begin])
                metric["matched_items"] += 1; metric["exact_items"] += errors == 0
            else:
                errors = len(target); cand = None; value = ""; metric["missing_items"] += 1
            metric["errors"] += errors
            if errors:
                findings.append({"kind":"text","key":"","reference_line":ref.line,"reference_end_line":ref.end_line,
                                 "candidate_line":cand.line if cand else None,"page_hint":ref.page,
                                 "classification":"unresolved" if cand else "alignment-failure",
                                 "status":"different" if cand else "unmatched","units":len(target),"errors":errors,
                                 "reference":ref.text,"candidate":value,"priority":"high" if not cand else "normal"})
            continue
        scores = sorted(((similarity(ref, c), idx) for idx, c in enumerate(candidate.blocks)
                         if idx not in used and c.kind == ref.kind), reverse=True)
        threshold = 0.38 if ref.kind == "text" else 0.55
        matched = bool(scores and scores[0][0] >= threshold)
        ambiguous = matched and len(scores) > 1 and scores[0][0] - scores[1][0] < 0.04
        cand = candidate.blocks[scores[0][1]] if matched and not ambiguous else None
        if ref.kind == "equation":
            left = math_tokens(ref.text); right = math_tokens(cand.text) if cand else []
        elif ref.kind == "table":
            # Include row/column positions and blanks. Moving a value is an error.
            left = [f"{r}:{c}:{normalize_text(value)}" for r,row in enumerate(ref.cells) for c,value in enumerate(row)]
            right = [f"{r}:{c}:{normalize_text(value)}" for r,row in enumerate(cand.cells) for c,value in enumerate(row)] if cand else []
        else:
            left = normalize_text(ref.text).split(); right = normalize_text(cand.text).split() if cand else []
        errors = edit_distance(left, right)
        metric["units"] += len(left); metric["errors"] += errors
        if cand:
            metric["matched_items"] += 1; used.add(scores[0][1])
            metric["exact_items"] += errors == 0
            if ref.kind == "text": positions.append(scores[0][1])
        elif ambiguous: metric["ambiguous_items"] += 1
        else: metric["missing_items"] += 1
        if errors or not cand:
            findings.append({"kind": ref.kind, "key": ref.key, "reference_line": ref.line,
                             "reference_end_line": ref.end_line, "candidate_line": cand.line if cand else None,
                             "page_hint": ref.page, "classification": "alignment-failure" if not cand else "unresolved",
                             "status": "ambiguous" if ambiguous else "different" if cand else "unmatched",
                             "units": len(left), "errors": errors, "reference": ref.text,
                             "candidate": cand.text if cand else "",
                             "priority": "high" if ref.kind != "text" or not cand or re.findall(r"[-+]?\d+(?:\.\d+)?", ref.text) != re.findall(r"[-+]?\d+(?:\.\d+)?", cand.text) else "normal"})
    for kind, metric in metrics.items():
        metric["error_rate"] = metric["errors"] / metric["units"] if metric["units"] else None
        metric["unmatched_candidate_items"] = sum(c.kind == kind and idx not in used for idx,c in enumerate(candidate.blocks))
    return {"metrics": metrics, "findings": sorted(findings, key=lambda f:f["priority"] != "high"),
            "text_order_inversions": sum(a > b for i,a in enumerate(positions) for b in positions[i+1:]),
            "candidate_exclusions": candidate.excluded, "reference_exclusions": reference.excluded,
            "parser_warnings": candidate.warnings + reference.warnings}


def validate_provenance(pdf: Path, reference: Path, annotations: dict) -> dict:
    source_hash = digest(pdf); result = {"source_sha256": source_hash, "reference_sha256": digest(reference),
                                      "status": "unverified-pair", "limitations": ""}
    expected = annotations.get("source_sha256")
    manifest = reference.parent / "conversion_work" / "manifest.json"
    if manifest.exists(): expected = read_json(manifest).get("source_sha256", expected)
    if expected:
        result["status"] = "hash-matched" if expected == source_hash else "hash-mismatch"
    elif (reference.parent / "manuscript.pdf").exists():
        adjacent_hash = digest(reference.parent / "manuscript.pdf")
        result["status"] = "adjacent-source-matched" if adjacent_hash == source_hash else "hash-mismatch"
    if annotations.get("reference_sha256") and annotations["reference_sha256"] != result["reference_sha256"]:
        result["status"] = "stale-annotations"
    handover = reference.parent / "handover.md"
    if handover.exists():
        text = handover.read_text(encoding="utf-8-sig")
        m = re.search(r"^## Known limitations\s*\n([\s\S]*?)(?=^## |\Z)", text, re.M|re.I)
        result["limitations"] = m[1].strip() if m else ""
    return result


def compare_reference(candidate: Path, reference: Path, pdf: Path, output_dir: Path,
                      annotations: Path | None = None) -> dict:
    # Never allow the report directory to overwrite an input file.
    outputs = [output_dir / "comparison.json", output_dir / "comparison.md"]
    if any(p.resolve() in {candidate.resolve(), reference.resolve(), pdf.resolve()} for p in outputs):
        raise ValueError("Comparison output would overwrite an input")
    started = time.perf_counter(); notes = read_json(annotations) if annotations else {}
    provenance = validate_provenance(pdf, reference, notes)
    result = {"schema_version": 1, "measurement": "reference-agreement-not-source-accuracy",
              "candidate": str(candidate), "reference": str(reference), "pdf": str(pdf), "provenance": provenance}
    if provenance["status"] in {"hash-mismatch", "stale-annotations"}:
        result.update({"status": "blocked", "reason": provenance["status"]})
    else:
        ref_text = reference.read_text(encoding="utf-8-sig")
        # Reviewed corrections are a derivative, never edits to the reference.
        for correction in notes.get("corrections", []):
            if not correction.get("source_evidence") or ref_text.count(correction["old"]) != 1:
                raise ValueError("Correction requires source evidence and a unique old passage")
            ref_text = ref_text.replace(correction["old"], correction["new"], 1)
        result.update(compare_parsed(parse_markdown(candidate.read_text(encoding="utf-8-sig")), parse_markdown(ref_text, notes)))
        result["status"] = "review-required" if result["findings"] or result["parser_warnings"] else "agreement"
    result["elapsed_seconds"] = time.perf_counter() - started
    write_json(outputs[0], result)
    lines = ["# Reference comparison", "", f"Status: **{result['status']}**", "",
             "Agreement with an enriched derivative is not verified source accuracy.", "",
             f"Source: `{pdf}`", f"Reference: `{reference}`", "",
             f"Provenance: {provenance['status']}", "", "## Scores", ""]
    for kind, metric in result.get("metrics", {}).items(): lines.append(f"- {kind}: {json.dumps(metric)}")
    lines.extend(["", "## Reference limitations", "", provenance["limitations"] or "None recorded.", "",
                  "## Discrepancies", ""])
    for finding in result.get("findings", []):
        lines.extend([f"### {finding['kind']} {finding['key']} — reference line {finding['reference_line']}", "",
                      f"{finding['status']}; candidate line {finding['candidate_line']}; classification: {finding['classification']}",
                      "", "Reference:", "", "~~~~text", finding["reference"], "~~~~", "",
                      "Candidate:", "", "~~~~text", finding["candidate"] or "[unmatched]", "~~~~", ""])
    outputs[1].write_text("\n".join(lines) + "\n", encoding="utf-8")
    return result


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ["candidate", "reference", "pdf", "output-dir"]: parser.add_argument("--" + name, type=Path, required=True)
    parser.add_argument("--annotations", type=Path)
    args = parser.parse_args(argv)
    result = compare_reference(args.candidate, args.reference, args.pdf, args.output_dir, args.annotations)
    print(json.dumps({k:v for k,v in result.items() if k not in {"findings", "candidate_exclusions", "reference_exclusions"}}, indent=2))
    return 2 if result["status"] == "blocked" else 0
