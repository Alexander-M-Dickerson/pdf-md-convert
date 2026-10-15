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
             "∫": "int", "≤": "le", "≥": "ge", "≠": "ne", "≈": "approx", "∂": "partial", "∇": "nabla",
             "∣": "mid", "⊤": "top", "⊙": "odot", "⊘": "oslash", "⊕": "oplus", "⊗": "otimes"}
ACCENTS = {
    "̃": "tilde", "˜": "tilde", "̂": "hat", "ˆ": "hat", "̄": "bar", "¯": "bar",
    "ˉ": "bar", "̇": "dot", "˙": "dot", "̈": "ddot", "¨": "ddot", "́": "acute",
    "´": "acute", "̀": "grave", "̌": "check", "ˇ": "check", "̆": "breve", "˘": "breve",
    "̊": "mathring", "˚": "mathring",
}
SUPERS = dict(zip("⁰¹²³⁴⁵⁶⁷⁸⁹⁺⁻⁼⁽⁾ⁿⁱ", "0123456789+-=()ni"))
SUBS = dict(zip("₀₁₂₃₄₅₆₇₈₉₊₋₌₍₎ₐₑₕᵢⱼₖₗₘₙₒₚᵣₛₜᵤᵥₓ", "0123456789+-=()aehijklmnoprstuvx"))


# Regression specifications name variables with words (Markup, Control, Post),
# but never use English function words; two distinct ones make a line prose.
FUNCTION_WORDS = frozenset("""the of and to in is are was were be been if then with when for that this these
    those by as we our it its an or not from at on which where while than there their has have can also such
    so but even each into only between both""".split())


def prose_word_count(text: str) -> int:
    return len({w for w in re.findall(r"[A-Za-z]+", text.lower()) if w in FUNCTION_WORDS})


def relations_only_in_parentheses(text: str) -> bool:
    """True for prose citing a statistic, e.g. "premium of 1.06 bps (t = 1.52)"."""
    depth = 0; found = outside = False
    for ch in text:
        if ch == "(": depth += 1
        elif ch == ")": depth = max(0, depth - 1)
        elif ch in RELATIONS:
            found = True; outside = outside or depth == 0
    return found and not outside


def is_prose_line(text: str) -> bool:
    return prose_word_count(text) >= 2 or relations_only_in_parentheses(text)


RELATIONS = set("=<>≤≥≈∝≡∼≠")
MATH_PUNCTUATION = set("×÷±·…′″‴−–—∣⋆★→←↔⇒⇔↦¯˜ˆǫµ’⁄")
# TeX math alphabets are often plain letters in a styled font (MSBM10 "S" is 𝕊).
FONT_STYLES = (("msbm", "mathbb"), ("bbold", "mathbb"), ("eufm", "mathfrak"), ("rsfs", "mathscr"),
               ("cmbx", "boldsymbol"), ("cmmib", "boldsymbol"))


def precomposed_accent(char: str) -> tuple[str, str] | None:
    """("y", "hat") for ŷ: a Latin letter and one supported accent."""
    parts = unicodedata.normalize("NFD", char)
    if len(parts) == 2 and parts[0].isascii() and parts[0].isalpha() and parts[1] in ACCENTS:
        return parts[0], ACCENTS[parts[1]]
    return None


def encode_char(char: str, font: str = "") -> str:
    if char in SUPERS: return "^{" + SUPERS[char] + "}"
    if char in SUBS: return "_{" + SUBS[char] + "}"
    if accented := precomposed_accent(char):
        return "\\" + accented[1] + "{" + encode_char(accented[0], font) + "}"
    name = unicodedata.name(char, "")
    base = unicodedata.normalize("NFKC", char)
    if base in GREEK: value = "\\" + GREEK[base] + "{}"
    elif base in OPERATORS: value = "\\" + OPERATORS[base] + "{}"
    elif char == "∆": value = r"\Delta{}"  # INCREMENT, typeset as a capital delta
    elif char == "∶": value = ":"  # RATIO
    elif char == "ǫ": value = r"\epsilon{}"  # pdfTeX's Unicode map for the lunate epsilon
    elif char == "’": value = "'"  # a prime set as a closing quote
    elif char == "★": value = r"\star{}"
    elif char == "⁄": value = "/"  # FRACTION SLASH
    elif char == "%": value = r"\%"  # a bare % starts a TeX comment
    elif char in {"−", "–"}: value = "-"
    else: value = base
    if "MATHEMATICAL" in name and "BOLD" in name: value = r"\boldsymbol{" + value + "}"
    elif "DOUBLE-STRUCK" in name: value = r"\mathbb{" + value + "}"
    elif "SCRIPT" in name and "MATHEMATICAL" in name: value = r"\mathcal{" + value + "}"
    elif "FRAKTUR" in name: value = r"\mathfrak{" + value + "}"
    elif char.isascii() and char.isalnum():
        style = next((style for key, style in FONT_STYLES if key in font.casefold()), None)
        if style: value = "\\" + style + "{" + value + "}"
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
                    if not (rect.x0 <= (x0+x1)/2 <= rect.x1 and rect.y0 <= (y0+y1)/2 <= rect.y1): continue
                    # Simulated bold prints each glyph twice with a tiny offset.
                    if any(o["c"] == char["c"] and abs(o["bbox"][0]-x0) < 1 and abs(o["bbox"][1]-y0) < 1
                           for o in chars[-4:]): continue
                    chars.append({**char, "size": span["size"], "font": span["font"]})
    return chars


def unsupported_symbol(text: str) -> bool:
    """True for characters outside the math repertoire, e.g. a font that maps
    alpha to an arrow or italic letters to Hangul."""
    for ch in text:
        if ch.isascii() or ch.isspace() or ch in SUPERS or ch in SUBS or ch in ACCENTS or unicodedata.combining(ch): continue
        if unicodedata.normalize("NFKC", ch).isascii(): continue  # ligatures such as "ﬁ" in variable names
        if precomposed_accent(ch): continue
        cp = ord(ch); name = unicodedata.name(ch, "")
        if ("GREEK" in name or "MATHEMATICAL" in name or 0x2200 <= cp <= 0x22FF or 0x2100 <= cp <= 0x214F
                or 0x27C0 <= cp <= 0x27FF or 0x2980 <= cp <= 0x2AFF or ch in MATH_PUNCTUATION):
            continue
        return True
    return False


def is_accent(char: str) -> bool:
    return bool(unicodedata.combining(char)) or char in ACCENTS


def attach_accents(chars: list[dict]) -> list[dict] | None:
    """Fold accent glyphs into the one base character beneath each of them.

    Accents are separate glyphs, often out of reading order. Each must lie over
    exactly one nearest base; unknown or ambiguous placements return None.
    """
    bases = [dict(c, accents=[]) for c in chars if not is_accent(c["c"])]
    for accent in (c for c in chars if is_accent(c["c"])):
        if accent["c"] not in ACCENTS: return None
        center = (accent["bbox"][0] + accent["bbox"][2]) / 2
        # The base sits just below the accent; a fraction denominator further down does not.
        drop = lambda b: b["origin"][1] - accent["origin"][1]
        under = [b for b in bases if not b["c"].isspace() and b["bbox"][0] - .5 <= center <= b["bbox"][2] + .5
                 and -1 <= drop(b) <= .8 * accent.get("size", 10)]
        distances = sorted((abs(center - (b["bbox"][0] + b["bbox"][2]) / 2), i) for i,b in enumerate(under))
        if not distances or (len(distances) > 1 and distances[1][0] - distances[0][0] < .5): return None
        under[distances[0][1]]["accents"].append(ACCENTS[accent["c"]])
    return bases


def render_line(chars: list[dict]) -> str | None:
    if not chars: return None
    sizes = sorted(c["size"] for c in chars if not c["c"].isspace())
    if not sizes: return None
    # Relations are set at base size; subscripts can outnumber base glyphs.
    relations = [c["size"] for c in chars if c["c"] in RELATIONS]
    size = max(relations) if relations else sizes[len(sizes)//2]
    base_chars = [c for c in chars if c["size"] >= size * .95 and not c["c"].isspace()]
    baselines = sorted(c["origin"][1] for c in base_chars)
    base = baselines[len(baselines)//2]
    if max(baselines)-min(baselines) > size * .35: return None
    # Scripts stacked on one base interleave by x (r, t, j, +1 for r^j_{t+1});
    # collect each run so a base gets one subscript and one superscript group.
    out = []; scripts = {"_": [], "^": []}
    def flush():
        if scripts["_"] or scripts["^"]:
            while out and out[-1].isspace(): out.pop()  # "C _{f}" is C_{f}
        for mode in "_^":
            text = "".join(scripts[mode]).strip()
            if text: out.append(mode + "{" + text + "}")
            scripts[mode].clear()
    for c in sorted(chars, key=lambda c:c["bbox"][0]):
        delta = c["origin"][1] - base
        mode = "^" if delta < -.18*size and c["size"] < .95*size else "_" if delta > .12*size and c["size"] < .95*size else None
        if abs(delta) > size and mode is None: return None
        value = encode_char(c["c"], c.get("font", ""))
        for command in c.get("accents", ()): value = "\\" + command + "{" + value + "}"
        if mode:
            scripts[mode].append(value)
        elif c["c"].isspace():
            if not (scripts["_"] or scripts["^"]): out.append(value)
        else:
            flush(); out.append(value)
    flush()
    return "".join(out).strip()


def incomplete(value: str) -> bool:
    """A display line that ends on an operator continues outside the region."""
    trailing = re.sub(r"[.,;\s]+$", "", value)
    return bool(re.search(r"(?:[-+=<>]|\\times\{\}|\\cdot\{\})$", trailing) or re.match(r"\s*(?:[=+<>]|\\times)", value))


def missing_base(value: str) -> bool:
    """A script group with no base, as when a summation sign or Greek capital was not
    extracted ("+_{k=1}^{K}"), or a percent sign standing in for a lost letter."""
    return bool(re.search(r"(?:^|[=+\-*/(,])\s*[_^]\{", value) or re.search(r"\\%[_^]", value))


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
    # Private-use code points are font-specific glyphs with no defined meaning.
    if re.search(r"[\x00-\x08\x0b-\x1f\ufffd\ue000-\uf8ff]", raw) or unsupported_symbol(raw):
        return None, ["unknown-encoded-symbol"]
    # Accents are separate glyphs placed over a base; reading order alone cannot attach them.
    if any(is_accent(ch) for ch in raw):
        chars = attach_accents(chars)
        if chars is None: return None, ["accent-not-reconstructed"]
    if re.search(r"\b(where|which|therefore|Table|Figure|Furthermore)\b", raw, re.I): return None, ["prose-in-equation-region"]
    if is_prose_line(raw): return None, ["prose-in-equation-region"]
    # Symbol fonts with broken encodings map glyphs to ASCII punctuation never set in a formula.
    if re.search(r"[&?@#$]", raw): return None, ["unknown-encoded-symbol"]
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
                if balanced(value):
                    if incomplete(value): return None, ["incomplete-expression"]
                    if missing_base(value): return None, ["script-without-base"]
                    return value, ["geometry-single-fraction"]
        return None, ["ambiguous-fraction-geometry"]
    if bars: return None, ["multiple-fraction-bars"]
    value = render_line(chars)
    if value and balanced(value) and re.search(r"\S\s*(?:=|\\propto\{\})\s*\S", value):
        if incomplete(value): return None, ["incomplete-expression"]
        if missing_base(value): return None, ["script-without-base"]
        # Without a tag, "Return Portfolio=4" is a label, not a formula.
        if tag.startswith("unnumbered-") and not re.search(r"[_^+*/]|-\s*\S|\\[A-Za-z]+\{", value):
            return None, ["not-a-formula"]
        return value, ["geometry-single-baseline"]
    return None, ["unsupported-or-ambiguous-equation-geometry"]
