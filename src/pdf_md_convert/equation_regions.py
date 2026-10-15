from __future__ import annotations

import re
import time
import unicodedata
from contextlib import suppress
from dataclasses import asdict, dataclass
from pathlib import Path

import fitz
from pdf_md_convert.layout import get_document, expired
from pdf_md_convert.math_geometry import encode_math, balanced, is_prose_line, reconstruct


# Four-digit numbers are years or journal page numbers, not equation tags.
TAG_PATTERN = r"(?:[A-Z]\.?\d+|[1-9]\d{0,2})(?:[a-z])?"
TAG_RE = re.compile(rf"\(?({TAG_PATTERN})\)?")
STANDALONE_TAG_RE = re.compile(rf"^\s*[.;,]?\s*\(({TAG_PATTERN})\)\s*[.;,]?\s*$")
BARE_TAG_RE = re.compile(rf"^\s*[.;,]?\s*({TAG_PATTERN})\s*[.;,]?\s*$")
INLINE_TAG_RE = re.compile(rf"\(({TAG_PATTERN})\)\s*[.;,]?\s*$")
LEGACY_STANDALONE_TAG_RE = re.compile(
    r"^\s*[.;,]?\s*[\u0111\u00f0\u0110\u00d0]\s*([1-9]\d?|1[0-4]\d|150)\s*[\u0162\u00de\u0163\u00fe]\s*[.;,]?\s*$"
)
MATH_RE = re.compile(
    r"[=+\-*/\u00d7\u2212\u221d\u2192\u221e\u2211\u2202\u2208]|"
    r"[\u03b1-\u03c9\u0391-\u03a9]|\b(E|Pr|ln|log|exp|Cost|Log|Inv)\b|[\U0001d400-\U0001d7ff]"
)
CONTROL_RE = re.compile(r"[\x00-\x01\x03-\x08\x0b-\x1f]")
TABLEISH_RE = re.compile(r"\b(Table|Panel|Figure|Observations|Dependent|Coefficient|Standard errors?)\b", re.IGNORECASE)
URL_RE = re.compile(r"https?://\S+|ssrn\.com/\S+|abstract=\d+", re.IGNORECASE)
CODE_LISTING_RE = re.compile(r"\*{8,}|_{8,}|ERROR\s+FILTER|^\s*\*{2,}", re.IGNORECASE | re.MULTILINE)
PROSE_SENTENCE_RE = re.compile(
    r"\b(Furthermore|Following|This|The|We|Table|Figure|Panel|estimated|reported|sample)\b",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class EquationRegion:
    page: int
    tag: str
    bbox: list[float]
    raw_text: str
    crop_path: str
    latex: str | None
    confidence: str
    reasons: list[str]
    repair_source: str = "pymupdf-layout"
    quality_flags: list[str] | None = None


def block_text(block: tuple) -> str:
    return str(block[4])


def block_bbox(block: tuple) -> fitz.Rect:
    return fitz.Rect(block[:4])


def union_rect(rects: list[fitz.Rect], page_rect: fitz.Rect, pad: float = 4.0) -> fitz.Rect:
    rect = fitz.Rect(rects[0])
    for other in rects[1:]:
        rect |= other
    rect.x0 = max(page_rect.x0, rect.x0 - pad)
    rect.y0 = max(page_rect.y0, rect.y0 - pad)
    rect.x1 = min(page_rect.x1, rect.x1 + pad)
    rect.y1 = min(page_rect.y1, rect.y1 + pad)
    return rect


def tag_from_text(text: str, *, allow_lone_bare: bool = False) -> str | None:
    """Return a display-equation tag, or None.

    An unparenthesized number is usually a page number, section number or table
    cell. It counts only as the final line after display math in the same block,
    or, with ``allow_lone_bare``, as a lone block the caller has aligned beside an
    equation.
    """
    lines = [line for line in text.splitlines() if line.strip()]
    for position, line in enumerate(reversed(lines)):
        leading = re.match(rf"^\s*\(({TAG_PATTERN})\)\s+(.+)", line)
        if leading and (MATH_RE.search(leading[2]) or re.match(r"[A-Za-z]\s*\([^)]{1,20}\)", leading[2])):
            return leading[1]
        match = STANDALONE_TAG_RE.fullmatch(line) or LEGACY_STANDALONE_TAG_RE.fullmatch(line)
        if match:
            if URL_RE.search(text) and math_signal(URL_RE.sub("", text)) < 2:
                continue
            return match.group(1)
        if position == 0 and (bare := BARE_TAG_RE.fullmatch(line)):
            body = URL_RE.sub("", "\n".join(lines[:-1]))
            if (body.strip() and math_signal(body) >= 2) or (not body.strip() and allow_lone_bare):
                return bare.group(1)
        if MATH_RE.search(line) and not URL_RE.search(line) and (
            match := INLINE_TAG_RE.search(line.strip())
        ):
            return match.group(1)
    return None


def math_signal(text: str) -> int:
    normalized = unicodedata.normalize("NFKC", text)
    score = 0
    score += 3 * normalized.count("=")
    score += 3 * normalized.count("\u221d")
    score += 2 * normalized.count("\u00d7")
    score += 2 * normalized.count("\u2192")
    score += 2 * normalized.count("\u221e")
    score += len(re.findall(r"[\u03b1-\u03c9\u0391-\u03a9]", normalized))
    # NFKC folds math alphanumerics to plain letters, so count them in the source.
    score += len(re.findall(r"[\U0001d400-\U0001d7ff]", text))
    score += len(re.findall(r"\b(E|Pr|ln|log|exp|Cost|Log|Inv)\b", normalized))
    return score


def is_math_block(text: str) -> bool:
    stripped = text.strip()
    if len(stripped) > 700:
        return False
    if TABLEISH_RE.search(stripped):
        return False
    if URL_RE.search(stripped) and math_signal(URL_RE.sub("", stripped)) < 2:
        return False
    return bool(MATH_RE.search(stripped) and math_signal(stripped) >= 2)


def is_prose_block(text: str) -> bool:
    # Unicode math italics give prose with inline symbols a math signal; the
    # number of ordinary words still separates paragraphs from displays.
    return len(re.findall(r"\b[A-Za-z]{3,}\b", text)) >= 10


def is_tagged_relation(text: str, tag: str) -> bool:
    """A self-contained display such as ``m(a) < m(b). (2)``: its own tag line and a relation."""
    if is_prose_block(text) or PROSE_SENTENCE_RE.search(text):
        return False
    if not re.search(rf"^\s*\({re.escape(tag)}\)\s*[.;,]?\s*$", text, re.M):
        return False
    body = " ".join(equation_body_lines(text, tag))
    return bool(re.search(r"\S\s*[=<>≤≥≈∝≡]\s*\S", body))


def same_equation_band(a: fitz.Rect, b: fitz.Rect) -> bool:
    vertical_overlap = min(a.y1, b.y1) - max(a.y0, b.y0)
    if vertical_overlap > 0:
        return True
    return abs(a.y0 - b.y0) < 10 or abs(a.y1 - b.y1) < 10


def find_equation_groups(page: fitz.Page) -> list[tuple[str, fitz.Rect, str]]:
    blocks = list(page.get_text("blocks"))
    groups: list[tuple[str, fitz.Rect, str]] = []
    used: set[int] = set()
    seen_tags: set[str] = set()

    for idx, block in enumerate(blocks):
        if idx in used:
            continue
        text = block_text(block)
        if not is_math_block(text) or is_prose_block(text):
            continue
        # An untagged lead-in sentence ("We write the gain as") must not swallow
        # the tagged display that follows it.
        if PROSE_SENTENCE_RE.search(text) and not tag_from_text(text):
            continue

        rects = [block_bbox(block)]
        texts = [text]
        tag = tag_from_text(text)

        for j in range(idx + 1, min(len(blocks), idx + 4)):
            other_text = block_text(blocks[j])
            other_rect = block_bbox(blocks[j])
            # A separate tag block sits in the equation's band, right of its centre.
            beside = same_equation_band(rects[0], other_rect) and other_rect.x0 > (rects[0].x0 + rects[0].x1) / 2
            other_tag = tag_from_text(other_text, allow_lone_bare=beside)
            if is_prose_block(other_text):
                continue
            if same_equation_band(rects[0], other_rect) or (not tag and other_tag):
                if is_math_block(other_text) or other_tag or len(other_text.strip()) < 80:
                    rects.append(other_rect)
                    texts.append(other_text)
                    used.add(j)
                    tag = tag or other_tag

        if tag and tag not in seen_tags:
            combined = "\n".join(texts)
            if URL_RE.search(combined) and math_signal(URL_RE.sub("", combined)) < 2:
                continue
            seen_tags.add(tag)
            groups.append((tag, union_rect(rects, page.rect), combined))
            used.add(idx)

    for idx, block in enumerate(blocks):
        text = block_text(block)
        tag = tag_from_text(text)
        if not tag or tag in seen_tags:
            continue
        tag_rect = block_bbox(block)
        rects = [tag_rect]
        texts = [text]
        for j in range(max(0, idx - 6), min(len(blocks), idx + 3)):
            if j == idx:
                continue
            other_text = block_text(blocks[j])
            other_rect = block_bbox(blocks[j])
            vertical_gap = max(other_rect.y0 - tag_rect.y1, tag_rect.y0 - other_rect.y1, 0)
            horizontally_near = other_rect.x0 < page.rect.x1 * 0.88
            if is_prose_block(other_text):
                continue
            if vertical_gap <= 95 and horizontally_near and (is_math_block(other_text) or math_signal(other_text) >= 2):
                rects.append(other_rect)
                texts.append(other_text)
        combined = "\n".join(texts)
        if URL_RE.search(combined) and math_signal(URL_RE.sub("", combined)) < 2:
            continue
        if (len(rects) >= 2 and math_signal(combined) >= 2) or (len(rects) == 1 and is_tagged_relation(text, tag)):
            seen_tags.add(tag)
            groups.append((tag, union_rect(rects, page.rect), combined))

    # Preserve unnumbered displays as distinct source regions. Prose and table
    # fragments remain excluded; reconstruction still requires geometry checks.
    occupied = [rect for _,rect,_ in groups]
    for i,block in enumerate(blocks):
        text = block_text(block); rect = block_bbox(block)
        if len(text.strip()) < 5 or len(text) > 200 or not is_math_block(text): continue
        # Without a tag, one "=" is weak evidence; function words make it prose.
        if PROSE_SENTENCE_RE.search(text) or is_prose_line(text): continue
        if any((rect & other).get_area() > .2*rect.get_area() for other in occupied): continue
        if text.count("=") == 1 and not re.search(r"[A-Za-z]{15,}|https?", text):
            groups.append((f"unnumbered-{i+1}", union_rect([rect], page.rect), text))
    return groups


def normalize_math_text(text: str) -> str:
    text = encode_math(text)
    text = text.replace("\ufb01", "fi").replace("\ufb02", "fl")
    text = text.replace("\u2212", "-").replace("\u2013", "-").replace("\u2014", "-")
    text = text.replace("\u00d7", r"\times")
    text = text.replace("\u221d", r"\propto")
    text = text.replace("\u2192", r"\to")
    text = text.replace("\u221e", r"\infty")
    text = text.replace("\u03bb", r"\lambda").replace("\u03b7", r"\eta").replace("\u03ba", r"\kappa")
    text = text.replace("\u03b1", r"\alpha").replace("\u03b2", r"\beta").replace("\u03b3", r"\gamma")
    text = text.replace("\u03bc", r"\mu").replace("\u03b5", r"\epsilon").replace("\u03c4", r"\tau")
    text = text.replace("\u03c3", r"\sigma").replace("\u03c1", r"\rho").replace("\u2208", r"\in")
    text = text.replace("\x02V", r"\widehat{V}")
    text = re.sub(r"\bE\s*\[", r"\\mathbb{E}[", text)
    text = re.sub(r"\\widehat\{V\}\s*(no|[boskr])\b", r"\\widehat{V}^{\1}", text)
    text = re.sub(r"\bM\s*(bid|ask)\b", r"M^{\1}", text)
    text = re.sub(r"\b([PUV])\s*(no|[boskr])\b", r"\1^{\2}", text)
    text = re.sub(r"\bP\s*([bs])\b", r"P^{\1}", text)
    text = re.sub(r"\bV\s*([boskr])\b", r"V^{\1}", text)
    text = re.sub(r"\bV\s*no\b", r"V^{no}", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text


def equation_body_lines(raw_text: str, tag: str) -> list[str]:
    lines = [line.strip() for line in raw_text.splitlines() if line.strip()]
    # Remove only the identified final tag. Naked numbers elsewhere may be
    # denominators, constants or limits, and must survive.
    for i in range(len(lines)-1, -1, -1):
        if re.fullmatch(rf"\(?\s*{re.escape(tag)}\s*\)?[.;,]?", lines[i]) or (
            (m := LEGACY_STANDALONE_TAG_RE.fullmatch(lines[i])) and m[1] == tag
        ):
            del lines[i]; break
        if re.search(rf"\({re.escape(tag)}\)[.;,]?\s*$", lines[i]):
            lines[i] = re.sub(rf"\s*\({re.escape(tag)}\)[.;,]?\s*$", "", lines[i]); break
    return [line for line in lines if line]


def false_positive_reasons(raw_text: str, tag: str) -> list[str]:
    body_lines = equation_body_lines(raw_text, tag)
    body = " ".join(body_lines)
    reasons: list[str] = []
    if CODE_LISTING_RE.search(raw_text):
        reasons.append("code-or-listing-block")
    if URL_RE.search(raw_text) and math_signal(URL_RE.sub("", raw_text)) < 4:
        reasons.append("url-or-footer-text")
    if PROSE_SENTENCE_RE.search(body) and body.count("=") <= 1 and len(body) > 90:
        reasons.append("prose-inline-equation")
    # Stacked fractions and sums also span many lines with one relation; a tag
    # on its own line marks them as displays, which stay as review crops.
    display_tag = re.search(rf"^\s*[.;,]?\s*\({re.escape(tag)}\)\s*[.;,]?\s*$", raw_text, re.M)
    if len(body_lines) >= 6 and body.count("=") <= 1 and not display_tag:
        reasons.append("fragmented-inline-equation")
    return reasons


def align_equation_line(line: str) -> str:
    normalized = normalize_math_text(line).rstrip(",.;")
    if "=" in normalized:
        left, right = normalized.split("=", 1)
        return f"{left.strip()} &= {right.strip()}"
    if r"\propto" in normalized:
        left, right = normalized.split(r"\propto", 1)
        return f"{left.strip()} &\\propto {right.strip()}"
    return normalized


def candidate_aligned_latex(body_lines: list[str], tag: str) -> str | None:
    math_lines = [line for line in body_lines if math_signal(line) >= 2 and not PROSE_SENTENCE_RE.search(line)]
    if len(math_lines) != len(body_lines) or any(CONTROL_RE.search(line) for line in body_lines):
        return None
    if any(not re.search(r"\S\s*(?:=|\u221d)\s*\S", line) for line in math_lines):
        return None
    if len(math_lines) < 2 or len(math_lines) > 4:
        return None
    relation_count = sum(line.count("=") + line.count("\u221d") for line in math_lines)
    if relation_count < 2:
        return None
    aligned = " \\\\\n".join(align_equation_line(line) for line in math_lines)
    return f"$$\n\\begin{{aligned}}\n{aligned}\n\\end{{aligned}}\n\\tag{{{tag}}}\n$$"


def candidate_latex(raw_text: str, tag: str) -> tuple[str | None, str, list[str]]:
    reasons: list[str] = []
    false_positive = false_positive_reasons(raw_text, tag)
    if false_positive:
        return None, "discard", false_positive

    body_lines = equation_body_lines(raw_text, tag)
    body = " ".join(body_lines)

    if CONTROL_RE.search(body):
        reasons.append("control-glyphs-from-stacked-layout")
    if re.search(r"https?://|ssrn|abstract=", body, re.IGNORECASE):
        reasons.append("url-or-footer-text")
    if len(body_lines) >= 2:
        reasons.append("multi-line-stacked-equation")
    if not balanced(body):
        reasons.append("unbalanced-structure")
    if not re.search(r"\S\s*(?:=|\u221d)\s*\S", body):
        reasons.append("incomplete-relation")
    if body.count("=") != 1 and "\u221d" not in body:
        reasons.append("not-single-equals-equation")

    aligned_latex = candidate_aligned_latex(body_lines, tag)
    if aligned_latex and not CONTROL_RE.search(body) and balanced(body):
        return aligned_latex, "high", ["aligned-multi-line-equation"]

    latex = normalize_math_text(body)
    confidence = "high"
    if reasons:
        confidence = "low"
    elif any(token in body for token in ["\n", "\x03", "\x04", "\x05", "\x06"]):
        confidence = "low"
        reasons.append("stacked-layout")

    if confidence != "high":
        return None, confidence, reasons
    return f"$$\n{latex}\n\\tag{{{tag}}}\n$$", confidence, reasons


def render_crop(page: fitz.Page, rect: fitz.Rect, crop_path: Path, dpi: int = 220) -> bool:
    """Write a crop image; a failed write (e.g. a Windows path-length limit) is not fatal."""
    try:
        crop_path.parent.mkdir(parents=True, exist_ok=True)
        pix = page.get_pixmap(matrix=fitz.Matrix(dpi / 72, dpi / 72), clip=rect, alpha=False)
        pix.save(crop_path)
        return crop_path.is_file()
    except Exception:
        with suppress(OSError):
            crop_path.unlink(missing_ok=True)
        return False


def artifact_stem(pdf: Path) -> str:
    return re.sub(r"[^A-Za-z0-9._-]+", "_", pdf.stem).strip("._-")[:40] or "paper"


def repair_equation_regions(
    pdf: Path,
    output_dir: Path,
    pages: list[int],
    *,
    max_crops: int,
    time_budget: float,
    deadline: float | None = None,
) -> list[EquationRegion]:
    started = time.perf_counter()
    doc = get_document(pdf)
    crop_root = output_dir / "_equation_crops" / artifact_stem(pdf)

    candidates = []
    for page_number in pages:
        if time.perf_counter() - started > time_budget or expired(deadline):
            break
        if 1 <= page_number <= len(doc):
            candidates.extend((page_number, *group) for group in find_equation_groups(doc[page_number - 1]))
    # Numbered displays can be cited, so they claim the crop budget before
    # unnumbered fragments; output keeps the page traversal order.
    order = sorted(range(len(candidates)), key=lambda i: candidates[i][1].startswith("unnumbered-"))
    taken: list[tuple[int, EquationRegion]] = []
    for index in order:
        page_number, tag, rect, raw_text = candidates[index]
        if len(taken) >= max_crops or time.perf_counter() - started > time_budget or expired(deadline):
            break
        page = doc[page_number - 1]
        latex, confidence, reasons = candidate_latex(raw_text, tag)
        if confidence == "discard":
            continue
        geometric, geometry_reasons = reconstruct(page, rect, tag)
        if geometric:
            suffix = "" if tag.startswith("unnumbered-") else f"\n\\tag{{{tag}}}"
            latex = f"$$\n{geometric}{suffix}\n$$"
            confidence = "high"
            reasons = geometry_reasons
        elif confidence == "high":
            # Text-only recognizers are locators, not semantic proof.
            latex = None
            confidence = "low"
            reasons = [*reasons, *geometry_reasons]
        crop_path = crop_root / f"p{page_number:03d}_eq{tag}.png"
        if not render_crop(page, rect, crop_path):
            reasons = [*reasons, "crop-write-failed"]
        taken.append((index, EquationRegion(
            page=page_number,
            tag=tag,
            bbox=[round(v, 2) for v in rect],
            raw_text=raw_text,
            crop_path=str(crop_path),
            latex=latex,
            confidence=confidence,
            reasons=reasons,
            quality_flags=reasons if confidence != "high" else [],
        )))
    return [region for _, region in sorted(taken, key=lambda item: item[0])]


def infer_referenced_equation_contexts(
    pdf: Path,
    output_dir: Path,
    existing_regions: list[EquationRegion],
    referenced_tags: list[str],
    *,
    max_contexts: int = 24,
    deadline: float | None = None,
) -> list[EquationRegion]:
    detected = {region.tag for region in existing_regions}
    missing = [tag for tag in referenced_tags if tag not in detected]
    if not missing:
        return []

    doc = get_document(pdf)
    crop_root = output_dir / "_equation_crops" / artifact_stem(pdf)
    contexts: list[EquationRegion] = []
    for tag in missing:
        if len(contexts) >= max_contexts or expired(deadline):
            break
        tag_pattern = re.compile(
            rf"(?:\({re.escape(tag)}\)|[\u0111\u00f0\u0110\u00d0]\s*{re.escape(tag)}\s*[\u0162\u00de\u0163\u00fe])"
        )
        reference_pattern = re.compile(rf"\b(?:Eq\.?|Equation|equation)\s*\(?\s*{re.escape(tag)}\s*\)?")
        best: tuple[int, fitz.Rect, str, list[str], int] | None = None
        for page_number, page in enumerate(doc, start=1):
            if expired(deadline): break
            blocks = list(page.get_text("blocks"))
            for idx, block in enumerate(blocks):
                text = block_text(block)
                has_tag_text, has_display_tag, has_reference = equation_context_flags(
                    text,
                    tag,
                    tag_pattern,
                    reference_pattern,
                )
                if not has_display_tag:
                    continue
                rects = [block_bbox(block)]
                texts = [text]
                for j in range(max(0, idx - 3), min(len(blocks), idx + 4)):
                    if j == idx:
                        continue
                    other_text = block_text(blocks[j])
                    other_rect = block_bbox(blocks[j])
                    if abs(other_rect.y0 - rects[0].y0) <= 100 or abs(other_rect.y1 - rects[0].y1) <= 100:
                        if is_math_block(other_text) or math_signal(other_text) >= 2:
                            rects.append(other_rect)
                            texts.append(other_text)
                combined = "\n".join(texts)
                if TABLEISH_RE.search(combined) and not has_display_tag:
                    continue
                score = (
                    math_signal(URL_RE.sub("", combined))
                    + (30 if has_display_tag else 0)
                    + (1 if has_reference else 0)
                )
                if best is None or score > best[4]:
                    best = (page_number, union_rect(rects, page.rect, pad=10.0), combined, [], score)
        if best is None:
            continue
        page_number, rect, raw_text, reasons, score = best
        if score < 4:
            reasons.append("weak-equation-context")
        reasons.extend(["referenced-equation-context-crop", "low-confidence-no-ocr"])
        crop_path = crop_root / f"p{page_number:03d}_eq{tag}_context.png"
        if not render_crop(doc[page_number - 1], rect, crop_path):
            reasons.append("crop-write-failed")
        contexts.append(
            EquationRegion(
                page=page_number,
                tag=tag,
                bbox=[round(v, 2) for v in rect],
                raw_text=raw_text,
                crop_path=str(crop_path),
                latex=None,
                confidence="low",
                reasons=reasons,
                repair_source="referenced-context",
                quality_flags=reasons,
            )
        )
        detected.add(tag)
    return contexts


def equation_context_flags(
    text: str,
    tag: str,
    tag_pattern: re.Pattern[str] | None = None,
    reference_pattern: re.Pattern[str] | None = None,
) -> tuple[bool, bool, bool]:
    tag_pattern = tag_pattern or re.compile(
        rf"(?:\({re.escape(tag)}\)|[\u0111\u00f0\u0110\u00d0]\s*{re.escape(tag)}\s*[\u0162\u00de\u0163\u00fe])"
    )
    reference_pattern = reference_pattern or re.compile(
        rf"\b(?:Eq\.?|Equation|equation)\s*\(?\s*{re.escape(tag)}\s*\)?"
    )
    has_tag_text = bool(tag_pattern.search(text))
    # The text cites this tag, so a lone bare number is admissible evidence here.
    has_display_tag = tag_from_text(text, allow_lone_bare=True) == tag
    has_reference = bool(reference_pattern.search(text))
    return has_tag_text, has_display_tag, has_reference


def regions_to_jsonable(regions: list[EquationRegion]) -> list[dict]:
    return [asdict(region) for region in regions]


def equation_repair_appendix(regions: list[EquationRegion]) -> str:
    high = [region for region in regions if region.confidence == "high" and region.latex]
    if not high:
        return ""
    lines = ["", "## Targeted Equation Repairs", ""]
    for region in high:
        lines.extend(
            [
                f"### Page {region.page}, Equation ({region.tag})",
                "",
                region.latex or "",
                "",
                f"<!-- crop: {region.crop_path} -->",
                "",
            ]
        )
    return "\n".join(lines).rstrip() + "\n"
