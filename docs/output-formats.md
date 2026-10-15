# Output formats

The new batch workflow uses schema version 1. Inputs remain untouched.

| Artifact | Meaning |
|---|---|
| `<relative source stem>.md` | Deterministic output, exact filename stem |
| `_batch_manifest.json` | Source hashes, conversion signatures, operation state, artifact inventories and hashes |
| `_batch_qa_summary.md/.json` | Current invocation execution and quality summary |
| `_artifacts/<id>/<attempt>/` | Isolated engine output and evidence |
| `*_residuals.json` | Risks, equation/table regions, confidence reasons, placement decisions and budgets |
| `_audit/<stem>/raw.md` | Original fast extraction before repairs |
| `_audit/<stem>/source_map.json` | Original text blocks and coordinates |
| `_audit/<stem>/repair_candidates.md` | Structured repair candidates; low-confidence equation metadata remains in residual JSON |
| `_equation_crops`, `_table_crops` | Rendered visual evidence |
| `_table_tsv` | Table sidecars, preserving empty cells |
| `_review/tasks.json` | Unverified review tasks and source hashes |
| `_review/reviewed/` | Independent drafts for semantic editing |
| `_review/corrections.json`, `handover.md` | Correction provenance and remaining limitations |

Manifest output paths and structured residual artifact paths are relative to the batch output root. Markdown artifact references are relative to the Markdown file. Original source paths are provenance and remain absolute; moving outputs does not move the source PDF. Move the complete output tree to preserve internal links. To review on another machine, provide the original matching PDFs and update source locations deliberately after verifying their hashes.

The primary Markdown and all managed sidecars are hashed for resume validation. Do not edit manifest hashes to bypass a conflict. Old artifact attempts are retained on overwrite and are not the current output inventory. Deleting old attempts is an explicit maintenance task, not an automatic batch side effect.

QA fields in the manifest and summaries include missing referenced tables and equation crops (hard findings), external appendix references, the equation reconstruction queue, low-text pages, `damaged_text_pages` (pages whose font encoding produced unreadable glyphs, shown as `�` in the Markdown; use the source image there) and `missing_crop_images` (evidence images that could not be written). Appendix-lettered tables are reported as external rather than missing only when the paper says its appendix is published separately and no caption for them appears in the PDF.

`needs_review` does not distinguish a correct passage from an incorrect one: it means automated extraction has not established whole-document accuracy. A reviewed subset must identify its scope. Raw text, region detection, crop capture, reconstruction confidence, and visual verification are different forms of evidence.

Legacy `hybrid` outputs keep their existing report conventions; the new batch manifest applies to `batch` only.
