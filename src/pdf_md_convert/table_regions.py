from __future__ import annotations

import re
import time
from dataclasses import asdict, dataclass
from pathlib import Path

import fitz
from pdf_md_convert.layout import get_document, expired


ROMAN_TABLE_PATTERN = r"(?:I{1,3}|IV|V|VI{0,3}|IX|X|XI{0,3}|XIV|XV|XVI{0,3}|XIX|XX)"
NAMED_TABLE_PATTERN = r"(?:[A-Z]+\s*\d+)"
TABLE_ID_PATTERN = rf"(?:IA\.?{ROMAN_TABLE_PATTERN}|[A-Z]+\.\d+|{NAMED_TABLE_PATTERN}|[A-Z]+\d+|\d+[A-Z]|{ROMAN_TABLE_PATTERN}|\d+)"
TABLE_PREFIX_RE = r"(?:Appendix\s+)?Table"
TABLE_LINE_RE = re.compile(
    rf"^{TABLE_PREFIX_RE}\s+({TABLE_ID_PATTERN})(?:,\s*Panel\s+[A-Z])?\s*[:.]?\s*$",
    re.IGNORECASE,
)
TABLE_TITLED_LINE_RE = re.compile(
    rf"^{TABLE_PREFIX_RE}\s+({TABLE_ID_PATTERN})(?:\s*[:.]\s*|\s+[-\u2013\u2014]\s*|\s{{2,}}|,\s*Panel\s+[A-Z][.:]?\s*)(\S.+)$",
    re.IGNORECASE,
)
PROSE_RE = re.compile(
    r"\b(reports|presents|illustrates|shows|discussed|caption|following|estimate|estimates|whether|sample|relation)\b",
    re.IGNORECASE,
)
DATA_RE = re.compile(r"\bObservations\b|\bConstant\b|\bFixed\b|\bSD\b|\bMinimum\b", re.IGNORECASE)
HEADER_RE = re.compile(r"#|\(\d+\)|\b(Panel\s+[A-Z]:|Dependent Variable|All Trades|Model|Specification)\b", re.IGNORECASE)
NUMERIC_VALUE_PATTERN = r"[-+]?(?:\d[\d,]*(?:\.\d+)?|\.\d+)%?"
NUMERIC_RE = re.compile(
    rf"^\(?{NUMERIC_VALUE_PATTERN}\)?(?:\*+)?(?:/{NUMERIC_VALUE_PATTERN})?(?:\*+)?$"
)
COLUMN_TOKEN_RE = re.compile(r"^(?:#|\(\d+\)|\d+|N|R2|R\^2|Adj\.?\s*R2)$", re.IGNORECASE)
ROMAN_TO_INT = {
    "I": 1,
    "II": 2,
    "III": 3,
    "IV": 4,
    "V": 5,
    "VI": 6,
    "VII": 7,
    "VIII": 8,
    "IX": 9,
    "X": 10,
    "XI": 11,
    "XII": 12,
    "XIII": 13,
    "XIV": 14,
    "XV": 15,
    "XVI": 16,
    "XVII": 17,
    "XVIII": 18,
    "XIX": 19,
    "XX": 20,
}
INT_TO_ROMAN = {value: key for key, value in ROMAN_TO_INT.items()}


@dataclass(frozen=True)
class TableRegion:
    page: int
    number: str
    title: str
    bbox: list[float]
    crop_path: str
    confidence: str
    reasons: list[str]
    extracted_text: str
    note_text: str
    tsv_path: str
    markdown: str | None
    repair_source: str = "pymupdf-layout"
    quality_flags: list[str] | None = None
    body_bbox: list[float] | None = None
    cells: list[list[str]] | None = None


def clamp_rect(page: fitz.Page, rect: fitz.Rect) -> fitz.Rect:
    clamped = fitz.Rect(
        max(page.rect.x0, rect.x0),
        max(page.rect.y0, rect.y0),
        min(page.rect.x1, rect.x1),
        min(page.rect.y1, rect.y1),
    )
    if clamped.width < 2 or clamped.height < 2:
        return fitz.Rect(page.rect.x0 + 36, page.rect.y0 + 36, page.rect.x1 - 36, page.rect.y1 - 36)
    return clamped


def render_crop(page: fitz.Page, rect: fitz.Rect, crop_path: Path, dpi: int = 190) -> bool:
    crop_path.parent.mkdir(parents=True, exist_ok=True)
    candidates = [(clamp_rect(page, rect), dpi), (clamp_rect(page, fitz.Rect(36, 36, page.rect.x1 - 36, page.rect.y1 - 36)), 140)]
    for candidate, candidate_dpi in candidates:
        try:
            pix = page.get_pixmap(
                matrix=fitz.Matrix(candidate_dpi / 72, candidate_dpi / 72),
                clip=candidate,
                alpha=False,
            )
            pix.save(crop_path)
            return crop_path.exists() and crop_path.stat().st_size > 0
        except Exception:
            if crop_path.exists():
                crop_path.unlink(missing_ok=True)
    return False


def artifact_stem(pdf: Path) -> str:
    return re.sub(r"[^A-Za-z0-9._-]+", "_", pdf.stem).strip("._-")[:40] or "paper"


def normalize_cell(text: str) -> str:
    return (
        text.replace("\ufb01", "fi")
        .replace("\ufb02", "fl")
        .replace("\u2212", "-")
        .replace("\u2013", "-")
        .replace("\u2014", "-")
        .replace("\u2217", "*")
        .replace("âˆ—", "*")
        .strip()
    )


def group_word_rows(words: list[tuple], y_tolerance: float = 3.0) -> list[list[tuple]]:
    rows: list[list[tuple]] = []
    for word in sorted(words, key=lambda w: (w[1], w[0])):
        if not rows:
            rows.append([word])
            continue
        row_y = sum(w[1] for w in rows[-1]) / len(rows[-1])
        if abs(word[1] - row_y) <= y_tolerance:
            rows[-1].append(word)
        else:
            rows.append([word])
    for row in rows:
        row.sort(key=lambda w: w[0])
    return rows


def row_text(row: list[tuple]) -> str:
    return " ".join(normalize_cell(str(w[4])) for w in row).strip()


def cell_text(cell: list[tuple]) -> str:
    return " ".join(normalize_cell(str(w[4])) for w in cell).strip()


def word_center(word: tuple) -> float:
    return (float(word[0]) + float(word[2])) / 2


def cell_center(cell: list[tuple]) -> float:
    return (min(float(w[0]) for w in cell) + max(float(w[2]) for w in cell)) / 2


def group_row_cells(row: list[tuple]) -> list[list[tuple]]:
    if not row:
        return []
    sorted_row = sorted(row, key=lambda w: w[0])
    widths = [max(1.0, float(w[2]) - float(w[0])) for w in sorted_row]
    median_width = sorted(widths)[len(widths) // 2]
    gap_threshold = max(8.0, median_width * 1.6)
    cells: list[list[tuple]] = [[sorted_row[0]]]
    for word in sorted_row[1:]:
        gap = float(word[0]) - float(cells[-1][-1][2])
        previous_token = normalize_cell(str(cells[-1][-1][4]))
        current_token = normalize_cell(str(word[4]))
        numeric_column_gap = gap > 2.5 and (is_column_candidate(previous_token) or is_column_candidate(current_token))
        if gap > gap_threshold or numeric_column_gap:
            cells.append([word])
        else:
            cells[-1].append(word)
    return cells


def is_header_row(text: str) -> bool:
    if HEADER_RE.search(text):
        return True
    return bool(
        re.match(
            r"^(Aggregate Market Sample|Top 70% Sample|Constant Dealer Sample|Year\b|Variable\b)",
            text,
            re.IGNORECASE,
        )
    )


def normalize_caption_number(number: str) -> str:
    number = number.strip().replace(" ", "")
    number = re.sub(r"^IA\.?", "IA", number, flags=re.IGNORECASE)
    return number


def table_number_aliases(number: str) -> set[str]:
    normalized = normalized_table_number(number)
    aliases = {normalized}
    prefix = ""
    body = normalized
    if normalized.startswith("IA"):
        prefix = "IA"
        body = normalized[2:]
    if body.isdigit():
        value = int(body)
        roman = INT_TO_ROMAN.get(value)
        if roman:
            aliases.add(prefix + roman)
    elif body in ROMAN_TO_INT:
        aliases.add(prefix + str(ROMAN_TO_INT[body]))
    return aliases


def actual_table_caption_lines(text: str) -> list[tuple[str, str]]:
    # Some scanned PDFs encode a caption one character per line. Recover only
    # the explicit caption token/identifier, not arbitrary prose or mathematics.
    text = re.sub(r"(?im)^\s*T\s*\na\s*\nb\s*\nl\s*\ne\s*\n\s*((?:[IVX]\s*\n)+)",
                  lambda m: "Table " + re.sub(r"\s+", "", m[1]) + "\n", text)
    lines = [line.strip() for line in text.splitlines()]
    captions: list[tuple[str, str]] = []
    for i, line in enumerate(lines):
        if re.match(r"^insert\s+table\b", line, re.IGNORECASE):
            continue
        titled_match = TABLE_TITLED_LINE_RE.fullmatch(line)
        standalone_match = TABLE_LINE_RE.fullmatch(line)
        if titled_match:
            number = normalize_caption_number(titled_match.group(1))
            title = titled_match.group(2).strip()
            if re.match(r"^Panel\s+[A-Z]\s+(reports|shows|presents)\b", title, re.IGNORECASE):
                continue
            if re.search(
                r"\b(standard errors?|statistical significance|shown in parentheses|clustered by)\b",
                title,
                re.IGNORECASE,
            ):
                continue
        elif standalone_match:
            number = normalize_caption_number(standalone_match.group(1))
            title_parts: list[str] = []
            prose_first = False
            for nxt in lines[i + 1 : i + 4]:
                if not nxt:
                    continue
                if re.match(r"^Electronic copy available\b", nxt, re.IGNORECASE):
                    continue
                if TABLE_LINE_RE.fullmatch(nxt) or TABLE_TITLED_LINE_RE.fullmatch(nxt):
                    break
                if PROSE_RE.search(nxt) and not is_caption_title_line(nxt):
                    if not title_parts:
                        prose_first = True
                    break
                if nxt.lower().startswith("panel "):
                    break
                if len(nxt) > 120:
                    break
                title_parts.append(nxt)
                if len(title_parts) >= 2:
                    break
            if prose_first:
                continue
            title = " ".join(title_parts).strip() or f"Table {number}"
        else:
            continue
        lowered = title.lower()
        if lowered.startswith("here") or "electronic copy" in lowered or "ssrn.com" in lowered:
            continue
        captions.append((number, title))
    return captions


def is_caption_title_line(line: str) -> bool:
    stripped = line.strip()
    if not stripped or len(stripped) > 120:
        return False
    if re.search(
        r"\b(this table|reports|presents|illustrates|shows|discussed|following|whether|relation)\b",
        stripped,
        re.IGNORECASE,
    ):
        return False
    if stripped.endswith(".") and len(stripped.split()) > 7:
        return False
    return bool(re.search(r"[A-Za-z]", stripped))


def caption_y(page: fitz.Page, number: str) -> float | None:
    words = page.get_text("words")
    wanted = normalized_table_number(number)
    rows = group_word_rows(words, y_tolerance=4.0)
    for row in rows:
        for idx, word in enumerate(row[:-1]):
            token = str(word[4]).lower()
            if token != "table":
                continue
            nxt = str(row[idx + 1][4]).strip().rstrip(":.")
            if normalized_table_number(nxt) != wanted:
                continue
            visual_line = row_text(row)
            if re.match(r"^(Appendix\s+)?Table\s+", visual_line, re.IGNORECASE):
                return min(float(w[1]) for w in row)
    for idx, word in enumerate(words[:-1]):
        token = str(word[4]).lower()
        if token != "table":
            continue
        nxt = str(words[idx + 1][4]).strip().rstrip(":.")
        if normalized_table_number(nxt) == wanted:
            break
    for block in page.get_text("blocks"):
        rect = fitz.Rect(block[:4])
        text = str(block[4])
        if re.search(r"T\s*\na\s*\nb\s*\nl\s*\ne", text, re.I):
            if re.sub(r"\s+", "", text).upper() == "TABLE" + wanted or any(normalized_table_number(n) == wanted for n,_ in actual_table_caption_lines(text)):
                return rect.y0
        for line in text.splitlines():
            line = line.strip()
            match = TABLE_LINE_RE.match(line) or TABLE_TITLED_LINE_RE.match(line)
            if match and normalized_table_number(match.group(1)) == wanted:
                return rect.y0
    return None


def table_words(page: fitz.Page, start_y: float, stop_y: float | None = None) -> list[tuple]:
    words = page.get_text("words")
    bottom = min(stop_y - 4, page.rect.y1 - 35) if stop_y else page.rect.y1 - 35
    selected = [w for w in words if float(w[1]) >= start_y - 4 and float(w[1]) <= bottom]
    return selected


def infer_table_rect(page: fitz.Page, words: list[tuple], start_y: float) -> fitz.Rect:
    if not words:
        return fitz.Rect(40, max(0, start_y - 10), page.rect.x1 - 40, page.rect.y1 - 45)
    x0 = max(page.rect.x0, min(float(w[0]) for w in words) - 6)
    y0 = max(page.rect.y0, start_y - 8)
    x1 = min(page.rect.x1, max(float(w[2]) for w in words) + 6)
    y1 = min(page.rect.y1, max(float(w[3]) for w in words) + 8)
    return fitz.Rect(x0, y0, x1, y1)


def is_column_candidate(text: str) -> bool:
    compact = text.replace(" ", "")
    return bool(NUMERIC_RE.match(compact) or COLUMN_TOKEN_RE.match(text.strip()))


def cluster_centers(centers: list[float], tolerance: float = 16.0) -> list[float]:
    if not centers:
        return []
    clusters: list[list[float]] = []
    for center in sorted(centers):
        if not clusters or abs(center - (sum(clusters[-1]) / len(clusters[-1]))) > tolerance:
            clusters.append([center])
        else:
            clusters[-1].append(center)
    return [sum(cluster) / len(cluster) for cluster in clusters if len(cluster) >= 2]


def infer_column_anchors(rows: list[list[tuple]]) -> list[float]:
    centers: list[float] = []
    within_row_gaps = []
    for row in rows:
        text = row_text(row)
        if re.match(r"^Panel\s+[A-Z]:", text, re.IGNORECASE):
            continue
        row_centers = []
        for cell in group_row_cells(row):
            value = cell_text(cell)
            if is_column_candidate(value):
                centers.append(cell_center(cell))
                row_centers.append(cell_center(cell))
        within_row_gaps.extend(b-a for a,b in zip(row_centers,row_centers[1:]) if b>a)
    tolerance = min(12.0, max(3.0, sorted(within_row_gaps)[len(within_row_gaps)//2] / 3)) if within_row_gaps else 12.0
    anchors = cluster_centers(centers, tolerance=tolerance)
    if len(anchors) <= 1:
        return anchors
    # The left-most repeated numeric-like cluster is often the row label index in
    # descriptive tables. Keep it only when it is close to the other data columns.
    gaps = [right - left for left, right in zip(anchors, anchors[1:])]
    if gaps and gaps[0] > 1.8 * (sorted(gaps)[len(gaps) // 2] or gaps[0]):
        first_values = [cell_text(cell) for row in rows for cell in group_row_cells(row)
                        if abs(cell_center(cell)-anchors[0]) <= tolerance and is_column_candidate(cell_text(cell))]
        # A wide gap alone does not make a numeric column a row index.
        if len(first_values)>=3 and all(re.fullmatch(r"\d{1,3}",v) for v in first_values):
            numbers=[int(v) for v in first_values]
            if numbers == list(range(numbers[0],numbers[0]+len(numbers))): anchors=anchors[1:]
    return anchors


def markdown_escape(value: str) -> str:
    return value.replace("|", "\\|").strip()


def is_section_label(text: str) -> bool:
    return bool(
        re.match(
            r"^(Panel\s+[A-Z]:|Core dealers|Peripheral dealers|Quantile regressions|Most Traded|Second-Most Traded)\b",
            text,
            re.IGNORECASE,
        )
    )


def row_data_count(row: list[str]) -> int:
    return sum(1 for value in row[1:] if value)


def strip_table_boilerplate_label(label: str) -> str:
    stripped = label.strip()
    if not stripped:
        return ""
    if re.match(r"^https?://", stripped, re.IGNORECASE):
        return ""
    if re.fullmatch(
        r"(?:downloaded|from|by|of|university|chicago|library|user|on|february|\d{1,2}|20\d{2})",
        stripped,
        re.IGNORECASE,
    ):
        return ""
    stripped = re.sub(r"\bhttps?://\S+\b", "", stripped, flags=re.IGNORECASE).strip()
    stripped = re.sub(
        r"\b(?:Downloaded|from|by University|University|of Chicago|Chicago|Library|user|on February|February)\b",
        "",
        stripped,
        flags=re.IGNORECASE,
    )
    return re.sub(r"\s{2,}", " ", stripped).strip()


def is_embedded_body_prose(label: str) -> bool:
    if is_section_label(label):
        return False
    words = re.findall(r"[A-Za-z]{2,}", label)
    if len(words) < 5:
        return False
    if is_note_line(label):
        return True
    return bool(
        re.search(
            r"\b(Table\s+\d+\s+shows|Equation\s*\(?\d+|equation|where|following specification|"
            r"following equation|indicating|suggesting|we follow|we propose|we consider|"
            r"inventory level|standard errors?|robust standard errors?)\b",
            label,
            re.IGNORECASE,
        )
        or (len(words) >= 9 and re.search(r"[.,;:]", label))
    )


def is_table_note_tail(label: str) -> bool:
    if not is_note_line(label):
        return False
    return not bool(
        re.search(
            r"\b(equation|where|following equation|following specification|we follow|we propose|"
            r"we consider|inventory level)\b|=",
            label,
            re.IGNORECASE,
        )
    )


def trim_embedded_prose_tail(rows: list[list[str]]) -> tuple[list[list[str]], list[str], int]:
    data_rows_seen = 0
    for idx, row in enumerate(rows):
        data_count = row_data_count(row)
        label = row[0].strip()
        if data_count >= 2:
            data_rows_seen += 1
            continue
        if data_rows_seen < 3 or data_count != 0 or not is_embedded_body_prose(label):
            continue
        tail = rows[idx:]
        future_dense_rows = sum(1 for future in tail[1:8] if row_data_count(future) >= 2)
        future_sections = any(is_section_label(future[0].strip()) for future in tail[1:8])
        if future_dense_rows or future_sections:
            continue
        notes = [clean_note_line(item[0]) for item in tail if is_table_note_tail(item[0])]
        return rows[:idx], notes, len(tail)
    return rows, [], 0


def merge_label_continuations(rows: list[list[str]]) -> list[list[str]]:
    merged: list[list[str]] = []
    pending: list[str] = []
    for row in rows:
        data_count = row_data_count(row)
        label = strip_table_boilerplate_label(row[0])
        row = row.copy()
        row[0] = label
        if data_count == 0 and label and len(label) <= 70 and not is_section_label(label):
            pending.append(label)
            continue
        if pending and data_count > 0:
            row = row.copy()
            row[0] = " ".join(pending + [row[0]]).strip()
            pending = []
        elif pending:
            for label_part in pending:
                merged.append([label_part] + [""] * (len(row) - 1))
            pending = []
        merged.append(row)
    for label_part in pending:
        merged.append([label_part] + [""] * (len(rows[0]) - 1))
    return merged


def trim_trailing_notes(rows: list[list[str]]) -> tuple[list[list[str]], list[str]]:
    notes: list[str] = []
    while rows:
        row = rows[-1]
        data_count = row_data_count(row)
        label = strip_table_boilerplate_label(row[0])
        prose_note = len(label) > 80 and data_count <= 2
        empty_label_only = data_count == 0 and not is_section_label(label)
        if prose_note or empty_label_only:
            if is_note_line(label):
                notes.insert(0, clean_note_line(label))
            rows = rows[:-1]
            continue
        break
    return rows, notes


def split_prose_table_rows(rows: list[list[str]]) -> tuple[list[list[str]], list[str], int]:
    kept: list[list[str]] = []
    notes: list[str] = []
    removed = 0
    for row in rows:
        row = row.copy()
        row[0] = strip_table_boilerplate_label(row[0])
        data_count = row_data_count(row)
        label = row[0].strip()
        if not label and data_count == 0:
            removed += 1
            continue
        prose_like = bool(
            re.search(
                r"\b(this table|table\s+(?:\d+|[IVX]+)\s+shows|table\s+(?:\d+|[IVX]+)\b|"
                r"equation|figure|column|specification|"
                r"standard errors?|reports|regresses|sample|fixed effects?|following|"
                r"indicating|suggesting|where|we follow|we propose|we consider)\b",
                label,
                re.IGNORECASE,
            )
        )
        if (
            (data_count == 0 and len(label) > 90)
            or (data_count == 0 and prose_like)
            or (len(label) > 90 and prose_like and data_count <= 2)
            or (len(label) > 160 and data_count <= 3)
        ):
            removed += 1
            if is_note_line(label):
                notes.append(clean_note_line(label))
            continue
        kept.append(row)
    return kept, notes, removed


def is_note_line(text: str) -> bool:
    stripped = text.strip()
    if not stripped or stripped.isdigit():
        return False
    if re.match(r"^\d+(?:\.\d+)?\s+[A-Z]", stripped):
        return False
    if len(stripped.split()) < 5 and not re.search(r"\*|p<|standard errors?|fixed effects?", stripped, re.IGNORECASE):
        return False
    return bool(
        re.search(
            r"\b(note|standard errors?|t-stat|p<|significance|sample|fixed effects?|instrumental variables|observations|reported|regression|table)\b|\*",
            stripped,
            re.IGNORECASE,
        )
    )


def clean_note_line(text: str) -> str:
    return re.sub(r"^\s*Note\s*[:.]?\s*", "", text.strip(), flags=re.IGNORECASE)


def plain_coordinate_fallback(rows: list[list[tuple]], *, max_rows: int = 60) -> str:
    fallback_rows = []
    for row in rows:
        text = row_text(row)
        if not text:
            continue
        if text.isdigit() and not fallback_rows:
            continue
        if TABLE_LINE_RE.fullmatch(text) or TABLE_TITLED_LINE_RE.fullmatch(text):
            continue
        if re.match(r"^insert\s+table\b", text, re.IGNORECASE):
            continue
        fallback_rows.append(text)
        if len(fallback_rows) >= max_rows:
            break
    return "\n".join(fallback_rows) if len(fallback_rows) >= 3 else ""


def is_list_table_cell(value: str) -> bool:
    if not value or len(value) > 45:
        return False
    if re.search(r"[.;:]", value):
        return False
    if re.search(r"\b(the|and|of|in|for|with|from)\b", value, re.IGNORECASE) and len(value.split()) > 3:
        return False
    return bool(re.search(r"[A-Za-z]", value))


def reconstruct_list_table(rows: list[list[tuple]]) -> tuple[str, str, list[str]]:
    best_run: list[list[str]] = []
    current: list[list[str]] = []
    for row in rows:
        cells = [cell_text(cell) for cell in group_row_cells(row)]
        cells = [cell for cell in cells if is_list_table_cell(cell)]
        if 2 <= len(cells) <= 4:
            current.append(cells)
        else:
            if len(current) > len(best_run):
                best_run = current
            current = []
    if len(current) > len(best_run):
        best_run = current
    if len(best_run) < 3:
        return "", "low", ["too-few-list-table-rows"]

    width = max(len(row) for row in best_run)
    padded = [row + [""] * (width - len(row)) for row in best_run]
    labels = [f"c{i}" for i in range(1, width + 1)]
    widths = [len(label) for label in labels]
    for row in padded:
        for idx, value in enumerate(row):
            widths[idx] = max(widths[idx], len(value))

    def format_row(values: list[str]) -> str:
        cells = [markdown_escape(value).ljust(widths[idx]) for idx, value in enumerate(values)]
        return "| " + " | ".join(cells) + " |"

    separator = "| " + " | ".join("-" * width for width in widths) + " |"
    lines = [format_row(labels), separator]
    lines.extend(format_row(row) for row in padded)
    return "\n".join(lines), "medium", ["list-table-column-clustering", "text-list-table"]


def reconstruct_clustered_table(rows: list[list[tuple]]) -> tuple[str, str, list[str]]:
    panel_starts = [i for i,row in enumerate(rows) if re.match(r"^Panel\s+[A-Z][:.]", row_text(row), re.I)]
    if len(panel_starts) > 1:
        parts = []; confidences = []; reasons = ["per-panel-column-clustering"]
        if panel_starts[0] > 0:
            prefix = plain_coordinate_fallback(rows[:panel_starts[0]])
            if prefix: parts.append(prefix)
        for start,stop in zip(panel_starts, panel_starts[1:] + [len(rows)]):
            body,confidence,why = reconstruct_clustered_table(rows[start+1:stop])
            if not body: body = plain_coordinate_fallback(rows[start+1:stop]); confidence = "low"
            parts.append("#### " + row_text(rows[start]) + "\n\n" + body)
            confidences.append(confidence); reasons.extend(why)
        return "\n\n".join(parts), "low" if "low" in confidences else "medium", sorted(set(reasons))
    anchors = infer_column_anchors(rows)
    reasons = ["x-coordinate-column-clustering", "borderless-table-coordinate-text"]
    if len(anchors) < 2:
        return "", "low", reasons + ["too-few-column-anchors"]

    tolerance = max(18.0, min(28.0, (anchors[-1] - anchors[0]) / max(1, len(anchors) - 1) * 0.45))
    labels = ["row"] + [f"c{i}" for i in range(1, len(anchors) + 1)]
    body: list[list[str]] = []
    numeric_rows = 0
    seen_numeric_data = False

    for row in rows:
        text = row_text(row)
        if not text:
            continue
        if re.match(r"^Panel\s+[A-Z]:", text, re.IGNORECASE):
            body.append([text] + [""] * len(anchors))
            continue

        header_row = not seen_numeric_data and sum(bool(NUMERIC_RE.fullmatch(normalize_cell(str(w[4])))) for w in row) < 2
        cells = []
        if header_row:
            for word in row:
                if cells and float(word[0])-float(cells[-1][-1][2]) < 6:
                    cells[-1].append(word)
                else: cells.append([word])
        else: cells = group_row_cells(row)
        group_headers = header_row and 1 < len(cells) < len(anchors) and all(len(cell_text(c))<40 for c in cells)
        row_values = [""] * (len(anchors) + 1)
        label_parts: list[str] = []
        for cell_index,cell in enumerate(cells):
            value = cell_text(cell)
            if not value:
                continue
            center = cell_center(cell)
            nearest_idx = min(range(len(anchors)), key=lambda i: abs(center - anchors[i]))
            header_cell = not seen_numeric_data and center > anchors[0] - tolerance and not is_table_note_tail(value)
            covered = [i+1 for i,anchor in enumerate(anchors)
                       if min(float(w[0]) for w in cell)-4 <= anchor <= max(float(w[2]) for w in cell)+4]
            if group_headers:
                left = (cell_center(cells[cell_index-1])+center)/2 if cell_index else anchors[0]-tolerance
                right = (cell_center(cells[cell_index+1])+center)/2 if cell_index+1<len(cells) else anchors[-1]+tolerance
                covered = [i+1 for i,anchor in enumerate(anchors) if left <= anchor < right]
            if header_row and covered:
                for target in covered: row_values[target] = f"{row_values[target]} {value}".strip()
                continue
            if abs(center - anchors[nearest_idx]) <= tolerance and (is_column_candidate(value) or header_cell):
                target = nearest_idx + 1
                row_values[target] = f"{row_values[target]} {value}".strip()
            else:
                label_parts.append(value)
        row_values[0] = " ".join(label_parts).strip()

        filled_data = sum(1 for value in row_values[1:] if value)
        if filled_data == 0 and not row_values[0]:
            continue
        if filled_data > 0:
            numeric_rows += 1
        if row_values[0] and any(NUMERIC_RE.match(v) for v in row_values[1:]):
            seen_numeric_data = True
        body.append(row_values)

    if numeric_rows < 3:
        return "", "low", reasons + ["few-clustered-numeric-rows"]

    header_rows = []
    for row in body[:6]:
        if row[0] and any(NUMERIC_RE.fullmatch(v) for v in row[1:]): break
        if sum(bool(v) for v in row[1:]) >= 2: header_rows.append(row)
    if header_rows:
        labels = [" ".join(row[i] for row in header_rows if row[i]) for i in range(len(anchors)+1)]
        body = [row for row in body if row not in header_rows]
        reasons.append("source-header-associations")
    for row in body[:6] if not header_rows else []:
        data_values = row[1:]
        if sum(1 for value in data_values if re.fullmatch(r"\(?\d+\)?", value.strip())) >= 2:
            labels = ["row"] + [value or f"c{i}" for i, value in enumerate(data_values, start=1)]
            break
    body = [row for row in body if row[1:] != labels[1:]]
    body = merge_label_continuations(body)
    body, embedded_notes, removed_embedded_rows = trim_embedded_prose_tail(body)
    if removed_embedded_rows:
        reasons.append("embedded-prose-tail-trimmed")
    body, prose_notes, removed_prose_rows = split_prose_table_rows(body)
    if removed_prose_rows:
        reasons.append("prose-table-rows-trimmed")
    body, note_lines = trim_trailing_notes(body)
    note_lines = embedded_notes + prose_notes + note_lines
    if note_lines:
        reasons.append("trailing-note-rows-trimmed")

    widths = [len(label) for label in labels]
    for row in body:
        for idx, value in enumerate(row):
            widths[idx] = max(widths[idx], len(value))

    def format_row(values: list[str]) -> str:
        padded = [markdown_escape(value).ljust(widths[idx]) for idx, value in enumerate(values)]
        return "| " + " | ".join(padded) + " |"

    separator = "| " + " | ".join("-" * max(3,width) for width in widths) + " |"
    lines = [format_row(labels), separator]
    lines.extend(format_row(row) for row in body)
    confidence = "medium" if numeric_rows >= 4 else "low"
    note_text = "\n".join(note_lines).strip()
    if note_text:
        lines.extend(["", "Note:", note_text])
    return "\n".join(lines), confidence, reasons


def reconstruct_coordinate_text(words: list[tuple], caption_start_y: float) -> tuple[str, str, list[str]]:
    rows = group_word_rows([w for w in words if float(w[1]) > caption_start_y + 15])
    out_rows: list[str] = []
    seen_data = False
    prose_streak = 0
    reasons: list[str] = []
    start_idx = None

    for idx, row in enumerate(rows):
        text = row_text(row)
        numeric_tokens = len(re.findall(r"[-+]?\d[\d,]*(?:\.\d+)?%?", text))
        if is_header_row(text) or (numeric_tokens >= 4 and not PROSE_RE.search(text)):
            start_idx = idx
            break

    if start_idx is None:
        list_table, list_confidence, list_reasons = reconstruct_list_table(rows)
        if list_table:
            return list_table, list_confidence, list_reasons
        fallback = plain_coordinate_fallback(rows)
        if fallback:
            reasons.extend(["no-table-header-row", "text-table-coordinate-fallback"])
            return fallback, "low", reasons
        reasons.append("no-table-header-row")
        return "", "low", reasons

    for row in rows[start_idx:]:
        text = row_text(row)
        if not text:
            continue
        numeric_tokens = len(re.findall(r"[-+]?\d[\d,]*(?:\.\d+)?%?", text))
        starts_panel = bool(re.match(r"^Panel\s+[A-Z]:", text, re.IGNORECASE))
        if seen_data and len(row) > 10 and numeric_tokens == 0 and not starts_panel:
            break
        is_data = starts_panel or bool(DATA_RE.search(text)) or numeric_tokens >= 2
        if not seen_data:
            seen_data = True
            prose_streak = 0
            out_rows.append(text)
            continue
        if is_data or is_header_row(text):
            prose_streak = 0
            out_rows.append(text)
            continue
        if seen_data:
            prose_streak += 1
            if prose_streak >= 3:
                break
            out_rows.append(text)

    if not out_rows:
        reasons.append("no-coordinate-table-rows")
        return "", "low", reasons
    # Include adjacent short header lines, without pulling caption prose into cells.
    header_start = start_idx
    while header_start > 0 and start_idx-header_start < 3:
        previous = row_text(rows[header_start-1])
        if len(previous) > 100 or re.search(r"[.;:]$|\b(this|reports|sample|table|notes?)\b", previous, re.I): break
        header_start -= 1
    clustered, clustered_confidence, clustered_reasons = reconstruct_clustered_table(rows[header_start:])
    if clustered:
        return clustered, clustered_confidence, clustered_reasons
    numeric_rows = sum(1 for row in out_rows if re.search(r"\d", row))
    if numeric_rows < 3:
        fallback = plain_coordinate_fallback(rows[start_idx:])
        if fallback:
            reasons.append("text-table-coordinate-fallback")
            return fallback, "low", reasons
        reasons.append("few-numeric-rows")
        reasons.append("borderless-table-coordinate-text")
        return "", "low", reasons
    reasons.append("borderless-table-coordinate-text")
    confidence = "medium" if numeric_rows >= 6 else "low"
    return "\n".join(out_rows), confidence, reasons


def table_markdown(region: TableRegion) -> str:
    lines = [
        f"### Table {region.number}. {region.title}",
        "",
        f"<!-- page: {region.page}; crop: {region.crop_path}; tsv: {region.tsv_path or 'none'}; confidence: {region.confidence}; reasons: {', '.join(region.reasons)} -->",
        "",
    ]
    if region.extracted_text:
        if any(line.startswith("|") for line in region.extracted_text.splitlines()):
            lines.extend([region.extracted_text, ""])
        else:
            lines.extend(["```text", region.extracted_text, "```", ""])
    else:
        lines.extend(["Table geometry was detected but not reconstructed reliably. See crop image.", ""])
    if region.note_text:
        lines.extend(["**Note.** " + region.note_text, ""])
    return "\n".join(lines).rstrip() + "\n"


def ruled_table(page, rect: fitz.Rect, attempts: list[int] | None = None) -> tuple[str, list[list[str]], list[float]] | None:
    """Use the installed native detector only when visible rules justify its cost."""
    lines = [item for drawing in page.get_drawings() for item in drawing["items"]
             if item[0] == "l" and rect.contains(item[1]) and rect.contains(item[2])]
    horizontal = sum(abs(item[1].y-item[2].y)<1 for item in lines)
    vertical = sum(abs(item[1].x-item[2].x)<1 for item in lines)
    if horizontal < 3 or vertical < 2: return None
    if attempts is not None:
        if attempts[0] >= 3: return None
        attempts[0] += 1
    found = page.find_tables(clip=rect, strategy="lines_strict")
    if len(found.tables) != 1: return None
    table = found.tables[0]
    cells = [[normalize_cell(value or "").replace("\n", " ") for value in row] for row in table.extract()]
    if len(cells)<3 or len(cells[0])<2: return None
    width = len(cells[0])
    if any(len(row)!=width for row in cells): return None
    rows = ["| " + " | ".join(markdown_escape(value) for value in row) + " |" for row in cells]
    rows.insert(1,"| " + " | ".join(["---"]*width) + " |")
    return "\n".join(rows), cells, list(table.bbox)


def detect_table_regions(
    pdf: Path,
    output_dir: Path,
    pages: list[int],
    *,
    max_crops: int,
    time_budget: float,
    deadline: float | None = None,
) -> list[TableRegion]:
    started = time.perf_counter()
    doc = get_document(pdf)
    regions: list[TableRegion] = []
    crop_root = output_dir / "_table_crops" / artifact_stem(pdf)
    tsv_root = output_dir / "_table_tsv" / artifact_stem(pdf)
    seen: set[tuple[int, str]] = set()
    native_attempts = [0]

    for page_number in pages:
        if len(regions) >= max_crops or time.perf_counter() - started > time_budget or expired(deadline):
            break
        if page_number < 1 or page_number > len(doc):
            continue
        page = doc[page_number - 1]
        captions = actual_table_caption_lines(page.get_text("text"))
        caption_positions: list[tuple[str, str, float]] = []
        for number, title in captions:
            y = caption_y(page, number)
            if y is not None:
                caption_positions.append((number, title, y))
            elif not expired(deadline):
                blocks = page.get_text("blocks")
                degenerate = sum(float(b[2])-float(b[0]) < .1 for b in blocks)
                if degenerate >= 5 and any((fitz.Rect(info["bbox"]) & page.rect).get_area() > .8*page.rect.get_area() for info in page.get_image_info()):
                    crop_path = crop_root / f"p{page_number:03d}_table_{number}_source.png"
                    render_crop(page, page.rect, crop_path, dpi=140)
                    regions.append(TableRegion(page_number, number, title, list(page.rect), str(crop_path), "low",
                                               ["source-degenerate-text-geometry"], "",
                                               "Source image retained: embedded character coordinates are degenerate; no OCR was used.", "", None))
        caption_positions.sort(key=lambda item: item[2])
        for idx, (number, title, y) in enumerate(caption_positions):
            if len(regions) >= max_crops or time.perf_counter() - started > time_budget or expired(deadline):
                break
            key = (page_number, number)
            if key in seen:
                continue
            seen.add(key)
            stop_y = None
            for _, _, candidate_y in caption_positions[idx + 1 :]:
                if candidate_y > y + 24:
                    stop_y = candidate_y
                    break
            words = table_words(page, y, stop_y)
            rect = infer_table_rect(page, words, y)
            extracted, confidence, reasons = reconstruct_coordinate_text(words, y)
            native = None if expired(deadline) else ruled_table(page, rect, native_attempts)
            body_bbox = None; cells = None
            if native:
                extracted,cells,body_bbox = native
                confidence = "medium"; reasons = ["native-ruled-table", "cell-geometry"]
            extracted, note_text = split_extracted_note(extracted)
            crop_path = crop_root / f"p{page_number:03d}_table_{number}.png"
            tsv_path = write_table_tsv(extracted, tsv_root / f"p{page_number:03d}_table_{number}.tsv")
            if not render_crop(page, rect, crop_path):
                reasons = [*reasons, "crop-render-failed"]
                confidence = "low"
            region = TableRegion(
                page=page_number,
                number=number,
                title=title,
                bbox=[round(v, 2) for v in rect],
                crop_path=str(crop_path),
                confidence=confidence,
                reasons=reasons,
                extracted_text=extracted,
                note_text=note_text,
                tsv_path=tsv_path,
                markdown=None,
                quality_flags=reasons if confidence == "low" else [],
                body_bbox=body_bbox,
                cells=cells,
            )
            regions.append(region)
    return regions


def table_sequence_value(number: str) -> int | None:
    normalized = normalized_table_number(number)
    if normalized.startswith("IA"):
        return None
    if normalized.isdigit():
        return int(normalized)
    return ROMAN_TO_INT.get(normalized)


def infer_missing_table_placeholders(
    pdf: Path,
    output_dir: Path,
    existing_regions: list[TableRegion],
    referenced_numbers: list[str],
    *,
    max_placeholders: int = 6,
    deadline: float | None = None,
) -> list[TableRegion]:
    if not referenced_numbers or not existing_regions:
        return []
    extracted_aliases = {alias for region in existing_regions for alias in table_number_aliases(region.number)}
    numbered_regions = [
        (value, region)
        for region in existing_regions
        if (value := table_sequence_value(region.number)) is not None
    ]
    if len(numbered_regions) < 2:
        return []

    doc = get_document(pdf)
    crop_root = output_dir / "_table_crops" / artifact_stem(pdf)
    placeholders: list[TableRegion] = []
    used_pages = {region.page for region in existing_regions}
    for reference in referenced_numbers:
        if len(placeholders) >= max_placeholders or expired(deadline):
            break
        if table_number_aliases(reference) & extracted_aliases:
            continue
        target = table_sequence_value(reference)
        if target is None:
            continue
        previous = [item for item in numbered_regions if item[0] < target]
        following = [item for item in numbered_regions if item[0] > target]
        if not previous or not following:
            continue
        prev_region = max(previous, key=lambda item: item[0])[1]
        next_region = min(following, key=lambda item: item[0])[1]
        if next_region.page - prev_region.page > 3:
            continue
        candidate_pages = [page for page in range(prev_region.page + 1, next_region.page) if page not in used_pages]
        if not candidate_pages:
            continue
        page_number = min(
            candidate_pages,
            key=lambda page_number: len(doc[page_number - 1].get_text("text").strip()),
        )
        page = doc[page_number - 1]
        if len(page.get_text("text").strip()) > 800:
            continue
        rect = fitz.Rect(36, 36, page.rect.x1 - 36, page.rect.y1 - 36)
        crop_path = crop_root / f"p{page_number:03d}_table_{normalized_table_number(reference)}_inferred.png"
        render_crop(page, rect, crop_path, dpi=190)
        placeholder = TableRegion(
            page=page_number,
            number=reference,
            title="Referenced table inferred from page order; text extraction unavailable",
            bbox=[round(v, 2) for v in rect],
            crop_path=str(crop_path),
            confidence="low",
            reasons=["inferred-missing-table-page", "no-extractable-table-text"],
            extracted_text="",
            note_text="Rendered crop only. The PDF page has little or no extractable text, and OCR was not used.",
            tsv_path="",
            markdown=None,
        )
        placeholders.append(placeholder)
        extracted_aliases.update(table_number_aliases(reference))
        used_pages.add(page_number)
    return placeholders


def infer_referenced_table_contexts(
    pdf: Path,
    output_dir: Path,
    existing_regions: list[TableRegion],
    referenced_numbers: list[str],
    *,
    max_contexts: int = 16,
    deadline: float | None = None,
) -> list[TableRegion]:
    extracted_aliases = {alias for region in existing_regions for alias in table_number_aliases(region.number)}
    missing = [number for number in referenced_numbers if not (table_number_aliases(number) & extracted_aliases)]
    if not missing:
        return []

    doc = get_document(pdf)
    crop_root = output_dir / "_table_crops" / artifact_stem(pdf)
    contexts: list[TableRegion] = []
    used: set[str] = set()
    for reference in missing:
        if len(contexts) >= max_contexts or expired(deadline):
            break
        wanted = normalized_table_number(reference)
        found: tuple[int, fitz.Rect, str] | None = None
        for page_number, page in enumerate(doc, start=1):
            if expired(deadline): break
            for block in page.get_text("blocks"):
                rect = fitz.Rect(block[:4])
                text = str(block[4])
                for line in text.splitlines():
                    line = line.strip()
                    match = TABLE_LINE_RE.match(line) or TABLE_TITLED_LINE_RE.match(line)
                    if match and normalized_table_number(match.group(1)) == wanted:
                        found = (page_number, rect, line)
                        break
                if found:
                    break
            if found:
                break
        if not found:
            continue
        page_number, rect, line = found
        key = f"{page_number}:{wanted}"
        if key in used:
            continue
        used.add(key)
        page = doc[page_number - 1]
        crop_rect = fitz.Rect(36, max(0, rect.y0 - 12), page.rect.x1 - 36, page.rect.y1 - 36)
        crop_path = crop_root / f"p{page_number:03d}_table_{wanted}_context.png"
        render_crop(page, crop_rect, crop_path, dpi=190)
        contexts.append(
            TableRegion(
                page=page_number,
                number=reference,
                title=line,
                bbox=[round(v, 2) for v in crop_rect],
                crop_path=str(crop_path),
                confidence="low",
                reasons=["referenced-table-context-crop", "low-confidence-layout-fallback"],
                extracted_text="",
                note_text="Rendered crop only. The table was referenced and a caption-like block was found, but reliable coordinate reconstruction was not available.",
                tsv_path="",
                markdown=None,
                repair_source="referenced-context",
                quality_flags=["referenced-table-context-crop"],
            )
        )
        extracted_aliases.update(table_number_aliases(reference))
    return contexts


def regions_to_jsonable(regions: list[TableRegion]) -> list[dict]:
    return [asdict(region) for region in regions]


def table_repair_appendix(regions: list[TableRegion]) -> str:
    if not regions:
        return ""
    lines = ["", "## Extracted Tables", ""]
    for group in group_continuation_tables(regions):
        lines.append(grouped_table_markdown(group))
    return "\n".join(lines).rstrip() + "\n"


def normalized_table_number(number: str) -> str:
    return number.strip().replace(" ", "").replace(".", "").upper()


def is_continuation_title(title: str) -> bool:
    return bool(re.search(r"\bcontinued\b", title, re.IGNORECASE))


def group_continuation_tables(regions: list[TableRegion]) -> list[list[TableRegion]]:
    groups: list[list[TableRegion]] = []
    by_number: dict[str, list[TableRegion]] = {}
    for region in regions:
        key = normalized_table_number(region.number)
        if key in by_number and is_continuation_title(region.title):
            by_number[key].append(region)
            continue
        if is_continuation_title(region.title):
            for group in reversed(groups):
                if normalized_table_number(group[0].number) == key:
                    group.append(region)
                    by_number[key] = group
                    break
            else:
                group = [region]
                groups.append(group)
                by_number[key] = group
            continue
        group = [region]
        groups.append(group)
        by_number[key] = group
    return groups


def grouped_table_markdown(regions: list[TableRegion]) -> str:
    if len(regions) == 1:
        return table_markdown(regions[0])

    first = regions[0]
    confidence = "low" if any(region.confidence == "low" for region in regions) else "medium"
    reasons = sorted({reason for region in regions for reason in region.reasons})
    lines = [
        f"### Table {first.number}. {first.title}",
        "",
        f"<!-- pages: {', '.join(str(region.page) for region in regions)}; confidence: {confidence}; reasons: {', '.join(reasons)} -->",
        "",
    ]
    for idx, region in enumerate(regions, start=1):
        lines.extend(
            [
                f"#### Part {idx}: page {region.page}",
                "",
                f"<!-- crop: {region.crop_path}; tsv: {region.tsv_path or 'none'}; title: {region.title} -->",
                "",
            ]
        )
        if region.extracted_text:
            lines.extend(["```text", region.extracted_text, "```", ""])
        else:
            lines.extend(["Table geometry was detected but not reconstructed reliably. See crop image.", ""])
        if region.note_text:
            lines.extend(["**Note.** " + region.note_text, ""])
    return "\n".join(lines).rstrip() + "\n"


def split_extracted_note(extracted: str) -> tuple[str, str]:
    marker = "\n\nNote:\n"
    if marker not in extracted:
        return extracted, ""
    body, note = extracted.split(marker, 1)
    return body.rstrip(), note.strip()


def markdown_table_to_tsv(text: str) -> str:
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    pipe_rows = [line for line in lines if line.startswith("|") and line.endswith("|")]
    if len(pipe_rows) >= 2:
        out: list[str] = []
        for line in pipe_rows:
            cells = [cell.strip().replace(r"\|", "|") for cell in re.split(r"(?<!\\)\|", line[1:-1])]
            if cells and all(set(cell) <= {"-"} for cell in cells if cell):
                continue
            out.append("\t".join(cells))
        return "\n".join(out) + "\n" if out else ""
    return "\n".join(line.replace("\t", " ") for line in lines).rstrip() + "\n" if lines else ""


def write_table_tsv(extracted: str, tsv_path: Path) -> str:
    if not extracted:
        return ""
    tsv = markdown_table_to_tsv(extracted)
    if not tsv.strip():
        return ""
    tsv_path.parent.mkdir(parents=True, exist_ok=True)
    tsv_path.write_text(tsv, encoding="utf-8")
    return str(tsv_path)
