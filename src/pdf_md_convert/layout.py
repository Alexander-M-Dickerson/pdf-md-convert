"""Per-conversion geometry cache and conservative source-position assembly."""
from __future__ import annotations

from collections import Counter
from contextvars import ContextVar
from functools import wraps
from pathlib import Path
import re
import time

import fitz


_ACTIVE = ContextVar("pdf_md_active_document", default=None)


class CachedPage:
    def __init__(self, page):
        self.page = page
        self.cache = {}

    def __getattr__(self, name):
        return getattr(self.page, name)

    @property
    def rect(self):
        # Text/drawing coordinates are unrotated; raster clip coordinates are not.
        return self.page.rect * self.page.derotation_matrix

    def get_pixmap(self, **kwargs):
        if kwargs.get("clip") is not None:
            kwargs["clip"] = fitz.Rect(kwargs["clip"]) * self.page.rotation_matrix
        return self.page.get_pixmap(**kwargs)

    def get_text(self, option="text", **kwargs):
        key = (option, tuple(sorted((k, str(v)) for k,v in kwargs.items())))
        if key not in self.cache:
            self.cache[key] = self.page.get_text(option, **kwargs)
        return self.cache[key]

    def get_drawings(self):
        if "drawings" not in self.cache: self.cache["drawings"] = self.page.get_drawings()
        return self.cache["drawings"]

    def get_image_info(self):
        if "images" not in self.cache: self.cache["images"] = self.page.get_image_info()
        return self.cache["images"]


class CachedDocument:
    def __init__(self, path):
        self.path = Path(path).resolve()
        self.document = fitz.open(path)
        self.pages = {}

    def __len__(self): return len(self.document)

    def __getitem__(self, number):
        if number not in self.pages: self.pages[number] = CachedPage(self.document[number])
        return self.pages[number]

    def __iter__(self):
        for i in range(len(self)): yield self[i]

    def close(self): self.document.close()


def get_document(path):
    current = _ACTIVE.get()
    if current is not None and current.path == Path(path).resolve(): return current
    return fitz.open(path)


def document_scope(function):
    @wraps(function)
    def wrapped(pdf, *args, **kwargs):
        document = CachedDocument(pdf)
        token = _ACTIVE.set(document)
        try: return function(pdf, *args, **kwargs)
        finally:
            _ACTIVE.reset(token)
            document.close()
    return wrapped


def expired(deadline: float | None) -> bool:
    return deadline is not None and time.perf_counter() >= deadline


# A gap wider than this share of the font size between two printed characters is a word space. Glued word gaps sit
# at 0.13 to 0.15; some fonts leave up to 0.12 after an "f" inside a word, and real word spaces start near 0.17.
SPACE_GAP_SHARE = .13
_GLUE_RUN = re.compile(r"[A-Za-z]{15,}")  # shorter runs cost far more reads than they repair
# Spacing accents are separate glyphs placed over a letter ("P´astor"); their gaps say nothing about words.
_ACCENTS = set("¨´`ˆ˜ˇ˘˙˚˝¯¸˛") | {chr(c) for c in range(0x300, 0x370)}
_NO_SPACE_BEFORE = set(".,;:!?)]}%'’”-‐‑") | _ACCENTS
_NO_SPACE_AFTER = set("([{‘“-‐‑") | _ACCENTS
_MATH_FONT = re.compile(r"(?i)math|^(MT|RMT)(MI|SY|EX)|^CM(MI|SY|EX)|^MSAM|^MSBM|symbol|greek|euclid")


def _font(span: dict) -> str:
    return span["font"].split("+")[-1]


def _block_text(block: dict, gap_share: float | None) -> str:
    """A rawdict text block as get_text("blocks") writes it, optionally with spaces at wide gaps."""
    lines = []
    for line in block["lines"]:
        if gap_share is None or abs(line["dir"][1]) >= .01:
            lines.append("".join(c["c"] for span in line["spans"] for c in span["chars"])); continue
        out, prev = [], None
        for span in line["spans"]:
            size, math = span["size"], bool(_MATH_FONT.search(_font(span)))
            for c in span["chars"]:
                if (prev is not None and not math and not prev[2]
                        and not c["c"].isspace() and not prev[0]["c"].isspace()
                        and c["c"] not in _NO_SPACE_BEFORE and prev[0]["c"] not in _NO_SPACE_AFTER
                        and abs(c["origin"][1] - prev[0]["origin"][1]) <= .2 * prev[1]
                        and c["bbox"][0] - prev[0]["bbox"][2] > gap_share * prev[1]):
                    out.append(" ")
                out.append(c["c"]); prev = (c, size, math)
        lines.append("".join(out))
    return "\n".join(lines) + "\n"


def restore_missing_spaces(page, blocks: list[tuple], vocabulary: Counter, gap_share: float | None = None) -> list[tuple]:
    """Put back word spaces that PyMuPDF drops when the printed gap is narrow.

    MuPDF inserts a space only above roughly 0.15 of the font size, and tightly set
    lines in some journals print their word gaps just below that, so whole lines come
    out as one token. Only blocks holding a long letter run found nowhere else in the
    document are rebuilt from character positions; real long words repeat. A block
    keeps its original text unless the rebuild, before adding spaces, reproduces it.
    Character positions are read for the one suspect block alone (a clipped read is
    cheaper) or, with several suspects, once for the whole page.
    """
    suspects = [i for i,b in enumerate(blocks)
                if any(vocabulary[run.casefold()] <= 1 for run in _GLUE_RUN.findall(b[4]))]
    if not suspects: return blocks
    if len(suspects) == 1:
        clip = fitz.Rect(blocks[suspects[0]][:4])
        raw = page.get_text("rawdict", flags=fitz.TEXTFLAGS_BLOCKS, clip=clip)["blocks"]
        rebuilt = {suspects[0]: {"lines": [line for b in raw if b["type"] == 0 for line in b["lines"]]}}
    else:
        raw = {tuple(round(v, 2) for v in b["bbox"]): b
               for b in page.get_text("rawdict", flags=fitz.TEXTFLAGS_BLOCKS)["blocks"] if b["type"] == 0}
        rebuilt = {i: raw.get(tuple(round(v, 2) for v in blocks[i][:4])) for i in suspects}
    out = list(blocks)
    for i, block in rebuilt.items():
        if block is None or _block_text(block, None) != blocks[i][4]: continue
        spaced = _block_text(block, gap_share or SPACE_GAP_SHARE)
        if spaced != blocks[i][4]: out[i] = (*blocks[i][:4], spaced, *blocks[i][5:])
    return out


def ordered_blocks(page) -> list[tuple]:
    blocks = [b for b in page.get_text("blocks") if len(b) < 7 or b[6] == 0]
    mid = page.rect.width / 2
    left = [b for b in blocks if b[2] <= mid + 8 and b[2]-b[0] > 90]
    right = [b for b in blocks if b[0] >= mid - 8 and b[2]-b[0] > 90]
    # Require substantial text in each column. Short formula fragments are not columns.
    if sum(len(b[4]) for b in left) < 400 or sum(len(b[4]) for b in right) < 400:
        return blocks
    if not left or not right or max(b[2] for b in left) > min(b[0] for b in right): return blocks
    spanning = [b for b in blocks if b not in left and b not in right and b[0] < mid < b[2]]
    if any(any(b[1] < s[3] and b[3] > s[1] for b in left + right) for s in spanning): return blocks
    remaining = list(blocks); ordered = []
    for boundary in sorted(spanning, key=lambda b:b[1]) + [None]:
        band = [b for b in remaining if boundary is None or b[3] <= boundary[1]]
        ordered.extend(sorted(band, key=lambda b:(b[0] >= mid, b[1], b[0])))
        remaining = [b for b in remaining if b not in band]
        if boundary is not None:
            ordered.append(boundary); remaining.remove(boundary)
    return ordered


def repeating_margins(doc) -> set[str]:
    seen = Counter()
    for page in doc:
        values = set()
        for b in page.get_text("blocks"):
            if b[1] > 45 and b[3] < page.rect.height - 40: continue
            text = b[4].strip()
            if len(text) > 150 or "=" in text: continue
            values.add(re.sub(r"\d+", "#", text))
        seen.update(values)
    return {text for text,count in seen.items() if count >= max(3, len(doc)*.5)}


MARGIN_BAND = .15  # running heads and feet sit in the outer 15% of the page


def _edge_blocks(page) -> list[tuple]:
    """Short text blocks with nothing above (or below) them in the page's outer band."""
    height = page.rect.height
    # Rotated download stamps can run the full page height; they are not body text.
    blocks = [b for b in page.get_text("blocks") if (len(b) < 7 or b[6] == 0) and b[3] - b[1] < height / 2]
    edges = []
    for i,b in enumerate(blocks):
        text = b[4].strip()
        if not text or len(text) > 150 or "=" in text: continue
        # "Table 5 (continued)" can repeat at the top of appendix pages; it is content.
        if re.match(r"(?i)(table|figure|panel|appendix|exhibit)\b", text): continue
        others = blocks[:i] + blocks[i+1:]
        if b[3] <= height*MARGIN_BAND and not any(o[3] <= b[1] for o in others): edges.append(b)
        elif b[1] >= height*(1-MARGIN_BAND) and not any(o[1] >= b[3] for o in others): edges.append(b)
    return edges


def _margin_key(text: str) -> str:
    return re.sub(r"\d+", "#", " ".join(text.split())).casefold()


def margin_blocks(doc) -> set[tuple[int, tuple]]:
    """(page index, bbox) of running heads, feet and page numbers.

    Journals alternate odd/even heads, so a head repeating on a fifth of the
    pages counts. A lone number counts only when it fits the document's page
    numbering, inferred from at least three pages.
    """
    legacy = repeating_margins(doc)
    edges = [_edge_blocks(page) for page in doc]
    keys = Counter(key for blocks in edges for key in {_margin_key(b[4]) for b in blocks})
    offsets = Counter()
    for index,blocks in enumerate(edges):
        numbers = {int(line) for b in blocks for line in b[4].split() if line.isdigit() and len(line) <= 4}
        offsets.update(n - (index + 1) for n in numbers)
    offset, support = offsets.most_common(1)[0] if offsets else (None, 0)
    excluded = set()
    for index,page in enumerate(doc):
        height = page.rect.height
        for b in page.get_text("blocks"):
            if (b[1] <= 45 or b[3] >= height - 40) and re.sub(r"\d+", "#", b[4].strip()) in legacy:
                excluded.add((index, tuple(b[:4])))
        for b in edges[index]:
            text = b[4].strip()
            if keys[_margin_key(text)] >= max(3, len(doc)*.2) or (
                support >= 3 and text.isdigit() and int(text) == index + 1 + offset
            ):
                excluded.add((index, tuple(b[:4])))
    return excluded


def merge_broken_words(texts: list[str]) -> list[str]:
    """Join a block that ends in ``word-`` to the next block when it continues in lowercase.

    Blocks are normalized one at a time, so a word broken across two blocks
    (many PDFs store every line as its own block) would keep its hyphen and
    split the paragraph. The merged text keeps the line break for the
    hyphenation rule to judge.
    """
    merged = []
    for text in texts:
        if merged and re.search(r"[A-Za-z]-\s*$", merged[-1]) and re.match(r"\s*[a-z]", text):
            merged[-1] = merged[-1].rstrip() + "\n" + text.lstrip()
        else:
            merged.append(text)
    return merged


def layout_markdown(doc, normalize) -> tuple[str, list[dict]]:
    margins = margin_blocks(doc); pages = []; mapping = []
    vocabulary = Counter(run.casefold() for page in doc for b in page.get_text("blocks") if len(b) < 7 or b[6] == 0
                         for run in re.findall(r"[A-Za-z]+", b[4]))
    for number,page in enumerate(doc, 1):
        parts = []; records = []; seen = set()
        for i,b in enumerate(restore_missing_spaces(page, ordered_blocks(page), vocabulary)):
            margin = (number - 1, tuple(b[:4])) in margins
            # Some PDFs draw the same text twice at one position (e.g. for bolding).
            duplicate = not margin and (tuple(b[:4]), b[4]) in seen
            seen.add((tuple(b[:4]), b[4]))
            records.append({"id": f"p{number}-b{i}", "bbox": list(b[:4]), "text": b[4],
                            "excluded_margin": margin, "excluded_duplicate": duplicate})
            if not margin and not duplicate: parts.append(b[4])
        mapping.append({"page": number, "blocks": records})
        pages.append(f"*(Page {number})*\n\n" + "\n\n".join(normalize(text).strip() for text in merge_broken_words(parts)))
    return "\n\n".join(pages).rstrip() + "\n", mapping


def assemble_inline(mapping: list[dict], equations, tables, normalize) -> tuple[str, list[dict]]:
    """Replace only fully contained, unambiguous blocks, and keep every other block.

    Partial block overlaps are deliberately refused. An audit entry explains why
    a repair remains a candidate rather than an authoritative duplicate.
    """
    from pdf_md_convert.table_regions import table_markdown
    events = []; pages = []
    for source in mapping:
        number = source["page"]; blocks = source["blocks"]
        edits = {}; consumed = set()
        regions = [("table", r) for r in tables if r.page == number] + [("equation", r) for r in equations if r.page == number]
        for kind,region in regions:
            key = getattr(region, "tag", getattr(region, "number", ""))
            event = {"page": number, "kind": kind, "key": key, "status": "unresolved", "bbox": region.bbox}
            events.append(event)
            content = region.latex if kind == "equation" else region.extracted_text
            if not content or region.confidence not in ({"high"} if kind == "equation" else {"high", "medium"}):
                event["reason"] = "insufficient-reconstruction-confidence"; continue
            if kind == "table" and not any(line.startswith("|") for line in content.splitlines()):
                event["reason"] = "coordinate-text-not-structured-table"; continue
            rect = fitz.Rect(getattr(region, "body_bbox", None) or region.bbox); selected = []; partial = False
            for i,block in enumerate(blocks):
                box = fitz.Rect(block["bbox"]); overlap = box & rect
                if overlap.is_empty: continue
                if overlap.get_area() / max(box.get_area(),1) < .97: partial = True
                elif not block["excluded_margin"]: selected.append(i)
            if partial or not selected or consumed.intersection(selected):
                event["reason"] = "ambiguous-or-overlapping-source-boundaries"; continue
            # A table reconstruction must conserve every numeric token in its source blocks.
            raw = "\n".join(blocks[i]["text"] for i in selected)
            replacement = region.latex if kind == "equation" else table_markdown(region)
            numeric = lambda s: Counter(re.findall(r"(?<!\w)[-+−]?\d+(?:[.,]\d+)*", s.replace("−","-")))
            if kind == "table" and numeric(raw) - numeric(replacement):
                event["reason"] = "table-numeric-content-not-conserved"; continue
            if kind == "equation":
                source_compact = re.sub(r"\s+", "", normalize(raw))
                expected = re.sub(r"\s+", "", normalize(region.raw_text))
                if source_compact != expected:
                    event["reason"] = "equation-source-span-mismatch"; continue
            start = min(selected)
            edits[start] = f"<!-- source-region: p{number}-{kind}-{key} -->\n{replacement}"
            consumed.update(selected)
            event.update(status="reconstructed", source_blocks=[blocks[i]["id"] for i in selected])
        output = []; run = []
        # Broken words are rejoined only between blocks kept as text, never across a replaced region.
        for i,block in enumerate(blocks):
            if i in consumed:
                output += [normalize(text).strip() for text in merge_broken_words(run)]; run = []
                if i in edits: output.append(edits[i])
            elif not block["excluded_margin"] and not block.get("excluded_duplicate"):
                run.append(block["text"])
        output += [normalize(text).strip() for text in merge_broken_words(run)]
        pages.append(f"*(Page {number})*\n\n" + "\n\n".join(output))
    return "\n\n".join(pages).rstrip() + "\n", events
