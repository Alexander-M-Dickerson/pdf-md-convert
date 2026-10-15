from __future__ import annotations

import argparse
import hashlib
import json
import re
import time
from collections import Counter
from dataclasses import asdict, dataclass
from functools import partial
from pathlib import Path
from typing import Iterable

import fitz
from pdf_md_convert.layout import get_document, document_scope, layout_markdown, assemble_inline

from pdf_md_convert.equation_regions import (
    equation_repair_appendix,
    infer_referenced_equation_contexts,
    regions_to_jsonable,
    repair_equation_regions,
)
from pdf_md_convert.marker_backend import default_output_dir, discover_pdfs, run_marker_single
from pdf_md_convert.table_regions import (
    actual_table_caption_lines,
    detect_table_regions,
    infer_missing_table_placeholders,
    infer_referenced_table_contexts,
    regions_to_jsonable as table_regions_to_jsonable,
    table_repair_appendix,
    table_number_aliases,
)


MATH_GLYPHS = "αβγδεηθκλμρσφχψωΓΔΘΛΞΠΣΦΨΩ∑∫∞≤≥≠≈≡∝∂√"
MOJIBAKE_GLYPHS = "\ufffd\ufb01\ufb02âÃÂï¿½"
EQUATION_NUMBER_RE = re.compile(r"^\s*\(?\d{1,3}\)?\s*$")
TABLE_RE = re.compile(r"\b(Table|Panel|Dependent variable|Standard errors|Observations|Coefficient)\b", re.IGNORECASE)
UNREADABLE_GLYPH_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f]")
# Word endings that never stand alone as the second half of a compound.
BOUND_SUFFIXES = frozenset("""ing ings tion tions sion sions ment ments ness ity ities ance ances ence ences
    ous ive ively able ible ally ly ed ize izes ized ization ary ory ism ist ists ful""".split())
DAMAGED_PAGE_MIN_GLYPHS = 3


@dataclass(frozen=True)
class PageRisk:
    page: int
    score: int
    reasons: list[str]
    equation_numbers: list[str]
    text_chars: int


@dataclass(frozen=True)
class HybridResult:
    pdf: str
    output_markdown: str
    residuals_json: str
    report_markdown: str
    page_count: int
    fast_backend: str
    fast_seconds: float
    repair_seconds: float
    repaired_pages: list[int]
    equation_crops: int
    equation_repairs: int
    table_crops: int
    table_repairs: int
    high_risk_pages: list[int]
    warning_count: int


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def fix_text_artifacts(text: str) -> str:
    if text.isascii(): return text
    try:
        from ftfy import fix_text

        text = fix_text(text)
    except Exception:
        pass
    return text


def hyphenation_vocabulary(texts: Iterable[str]) -> Counter:
    """Count words and hyphenated compounds printed whole on one line.

    Halves of a word broken at a line end are counted separately, so they never
    vouch for the joined spelling.
    """
    counts = Counter()
    for text in texts:
        counts.update(token.casefold() for token in re.findall(r"[A-Za-z]+(?:-[A-Za-z]+)*", text))
    return counts


def normalize_fast_markdown(text: str, vocabulary: Counter | None = None) -> str:
    text = fix_text_artifacts(text)
    replacements = {
        "\ufb01": "fi",
        "\ufb02": "fl",
        "\u00ad": "",
    }
    for src, dst in replacements.items():
        text = text.replace(src, dst)
    # Fonts without a usable Unicode map yield control codes. Show the reader
    # that a glyph is unreadable instead of passing invisible characters on.
    text = UNREADABLE_GLYPH_RE.sub("�", text)
    # A printed hyphen can be lexical (risk-adjusted). Preserve it unless the
    # PDF marks it as a soft hyphen, the document itself prints the joined word
    # more often than the compound, or the second half is a bound suffix.
    def join(match: re.Match) -> str:
        left, right = match.group(1), match.group(2)
        if vocabulary is not None:
            joined, compound = vocabulary[(left + right).casefold()], vocabulary[f"{left}-{right}".casefold()]
            if joined > compound or (right in BOUND_SUFFIXES and not compound):
                return left + right
        return f"{left}-{right}"
    text = re.sub(r"([A-Za-z]+)-\n([a-z]+)", join, text)
    return text.rstrip() + "\n"


def fast_markdown(pdf: Path, backend: str = "pymupdf4llm") -> tuple[str, str]:
    if backend == "pymupdf":
        doc = get_document(pdf)
        pages = [f"*(Page {i + 1})*\n\n{page.get_text('text')}" for i, page in enumerate(doc)]
        return normalize_fast_markdown("\n\n".join(pages)), "pymupdf"

    try:
        import pymupdf4llm

        text = pymupdf4llm.to_markdown(str(pdf))
        if isinstance(text, list):
            text = "\n\n".join(str(item) for item in text)
        return normalize_fast_markdown(str(text)), "pymupdf4llm"
    except Exception as exc:
        if backend == "pymupdf4llm":
            markdown, fallback_backend = fast_markdown(pdf, backend="pymupdf")
            return markdown, f"{fallback_backend}-fallback:{exc.__class__.__name__}"
        raise


def page_risk(page_number: int, text: str) -> PageRisk:
    text = fix_text_artifacts(text)
    reasons: list[str] = []
    score = 0
    equation_numbers: list[str] = []
    for line in text.splitlines():
        m = re.fullmatch(r"\s*\(?(\d{1,3})\)?\s*", line)
        if not m:
            continue
        number = int(m.group(1))
        if 2 <= number <= 150:
            equation_numbers.append(str(number))

    equals = text.count("=")
    math_glyphs = sum(text.count(ch) for ch in MATH_GLYPHS)
    mojibake = sum(text.count(ch) for ch in MOJIBAKE_GLYPHS)
    line_equations = 0
    for line in text.splitlines():
        stripped = line.strip()
        if EQUATION_NUMBER_RE.fullmatch(stripped):
            continue
        if "=" in stripped and len(stripped) < 220 and not TABLE_RE.search(stripped):
            line_equations += 1

    if equation_numbers:
        score += min(16, 2 * len(equation_numbers))
        reasons.append(f"equation-number-candidates:{','.join(equation_numbers[:8])}")
    if line_equations:
        score += min(24, 4 * line_equations)
        reasons.append(f"short-lines-with-equals:{line_equations}")
    if math_glyphs:
        score += min(16, math_glyphs)
        reasons.append(f"math-glyphs:{math_glyphs}")
    if equals >= 3:
        score += min(6, equals)
        reasons.append(f"equals:{equals}")
    if mojibake:
        score += min(8, mojibake * 2)
        reasons.append(f"extraction-artifacts:{mojibake}")
    if TABLE_RE.search(text) and len(text.splitlines()) > 35:
        score += 8
        reasons.append("table-like-page")
    if len(text.strip()) < 300:
        score += 5
        reasons.append("low-text-page")

    return PageRisk(
        page=page_number,
        score=score,
        reasons=reasons,
        equation_numbers=equation_numbers[:20],
        text_chars=len(text),
    )


def detect_residuals(pdf: Path) -> tuple[int, list[PageRisk]]:
    doc = get_document(pdf)
    risks = [page_risk(i + 1, page.get_text("text")) for i, page in enumerate(doc)]
    risks = [risk for risk in risks if risk.score > 0]
    risks.sort(key=lambda r: (-r.score, r.page))
    return len(doc), risks


def page_range_for_marker(page: int) -> str:
    # Marker uses zero-based page ranges.
    zero_based = max(page - 1, 0)
    return f"{zero_based}-{zero_based}"


def merge_repair_appendix(markdown: str, repairs: list[tuple[int, str]]) -> str:
    if not repairs:
        return markdown
    parts = [markdown.rstrip(), "\n\n## Targeted Equation Repair Appendix\n"]
    for page, repair_md in repairs:
        parts.append(f"\n### Repaired Page {page}\n\n{repair_md.strip()}\n")
    return "\n".join(parts).rstrip() + "\n"


TABLE_REF_RE = re.compile(
    r"\bTable\s+((?:IA\.?\s*)?(?:[A-H]\.\d+|\d+[A-Za-z]|I{1,3}|IV|V|VI{0,3}|IX|X|XI{0,3}|XIV|XV|XVI{0,3}|XIX|XX|\d+|[A-Z]+\s*\d+|[A-Z]+\d+))\b",
    re.IGNORECASE,
)
SEPARATE_APPENDIX_RE = re.compile(
    r"\b(?:Internet|Online|Supplementary|Supplemental)\s+Appendix\b"
    r"|\bAppendix\s+[A-Z]?\.?\s*Supplementary\s+(?:data|material)"
    r"|\bSupplementary\s+(?:data|material)\s+(?:to|for|related\s+to|associated\s+with)\s+this\s+article",
    re.IGNORECASE,
)
EQUATION_REF_RE = re.compile(
    r"\b(?:Eqs?\.?|Equations?)\s*(?:\(?\s*((?:[A-Z]\.?)?\d{1,4}[a-z]?)(?!\w)\s*\)?(?:\s*(?:-|to|and)\s*\(?\s*((?:[A-Z]\.?)?\d{1,4}[a-z]?)(?!\w)\s*\)?)?)",
    re.IGNORECASE,
)


def normalize_ref_id(value: str) -> str:
    return re.sub(r"[^A-Z0-9]", "", value.upper())


def natural_ref_key(value: str) -> tuple:
    return tuple((0, int(v)) if v.isdigit() else (1, v) for v in re.split(r"(\d+)", value) if v)


def expand_equation_ref(start: str, end: str | None) -> list[str]:
    if not start.isdigit() or (end and not end.isdigit()):
        return [start] + ([end] if end else [])
    first = int(start)
    if not end:
        return [str(first)]
    last = int(end)
    if last < first or last - first > 20:
        return [str(first), str(last)]
    return [str(item) for item in range(first, last + 1)]


def is_bibliographic_table_reference(line: str, start: int, end: int) -> bool:
    before = line[max(0, start - 100) : start]
    after = line[end : end + 80]
    if re.search(r"Flow of Funds Accounts\s*\(\s*$", before, re.I):
        return True
    if re.search(r"(?:[A-Z][A-Za-z'-]+(?:\s+et\s+al\.)?|et\s+al\.)\s*\(\d{4}[a-z]?,\s*$", before):
        return True
    if re.search(r"\b(their|his|her)\b", before[-80:], re.IGNORECASE):
        return True
    if re.match(r"\s+of\s+[A-Z][A-Za-z'.-]+(?:\s+(?:and\s+)?[A-Z][A-Za-z'.-]+)*(?:\s+et\s+al\.)?", after):
        return True
    if not after.lstrip().startswith(";"):
        return False
    return bool(
        re.search(r"\b(19|20)\d{2}\b", before)
        or re.search(r"\b[A-Z][a-z]+,\s+[A-Z][a-z]+", before)
        or re.search(r"\bSample period\b", after, re.IGNORECASE)
    )


def is_internet_appendix_table_reference(line: str, start: int, end: int) -> bool:
    context = line[max(0, start - 120) : min(len(line), end + 140)]
    return bool(re.search(r"\b(?:Internet|online|supplementary) Appendix\b", context, re.IGNORECASE))


def is_caption_note_table_reference(line: str, start: int, end: int) -> bool:
    after = line[end : end + 180]
    return bool(
        re.match(r"\s*[.:]\s*Panel\s+[A-Z]\s+(reports|shows|presents)\b", after, re.IGNORECASE)
        or re.match(
            r"\s*[.:]\s*.*\b(standard errors?|statistical significance|shown in parentheses|clustered by)\b",
            after,
            re.IGNORECASE,
        )
    )


def is_parenthetical_example_table_reference(line: str, start: int, end: int) -> bool:
    before = line[max(0, start - 80) : start]
    after = line[end : min(len(line), end + 80)]
    example_context = bool(
        re.search(r"\(\s*e\.g\.,?\s+see\b", before, re.IGNORECASE)
        or re.search(r"\be\.g\.,?\s+see\s+(?:the\s+)?[\w\s-]{0,50}\bin\s*$", before, re.IGNORECASE)
    )
    return example_context and bool(re.search(r"^\s*\)", after))


def referenced_tables(markdown: str) -> list[str]:
    refs: set[str] = set()
    previous_line = ""
    for line in markdown.splitlines():
        context_line = f"{previous_line} {line}" if previous_line else line
        context_offset = len(previous_line) + 1 if previous_line else 0
        for match in TABLE_REF_RE.finditer(line):
            if is_bibliographic_table_reference(line, match.start(), match.end()):
                continue
            if is_caption_note_table_reference(line, match.start(), match.end()):
                continue
            if is_parenthetical_example_table_reference(
                context_line,
                context_offset + match.start(),
                context_offset + match.end(),
            ):
                continue
            ref = normalize_ref_id(match.group(1))
            if re.fullmatch(r"(INCLUDES|REPORTS|SHOWS|PRESENTS|CONTAINS|PROVIDES)\d+", ref):
                continue
            refs.add(ref)
        previous_line = line
    return sorted(ref for ref in refs if ref)


def referenced_internet_appendix_tables(markdown: str) -> list[str]:
    refs: set[str] = set()
    previous_line = ""
    lines = markdown.splitlines()
    for index, line in enumerate(lines):
        context_line = f"{previous_line} {line}" if previous_line else line
        context_offset = len(previous_line) + 1 if previous_line else 0
        if index + 1 < len(lines):
            context_line += " " + lines[index + 1]
        for match in TABLE_REF_RE.finditer(line):
            if is_bibliographic_table_reference(line, match.start(), match.end()):
                continue
            if is_caption_note_table_reference(line, match.start(), match.end()):
                continue
            if is_parenthetical_example_table_reference(
                context_line,
                context_offset + match.start(),
                context_offset + match.end(),
            ):
                continue
            if is_internet_appendix_table_reference(context_line, context_offset + match.start(), context_offset + match.end()):
                ref = normalize_ref_id(match.group(1))
                if re.fullmatch(r"(INCLUDES|REPORTS|SHOWS|PRESENTS|CONTAINS|PROVIDES)\d+", ref):
                    continue
                refs.add(ref)
        previous_line = line
    return sorted(ref for ref in refs if ref)


def referenced_equations(markdown: str) -> list[str]:
    refs: set[str] = set()
    for match in EQUATION_REF_RE.finditer(markdown):
        if re.match(r"\s+in\s+[A-Z]{2,8}\b", markdown[match.end():match.end()+40]):
            continue
        refs.update(expand_equation_ref(match.group(1), match.group(2)))
    return sorted(refs, key=natural_ref_key)


def suspicious_pipe_rows(markdown: str) -> int:
    suspicious = 0
    prose_re = re.compile(r"\b(Table|Equation|This table|reports|shows|presents|Figure)\b", re.IGNORECASE)
    for line in markdown.splitlines():
        stripped = line.strip()
        if not (stripped.startswith("|") and stripped.endswith("|")):
            continue
        cells = [cell.strip() for cell in stripped.strip("|").split("|")]
        if not cells or all(set(cell) <= {"-", ":", " "} for cell in cells if cell):
            continue
        first = cells[0]
        filled = sum(1 for cell in cells[1:] if cell)
        if len(first) > 110 or (filled <= 2 and prose_re.search(first)):
            suspicious += 1
    return suspicious


def build_conversion_qa(markdown: str, table_regions: list, equation_regions: list) -> dict:
    table_refs = referenced_tables(markdown)
    internet_appendix_table_refs = referenced_internet_appendix_tables(markdown)
    caption_numbers = [number for number, _ in actual_table_caption_lines(markdown)]
    extracted_numbers = [region.number for region in table_regions]
    extracted_tables = sorted({normalize_ref_id(number) for number in extracted_numbers})
    extracted_table_aliases = {
        alias for number in extracted_numbers for alias in table_number_aliases(number)
    }
    missing_table_refs_all = sorted(
        ref for ref in table_refs if not (table_number_aliases(ref) & extracted_table_aliases)
    )
    # When the paper says its appendix is published separately, appendix-lettered
    # tables with no caption anywhere in this PDF are external references.
    caption_aliases = {alias for number in caption_numbers for alias in table_number_aliases(number)}
    external_appendix = bool(SEPARATE_APPENDIX_RE.search(markdown))
    def is_external(ref: str) -> bool:
        return (ref.startswith("IA") or ref in internet_appendix_table_refs or (
            external_appendix and re.fullmatch(r"[A-H]\d+", ref) and not table_number_aliases(ref) & caption_aliases))
    missing_internet_appendix_tables = sorted(ref for ref in missing_table_refs_all if is_external(ref))
    missing_pdf_tables = sorted(ref for ref in missing_table_refs_all if not is_external(ref))
    equation_refs = referenced_equations(markdown)
    detected_equations = sorted({region.tag for region in equation_regions}, key=natural_ref_key)
    repaired_equations = sorted(
        {region.tag for region in equation_regions if region.confidence == "high" and region.latex},
        key=natural_ref_key,
    )
    return {
        "referenced_tables": table_refs,
        "detected_table_captions": caption_numbers,
        "table_states": {str(region.number): "reconstructed" if getattr(region, "extracted_text", "").lstrip().startswith("|") else "captured" for region in table_regions},
        "equation_states": {str(region.tag): "reconstructed" if region.confidence == "high" and region.latex else "captured" for region in equation_regions},
        "referenced_internet_appendix_tables": internet_appendix_table_refs,
        "extracted_tables": extracted_tables,
        "missing_referenced_tables": missing_pdf_tables,
        "missing_internet_appendix_tables": missing_internet_appendix_tables,
        "missing_referenced_tables_all": missing_table_refs_all,
        "referenced_equations": equation_refs,
        "detected_equation_crops": detected_equations,
        "high_confidence_equation_repairs": repaired_equations,
        "missing_referenced_equation_crops": sorted(
            set(equation_refs) - set(detected_equations),
            key=natural_ref_key,
        ),
        "missing_high_confidence_equation_repairs": sorted(
            set(equation_refs) - set(repaired_equations),
            key=natural_ref_key,
        ),
        "suspicious_pipe_rows": suspicious_pipe_rows(markdown),
        "damaged_text_pages": damaged_text_pages(markdown),
        "missing_crop_images": sorted(
            Path(crop).name
            for region in [*equation_regions, *table_regions]
            if (crop := getattr(region, "crop_path", "")) and not Path(crop).is_file()
        ),
    }


def damaged_text_pages(markdown: str) -> list[int]:
    """Pages whose text layer has unreadable glyphs; the source image is authoritative there."""
    pages = []
    for match in re.finditer(r"\*\(Page (\d+)\)\*(.*?)(?=\*\(Page \d+\)\*|\Z)", markdown, re.S):
        if match.group(2).count("�") >= DAMAGED_PAGE_MIN_GLYPHS:
            pages.append(int(match.group(1)))
    return pages


def write_report(
    pdf: Path,
    output_markdown: Path,
    residuals_json: Path,
    report_path: Path,
    *,
    page_count: int,
    fast_backend: str,
    fast_seconds: float,
    repair_seconds: float,
    repaired_pages: list[int],
    equation_crops: int,
    equation_repairs: int,
    table_crops: int,
    table_repairs: int,
    risks: list[PageRisk],
    qa: dict | None = None,
) -> None:
    high = [risk for risk in risks if risk.score >= 18]
    lines = [
        f"# Conversion Smoke Report: {pdf.stem}",
        "",
        f"- Source PDF: `{pdf}`",
        f"- SHA256: `{sha256_file(pdf)}`",
        f"- Pages: {page_count}",
        f"- Fast backend: `{fast_backend}`",
        f"- Fast pass seconds: {fast_seconds:.2f}",
        f"- Repair seconds: {repair_seconds:.2f}",
        f"- Output markdown: `{output_markdown}`",
        f"- Residuals JSON: `{residuals_json}`",
        f"- High-risk pages: {', '.join(str(r.page) for r in high[:30]) or 'none'}",
        f"- Repaired pages: {', '.join(str(p) for p in repaired_pages) or 'none'}",
        f"- Equation crops rendered: {equation_crops}",
        f"- High-confidence equation repairs appended: {equation_repairs}",
        f"- Table crops rendered: {table_crops}",
        f"- Table entries appended: {table_repairs}",
        "",
        "## Top Residuals",
        "",
    ]
    for risk in risks[:20]:
        lines.append(f"- Page {risk.page}: score {risk.score}; {', '.join(risk.reasons)}")
    if not risks:
        lines.append("- No residual risks detected by the current heuristics.")
    if qa:
        lines.extend(
            [
                "",
                "## Reference Reconciliation",
                "",
                f"- Referenced tables: {', '.join(qa['referenced_tables']) or 'none'}",
                f"- Extracted tables: {', '.join(qa['extracted_tables']) or 'none'}",
                f"- Missing referenced tables: {', '.join(qa['missing_referenced_tables']) or 'none'}",
                f"- Missing Internet Appendix table references: {', '.join(qa.get('missing_internet_appendix_tables', [])) or 'none'}",
                f"- Referenced equations: {', '.join(qa['referenced_equations']) or 'none'}",
                f"- Detected equation crops: {', '.join(qa['detected_equation_crops']) or 'none'}",
                f"- Missing referenced equation crops: {', '.join(qa['missing_referenced_equation_crops']) or 'none'}",
                f"- Missing high-confidence equation repairs: {', '.join(qa['missing_high_confidence_equation_repairs']) or 'none'}",
            ]
        )
    lines.extend(
        [
            "",
            "## Interpretation",
            "",
            "- Fast markdown is the primary output.",
            "- Residual pages are candidates for targeted equation/table repair.",
            "- Marker should be run only on capped residual pages or future cropped equation regions, not on the full document.",
        ]
    )
    report_path.write_text("\n".join(lines).rstrip() + "\n", encoding="utf-8")


@document_scope
def convert_one_hybrid(
    pdf: Path,
    output_dir: Path,
    *,
    max_repair_pages: int = 0,
    repair_equations: bool = False,
    max_equation_crops: int = 10,
    repair_tables: bool = False,
    max_table_crops: int = 10,
    time_budget: float = 30.0,
    torch_device: str | None = "cuda",
    fast_backend: str = "pymupdf",
    local_repair_tier: str = "deterministic",
    max_total_repair_seconds: float = 30.0,
    repair_placement: str = "appendix",
) -> HybridResult:
    output_dir.mkdir(parents=True, exist_ok=True)
    output_markdown = output_dir / f"{pdf.stem}.md"
    residuals_json = output_dir / f"{pdf.stem}_residuals.json"
    report_markdown = output_dir / f"{pdf.stem}_conversion_report.md"

    fast_start = time.perf_counter()
    markdown, resolved_fast_backend = fast_markdown(pdf, backend=fast_backend)
    raw_markdown = markdown
    source_mapping = []
    normalize = normalize_fast_markdown
    if fast_backend == "pymupdf":
        document = get_document(pdf)
        normalize = partial(normalize_fast_markdown,
                            vocabulary=hyphenation_vocabulary(page.get_text("text") for page in document))
        markdown, source_mapping = layout_markdown(document, normalize)
    fast_seconds = time.perf_counter() - fast_start

    page_count, risks = detect_residuals(pdf)
    high = [risk for risk in risks if risk.score >= 18]

    repaired_pages: list[int] = []
    repairs: list[tuple[int, str]] = []
    equation_regions = []
    table_regions = []
    repair_start = time.perf_counter()
    repair_deadline = repair_start + max_total_repair_seconds
    local_marker_pages: list[int] = []

    def remaining_repair_budget() -> float:
        return max(0.0, max_total_repair_seconds - (time.perf_counter() - repair_start))

    if repair_equations:
        candidate_pages = list(dict.fromkeys([risk.page for risk in high] + list(range(1, page_count + 1))))
        equation_regions = repair_equation_regions(
            pdf,
            output_dir,
            candidate_pages,
            max_crops=max_equation_crops,
            time_budget=min(time_budget, remaining_repair_budget()),
            deadline=repair_deadline,
        )
        equation_regions.extend(
            infer_referenced_equation_contexts(
                pdf,
                output_dir,
                equation_regions,
                referenced_equations(markdown),
                deadline=repair_deadline,
            )
        )
    if repair_tables:
        table_pages = [risk.page for risk in high if any("table-like-page" in reason for reason in risk.reasons)]
        # Also scan all pages for actual Table captions; this is fast and catches table pages before prose-heavy pages.
        all_pages = list(dict.fromkeys(table_pages + list(range(1, page_count + 1))))
        table_regions = detect_table_regions(
            pdf,
            output_dir,
            all_pages,
            max_crops=max_table_crops,
            time_budget=min(time_budget, remaining_repair_budget()),
            deadline=repair_deadline,
        )
        table_regions.extend(
            infer_missing_table_placeholders(
                pdf,
                output_dir,
                table_regions,
                referenced_tables(markdown),
                deadline=repair_deadline,
            )
        )
        table_regions.extend(
            infer_referenced_table_contexts(
                pdf,
                output_dir,
                table_regions,
                referenced_tables(markdown),
                deadline=repair_deadline,
            )
        )
    if repair_equations and local_repair_tier == "marker-page" and remaining_repair_budget() >= 5.0:
        referenced = set(referenced_equations(markdown))
        repaired_tags = {region.tag for region in equation_regions if region.confidence == "high" and region.latex}
        hard_pages = list(
            dict.fromkeys(
                region.page
                for region in equation_regions
                if region.tag in referenced and region.tag not in repaired_tags and region.confidence != "high"
            )
        )
        for page in hard_pages[:3]:
            if remaining_repair_budget() < 5.0:
                break
            repair_dir = output_dir / "_local_marker_repairs" / pdf.stem / f"page_{page:03d}"
            result = run_marker_single(
                pdf,
                repair_dir,
                page_range=page_range_for_marker(page),
                torch_device=torch_device,
            )
            local_marker_pages.append(page)
            repairs.append((page, result.output_markdown.read_text(encoding="utf-8")))
    for risk in high[:max_repair_pages]:
        if remaining_repair_budget() <= 0:
            break
        repair_dir = output_dir / "_targeted_repairs" / pdf.stem / f"page_{risk.page:03d}"
        result = run_marker_single(
            pdf,
            repair_dir,
            page_range=page_range_for_marker(risk.page),
            torch_device=torch_device,
        )
        repaired_pages.append(risk.page)
        repairs.append((risk.page, result.output_markdown.read_text(encoding="utf-8")))
    repair_seconds = time.perf_counter() - repair_start

    qa = build_conversion_qa(markdown, table_regions, equation_regions)
    placement_events = []
    repaired_markdown = merge_repair_appendix(markdown, repairs)
    equation_appendix = equation_repair_appendix(equation_regions)
    if equation_appendix:
        repaired_markdown = repaired_markdown.rstrip() + "\n\n" + equation_appendix
    table_appendix = table_repair_appendix(table_regions)
    if table_appendix:
        repaired_markdown = repaired_markdown.rstrip() + "\n\n" + table_appendix
    audit_dir = output_dir / "_audit" / pdf.stem
    audit_dir.mkdir(parents=True, exist_ok=True)
    (audit_dir / "raw.md").write_text(raw_markdown, encoding="utf-8")
    (audit_dir / "repair_candidates.md").write_text(equation_appendix + "\n" + table_appendix, encoding="utf-8")
    if repair_placement == "inline" and source_mapping:
        repaired_markdown, placement_events = assemble_inline(source_mapping, equation_regions, table_regions, normalize)
        repaired_markdown += f"\n[Conversion audit and unresolved repair candidates](<_audit/{pdf.stem}/repair_candidates.md>)\n"
    (audit_dir / "source_map.json").write_text(json.dumps(source_mapping, ensure_ascii=False), encoding="utf-8")
    qa["suspicious_pipe_rows"] = suspicious_pipe_rows(repaired_markdown)
    output_markdown.write_text(repaired_markdown, encoding="utf-8")

    residual_payload = {
        "pdf": str(pdf),
        "page_count": page_count,
        "fast_backend": fast_backend,
        "fast_seconds": fast_seconds,
        "repair_seconds": repair_seconds,
        "max_repair_pages": max_repair_pages,
        "repair_equations": repair_equations,
        "max_equation_crops": max_equation_crops,
        "repair_tables": repair_tables,
        "max_table_crops": max_table_crops,
        "time_budget": time_budget,
        "local_repair_tier": local_repair_tier,
        "max_total_repair_seconds": max_total_repair_seconds,
        "local_marker_pages": local_marker_pages,
        "repair_placement": repair_placement,
        "placement_events": placement_events,
        "repair_budget_exhausted": remaining_repair_budget() <= 0,
        "repair_budget_overrun_seconds": max(0.0, repair_seconds - max_total_repair_seconds),
        "repaired_pages": repaired_pages,
        "equation_regions": regions_to_jsonable(equation_regions),
        "table_regions": table_regions_to_jsonable(table_regions),
        "qa": qa,
        "risks": [asdict(risk) for risk in risks],
    }
    residuals_json.write_text(json.dumps(residual_payload, indent=2, ensure_ascii=False), encoding="utf-8")
    write_report(
        pdf,
        output_markdown,
        residuals_json,
        report_markdown,
        page_count=page_count,
        fast_backend=resolved_fast_backend,
        fast_seconds=fast_seconds,
        repair_seconds=repair_seconds,
        repaired_pages=repaired_pages,
        equation_crops=len(equation_regions),
        equation_repairs=sum(1 for region in equation_regions if region.confidence == "high" and region.latex),
        table_crops=len(table_regions),
        table_repairs=len(table_regions),
        risks=risks,
        qa=qa,
    )

    return HybridResult(
        pdf=str(pdf),
        output_markdown=str(output_markdown),
        residuals_json=str(residuals_json),
        report_markdown=str(report_markdown),
        page_count=page_count,
        fast_backend=resolved_fast_backend,
        fast_seconds=fast_seconds,
        repair_seconds=repair_seconds,
        repaired_pages=repaired_pages,
        equation_crops=len(equation_regions),
        equation_repairs=sum(1 for region in equation_regions if region.confidence == "high" and region.latex),
        table_crops=len(table_regions),
        table_repairs=len(table_regions),
        high_risk_pages=[risk.page for risk in high],
        warning_count=len(high),
    )


def convert_hybrid(
    input_arg: str,
    *,
    output_dir_arg: str | None = None,
    max_repair_pages: int = 0,
    repair_equations: bool = False,
    max_equation_crops: int = 10,
    repair_tables: bool = False,
    max_table_crops: int = 10,
    time_budget: float = 30.0,
    torch_device: str | None = "cuda",
    fast_backend: str = "pymupdf",
    local_repair_tier: str = "deterministic",
    max_total_repair_seconds: float = 30.0,
    repair_placement: str = "appendix",
) -> dict:
    source = Path(input_arg).expanduser().resolve()
    pdfs = discover_pdfs(source)
    output_dir = Path(output_dir_arg).expanduser().resolve() if output_dir_arg else default_output_dir(source)
    started = time.perf_counter()
    results = [
        asdict(
            convert_one_hybrid(
                pdf,
                output_dir,
                max_repair_pages=max_repair_pages,
                repair_equations=repair_equations,
                max_equation_crops=max_equation_crops,
                repair_tables=repair_tables,
                max_table_crops=max_table_crops,
                time_budget=time_budget,
                torch_device=torch_device,
                fast_backend=fast_backend,
                local_repair_tier=local_repair_tier,
                max_total_repair_seconds=max_total_repair_seconds,
                repair_placement=repair_placement,
            )
        )
        for pdf in pdfs
    ]
    summary = {
        "backend": "hybrid-fast-first",
        "input": str(source),
        "output_dir": str(output_dir),
        "count": len(results),
        "total_seconds": time.perf_counter() - started,
        "local_repair_tier": local_repair_tier,
        "max_total_repair_seconds": max_total_repair_seconds,
        "results": results,
    }
    output_dir.mkdir(parents=True, exist_ok=True)
    (output_dir / "_hybrid_conversion_summary.json").write_text(
        json.dumps(summary, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    write_batch_qa(output_dir, summary)
    return summary


def write_batch_qa(output_dir: Path, summary: dict) -> None:
    papers: list[dict] = []
    totals = {
        "pdfs": summary["count"],
        "pages": 0,
        "tables": 0,
        "low_confidence_tables": 0,
        "tables_with_text_fallback": 0,
        "equation_crops": 0,
        "high_confidence_equation_repairs": 0,
        "missing_referenced_tables": 0,
        "missing_internet_appendix_tables": 0,
        "missing_referenced_equation_crops": 0,
        "missing_high_confidence_equation_repairs": 0,
        "suspicious_pipe_rows": 0,
        "local_marker_repairs": 0,
    }

    for result in summary["results"]:
        residual_path = Path(result["residuals_json"])
        if not residual_path.exists():
            continue
        data = json.loads(residual_path.read_text(encoding="utf-8"))
        table_regions = data.get("table_regions", [])
        equation_regions = data.get("equation_regions", [])
        qa = data.get("qa", {})
        low_tables = [region for region in table_regions if region.get("confidence") == "low"]
        fallback_tables = [
            region for region in table_regions if "text-table-coordinate-fallback" in region.get("reasons", [])
        ]
        paper = {
            "pdf": result["pdf"],
            "markdown": result["output_markdown"],
            "pages": result["page_count"],
            "fast_seconds": result["fast_seconds"],
            "repair_seconds": result["repair_seconds"],
            "tables": len(table_regions),
            "low_confidence_tables": len(low_tables),
            "tables_with_text_fallback": len(fallback_tables),
            "equation_crops": len(equation_regions),
            "high_confidence_equation_repairs": sum(
                1 for region in equation_regions if region.get("confidence") == "high" and region.get("latex")
            ),
            "missing_referenced_tables": qa.get("missing_referenced_tables", []),
            "missing_internet_appendix_tables": qa.get("missing_internet_appendix_tables", []),
            "missing_referenced_equation_crops": qa.get("missing_referenced_equation_crops", []),
            "missing_high_confidence_equation_repairs": qa.get("missing_high_confidence_equation_repairs", []),
            "suspicious_pipe_rows": qa.get("suspicious_pipe_rows", 0),
            "local_marker_repairs": len(data.get("local_marker_pages", [])),
            "high_risk_pages": result["high_risk_pages"],
        }
        papers.append(paper)
        totals["pages"] += paper["pages"]
        totals["tables"] += paper["tables"]
        totals["low_confidence_tables"] += paper["low_confidence_tables"]
        totals["tables_with_text_fallback"] += paper["tables_with_text_fallback"]
        totals["equation_crops"] += paper["equation_crops"]
        totals["high_confidence_equation_repairs"] += paper["high_confidence_equation_repairs"]
        totals["missing_referenced_tables"] += len(paper["missing_referenced_tables"])
        totals["missing_internet_appendix_tables"] += len(paper["missing_internet_appendix_tables"])
        totals["missing_referenced_equation_crops"] += len(paper["missing_referenced_equation_crops"])
        totals["missing_high_confidence_equation_repairs"] += len(paper["missing_high_confidence_equation_repairs"])
        totals["suspicious_pipe_rows"] += paper["suspicious_pipe_rows"]
        totals["local_marker_repairs"] += paper["local_marker_repairs"]

    batch = {
        "input": summary["input"],
        "output_dir": summary["output_dir"],
        "total_seconds": summary["total_seconds"],
        "totals": totals,
        "papers": papers,
    }
    (output_dir / "_batch_qa_summary.json").write_text(
        json.dumps(batch, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    write_batch_qa_markdown(output_dir / "_batch_qa_summary.md", batch)


def write_batch_qa_markdown(path: Path, batch: dict) -> None:
    totals = batch["totals"]
    lines = [
        "# Batch QA Summary",
        "",
        f"- Input: `{batch['input']}`",
        f"- Output: `{batch['output_dir']}`",
        f"- PDFs: {totals['pdfs']}",
        f"- Pages: {totals['pages']}",
        f"- Total seconds: {batch['total_seconds']:.2f}",
        f"- Tables extracted: {totals['tables']}",
        f"- Low-confidence tables: {totals['low_confidence_tables']}",
        f"- Text-fallback tables: {totals['tables_with_text_fallback']}",
        f"- Equation crops: {totals['equation_crops']}",
        f"- High-confidence equation repairs: {totals['high_confidence_equation_repairs']}",
        f"- Missing referenced tables: {totals['missing_referenced_tables']}",
        f"- Missing Internet Appendix table references: {totals['missing_internet_appendix_tables']}",
        f"- Missing referenced equation crops: {totals['missing_referenced_equation_crops']}",
        f"- Missing high-confidence equation repairs: {totals['missing_high_confidence_equation_repairs']}",
        f"- Suspicious pipe table rows: {totals['suspicious_pipe_rows']}",
        f"- Local Marker page repairs: {totals['local_marker_repairs']}",
        "",
        "## Attention Queue",
        "",
    ]
    queue = sorted(
        batch["papers"],
        key=lambda paper: (
            -len(paper["missing_referenced_tables"]),
            -len(paper["missing_referenced_equation_crops"]),
            -paper["low_confidence_tables"],
            -len(paper["high_risk_pages"]),
        ),
    )
    for paper in queue[:30]:
        name = Path(paper["pdf"]).stem
        issues = []
        if paper["missing_referenced_tables"]:
            issues.append(f"missing tables {', '.join(paper['missing_referenced_tables'][:8])}")
        if paper["missing_internet_appendix_tables"]:
            issues.append(f"external IA refs {', '.join(paper['missing_internet_appendix_tables'][:8])}")
        if paper["missing_referenced_equation_crops"]:
            issues.append(f"missing equation crops {', '.join(paper['missing_referenced_equation_crops'][:8])}")
        if paper["low_confidence_tables"]:
            issues.append(f"{paper['low_confidence_tables']} low-confidence tables")
        if paper["suspicious_pipe_rows"]:
            issues.append(f"{paper['suspicious_pipe_rows']} suspicious pipe rows")
        if not issues:
            issues.append("no major reference gaps")
        lines.append(f"- {name}: {'; '.join(issues)}")
    path.write_text("\n".join(lines).rstrip() + "\n", encoding="utf-8")


def main(argv: Iterable[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Fast-first PDF to Markdown conversion with residual detection.")
    parser.add_argument("input", help="PDF file or folder containing PDFs")
    parser.add_argument("--output-dir", help="Flat markdown output folder")
    parser.add_argument("--repair-placement", choices=["inline", "appendix"], default="appendix",
                        help="Inline is experimental until source-verified acceptance gates pass")
    parser.add_argument(
        "--max-repair-pages",
        type=int,
        default=0,
        help="Run Marker only on this many highest-risk pages per PDF. Default 0 avoids laptop heat.",
    )
    parser.add_argument("--torch-device", default="cuda", help="Device for capped Marker repair pages")
    parser.add_argument("--repair-equations", action="store_true", help="Render and append high-confidence equation crop repairs")
    parser.add_argument("--max-equation-crops", type=int, default=10, help="Maximum equation crops per PDF")
    parser.add_argument("--repair-tables", action="store_true", help="Render and append detected table crops and coordinate text")
    parser.add_argument("--max-table-crops", type=int, default=10, help="Maximum table crops per PDF")
    parser.add_argument("--time-budget", type=float, default=30.0, help="Equation repair time budget per PDF in seconds")
    parser.add_argument(
        "--local-repair-tier",
        choices=["off", "deterministic", "marker-page"],
        default="deterministic",
        help="Local hard-crop repair tier. marker-page runs capped local Marker page repair for hard referenced equations.",
    )
    parser.add_argument(
        "--max-total-repair-seconds",
        type=float,
        default=30.0,
        help="Total repair budget per PDF across deterministic and local Marker repairs.",
    )
    parser.add_argument(
        "--fast-backend",
        choices=["pymupdf4llm", "pymupdf"],
        default="pymupdf",
        help="Fast full-document backend. pymupdf is fastest; pymupdf4llm keeps better layout.",
    )
    args = parser.parse_args(argv)

    summary = convert_hybrid(
        args.input,
        output_dir_arg=args.output_dir,
        max_repair_pages=max(args.max_repair_pages, 0),
        repair_equations=args.repair_equations,
        max_equation_crops=max(args.max_equation_crops, 0),
        repair_tables=args.repair_tables,
        max_table_crops=max(args.max_table_crops, 0),
        time_budget=max(args.time_budget, 0.0),
        torch_device=args.torch_device,
        fast_backend=args.fast_backend,
        local_repair_tier=args.local_repair_tier,
        max_total_repair_seconds=max(args.max_total_repair_seconds, 0.0),
        repair_placement=args.repair_placement,
    )
    print(json.dumps(summary, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
