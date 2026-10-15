# Limitations

- Image-only scans do not become reliable text without OCR or external visual transcription. The base converter flags low-text pages.
- Damaged embedded font encodings can lose signs, integral operators, fractions, or equation tags even when the page image is readable. Control-code glyphs are shown as `�` and listed in `damaged_text_pages`; fonts that map symbols to other valid characters (arrows, punctuation, other scripts) are only partly detectable and block automatic equation repair rather than being corrected.
- The balanced profile crops at most 40 equations per paper. Numbered displays are taken first, so in equation-heavy papers some unnumbered displays receive no crop; raise `--max-equation-crops` when they matter.
- Complex stacked equations, multiple fraction bars, and integral/summation limits may remain unresolved. Confidence is not mathematical proof.
- Dense, borderless, merged-cell, definition, and multi-page tables may need manual checks. Numeric conservation does not prove correct column associations.
- Two-column reading-order detection is deliberately conservative. Ambiguous layouts preserve source blocks rather than guess.
- Figures are not automatically digitized into exact data. Captions and crops are evidence; do not imply exact numeric extraction.
- Appendix references, bibliographic references, and footnotes can confuse exhibit matching. Inspect reported gaps against supplied source files.
- Inline repair placement is experimental and opt-in. Appendices preserve raw body text but may duplicate a reconstructed exhibit.
- Legacy cleanup-equations is a mechanical draft tool, not semantic verification; never use its output without visual review.
- Model-backed Marker processing is optional, slower, and subject to separate dependencies/model terms. Budgets do not forcibly interrupt an in-flight native/model operation.
- A passed automatic check is not certification. Broad text/equation/table accuracy improvement has not been established.
