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


def layout_markdown(doc, normalize) -> tuple[str, list[dict]]:
    margins = repeating_margins(doc); pages = []; mapping = []
    for number,page in enumerate(doc, 1):
        parts = []; records = []
        for i,b in enumerate(ordered_blocks(page)):
            margin = (b[1] <= 45 or b[3] >= page.rect.height - 40) and re.sub(r"\d+", "#", b[4].strip()) in margins
            records.append({"id": f"p{number}-b{i}", "bbox": list(b[:4]), "text": b[4], "excluded_margin": margin})
            if not margin: parts.append(normalize(b[4]).strip())
        mapping.append({"page": number, "blocks": records})
        pages.append(f"*(Page {number})*\n\n" + "\n\n".join(parts))
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
        output = []
        for i,block in enumerate(blocks):
            if i in edits: output.append(edits[i])
            elif i not in consumed and not block["excluded_margin"]: output.append(normalize(block["text"]).strip())
        pages.append(f"*(Page {number})*\n\n" + "\n\n".join(output))
    return "\n\n".join(pages).rstrip() + "\n", events
