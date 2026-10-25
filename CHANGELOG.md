# Changelog

## Unreleased

Extraction fixes found by converting four recent journal papers and checked against the frozen 66-paper corpus and two fresh random samples of unseen papers (baseline code paired, appendix placement):

- Equation tags: page numbers, running heads, years, section numbers, `R2` labels and table columns are no longer read as equation numbers; tags are capped at three digits and must be balanced or follow display math.
- Equation detection: a prose paragraph can no longer swallow the tagged display beside it; stacked displays with their own tag line and tagged inequalities are kept for review; numbered displays claim the crop budget before unnumbered fragments.
- Geometric LaTeX: base size comes from the relation sign, stacked scripts merge into one group (`r^{j}_{t+1}`), accents attach to the glyph beneath them, and TeX math alphabets (`MSBM`, `CMBX`, `EUFM`) are encoded. Private-use, mis-mapped or overprinted glyphs, prose with statistics, scripts with no base, and displays ending on an operator no longer receive high confidence.
- Text: alternating running heads and page numbers are removed, text drawn twice prints once, and line-break hyphens are rejoined only when the document prints the joined word or the second half is a bound suffix. Unreadable font glyphs appear as `�` and are reported per page.
- Tables: sideways (landscape) tables are read in their own frame, bracketed statistics and long-short columns (`10-1`, `H-L`) are recognized, and repeated panel heads stay out of row labels.
- QA: `damaged_text_pages` and `missing_crop_images`; appendix tables of papers that publish their appendix separately are reported as external. Review workpacks include damaged-text pages.
- Windows: batch stops before converting when generated paths would exceed the path-length limit, and a failed crop write no longer fails the paper.
- Word spaces: lines whose word gaps sit just below MuPDF's space threshold (0.13 to 0.15 of the font size) no longer come out as one token. A body block holding a 15+ letter run found nowhere else in the document is rebuilt from character positions with a space at gaps above 0.13; accents, hyphens, closing punctuation, raised characters and math fonts never get one, and a block keeps its text unless the rebuild reproduces it. On the held-out half of 4,805 JF and RFS papers, glued words fell by 90% with other text error classes unchanged.

## 0.2.0rc1

- Portable local Python package with minimal base dependencies, optional layout/OCR extras, license metadata, and local release artifacts.
- Typed single-file/batch API, recursive discovery, isolated per-document artifacts, safe resume, explicit overwrite, and per-document failure reporting.
- Offline diagnostics, portable agent resource export, and source-evidence review workpacks with independent reviewed drafts.
- Workpack commands no longer depend on a source checkout or a specific Python environment.
- Retain deterministic repairs, geometry caching, rotated-page crops, raw audits, and reference validation.
- Document incomplete source-accuracy acceptance. Inline repairs remain experimental; automatic AI/OCR processing is off by default.

## 0.1.0

Local academic PDF conversion prototype and hybrid extraction/validation workflows.
