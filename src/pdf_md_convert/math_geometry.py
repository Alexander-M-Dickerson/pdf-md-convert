"""Conservative, local reconstruction from encoded characters and their geometry."""
from __future__ import annotations

import re
import unicodedata
import fitz


GREEK = dict(zip("αβγδεζηθικλμνξπρστυφχψωΓΔΘΛΞΠΣΥΦΨΩ", [
    "alpha", "beta", "gamma", "delta", "epsilon", "zeta", "eta", "theta", "iota", "kappa", "lambda", "mu",
    "nu", "xi", "pi", "rho", "sigma", "tau", "upsilon", "phi", "chi", "psi", "omega",
    "Gamma", "Delta", "Theta", "Lambda", "Xi", "Pi", "Sigma", "Upsilon", "Phi", "Psi", "Omega"]))
OPERATORS = {"×": "times", "∝": "propto", "→": "to", "∞": "infty", "∈": "in", "∑": "sum", "∏": "prod",
             "∫": "int", "≤": "le", "≥": "ge", "≠": "ne", "≈": "approx", "∂": "partial", "∇": "nabla"}
SUPERS = dict(zip("⁰¹²³⁴⁵⁶⁷⁸⁹⁺⁻⁼⁽⁾ⁿⁱ", "0123456789+-=()ni"))
SUBS = dict(zip("₀₁₂₃₄₅₆₇₈₉₊₋₌₍₎ₐₑₕᵢⱼₖₗₘₙₒₚᵣₛₜᵤᵥₓ", "0123456789+-=()aehijklmnoprstuvx"))


def encode_char(char: str) -> str:
    if char in SUPERS: return "^{" + SUPERS[char] + "}"
    if char in SUBS: return "_{" + SUBS[char] + "}"
    name = unicodedata.name(char, "")
    base = unicodedata.normalize("NFKC", char)
    if base in GREEK: value = "\\" + GREEK[base] + "{}"
    elif base in OPERATORS: value = "\\" + OPERATORS[base] + "{}"
    elif char in {"−", "–"}: value = "-"
    else: value = base
    if "MATHEMATICAL" in name and "BOLD" in name: value = r"\boldsymbol{" + value + "}"
    elif "DOUBLE-STRUCK" in name: value = r"\mathbb{" + value + "}"
    elif "SCRIPT" in name and "MATHEMATICAL" in name: value = r"\mathcal{" + value + "}"
    elif "FRAKTUR" in name: value = r"\mathfrak{" + value + "}"
    return value


def encode_math(text: str) -> str:
    out = []; i = 0
    while i < len(text):
        mapping = SUPERS if text[i] in SUPERS else SUBS if text[i] in SUBS else None
        if mapping is not None:
            values = []; operator = "^" if mapping is SUPERS else "_"
            while i < len(text) and text[i] in mapping:
                values.append(mapping[text[i]]); i += 1
            out.append(operator + "{" + "".join(values) + "}")
        else:
            out.append(encode_char(text[i])); i += 1
    return re.sub(r"\s+", " ", "".join(out)).strip()


def balanced(text: str) -> bool:
    stack = []
    for char in text:
        if char in "({[": stack.append(char)
        elif char in ")}]":
            if not stack or "({["[")}]".index(char)] != stack.pop(): return False
    return not stack


def chars_in_rect(page, rect: fitz.Rect) -> list[dict]:
    chars = []
    for block in page.get_text("rawdict").get("blocks", []):
        bounds = block.get("bbox", (0,0,0,0))
        if bounds[2] < rect.x0 or bounds[0] > rect.x1 or bounds[3] < rect.y0 or bounds[1] > rect.y1: continue
        for line in block.get("lines", []):
            if tuple(line.get("dir", (1, 0))) != (1, 0): continue
            for span in line["spans"]:
                for char in span["chars"]:
                    x0,y0,x1,y1 = char["bbox"]
                    if rect.x0 <= (x0+x1)/2 <= rect.x1 and rect.y0 <= (y0+y1)/2 <= rect.y1:
                        chars.append({**char, "size": span["size"]})
    return chars


def render_line(chars: list[dict]) -> str | None:
    if not chars: return None
    sizes = sorted(c["size"] for c in chars if not c["c"].isspace())
    if not sizes: return None
    size = sizes[len(sizes)//2]
    base_chars = [c for c in chars if c["size"] >= size * .95 and not c["c"].isspace()]
    baselines = sorted(c["origin"][1] for c in base_chars)
    base = baselines[len(baselines)//2]
    if max(baselines)-min(baselines) > size * .35: return None
    out = []; script = None
    for c in sorted(chars, key=lambda c:c["bbox"][0]):
        delta = c["origin"][1] - base
        mode = "^" if delta < -.18*size and c["size"] < .95*size else "_" if delta > .12*size and c["size"] < .95*size else None
        if abs(delta) > size and mode is None: return None
        if mode != script:
            if script: out.append("}")
            if mode: out.append(mode + "{")
            script = mode
        out.append(encode_char(c["c"]))
    if script: out.append("}")
    return "".join(out).strip()


def reconstruct(page, rect: fitz.Rect, tag: str) -> tuple[str | None, list[str]]:
    if any((fitz.Rect(info["bbox"]) & page.rect).get_area() > .8*page.rect.get_area()
           for info in page.get_image_info()):
        return None, ["image-page-text-layer-not-semantic-evidence"]
    chars = chars_in_rect(page, rect)
    if not chars: return None, ["no-encoded-characters"]
    # Remove a visible parenthesized tag, only once and only at the right edge.
    raw = "".join(c["c"] for c in chars)
    matches = list(re.finditer(r"\(\s*" + re.escape(tag) + r"\s*\)", raw)) if tag else []
    if matches:
        m = matches[-1]; tag_x = min(c["bbox"][0] for c in chars[m.start():m.end()])
        if tag_x >= rect.x0 + .65*rect.width:
            chars = chars[:m.start()] + chars[m.end():]
        else: return None, ["ambiguous-tag-location"]
    elif tag and not tag.startswith("unnumbered-"):
        return None, ["tag-not-geometrically-isolated"]
    raw = "".join(c["c"] for c in chars)
    if re.search(r"[\x00-\x08\x0b-\x1f\ufffd]", raw): return None, ["unknown-encoded-symbol"]
    if re.search(r"\b(where|which|therefore|Table|Figure|Furthermore)\b", raw, re.I): return None, ["prose-in-equation-region"]
    bars = []
    for drawing in page.get_drawings():
        for item in drawing["items"]:
            if item[0] != "l": continue
            a,b = item[1:3]
            if abs(a.y-b.y) < .7 and abs(a.x-b.x) > 6 and rect.contains(a) and rect.contains(b):
                bars.append((min(a.x,b.x), a.y, max(a.x,b.x)))
    if len(bars) == 1:
        x0,y,x1 = bars[0]
        above = [c for c in chars if x0-2 <= (c["bbox"][0]+c["bbox"][2])/2 <= x1+2 and c["origin"][1] < y]
        below = [c for c in chars if x0-2 <= (c["bbox"][0]+c["bbox"][2])/2 <= x1+2 and c["origin"][1] > y]
        other = [c for c in chars if c not in above and c not in below]
        numerator = render_line(above); denominator = render_line(below)
        if numerator and denominator and other:
            before = [c for c in other if c["bbox"][2] <= x0+2]
            after = [c for c in other if c not in before]
            prefix = render_line(before) if before else ""; suffix = render_line(after) if after else ""
            if prefix is not None and suffix is not None:
                value = prefix + r"\frac{" + numerator + "}{" + denominator + "}" + suffix
                if balanced(value): return value, ["geometry-single-fraction"]
        return None, ["ambiguous-fraction-geometry"]
    if bars: return None, ["multiple-fraction-bars"]
    value = render_line(chars)
    if value and balanced(value) and re.search(r"\S\s*(?:=|\\propto\{\})\s*\S", value):
        return value, ["geometry-single-baseline"]
    return None, ["unsupported-or-ambiguous-equation-geometry"]
