# Batch conversion how-to

## Choose an input and output

```sh
pdf-md-convert batch ./papers
pdf-md-convert batch ./papers --recursive --output-dir ./converted
pdf-md-convert batch "./papers/One paper.pdf" --output-dir ./one-paper-output
```

The first command reads immediate-folder PDFs, case-insensitively, and defaults to `papers/papers_md`. A single-file default is a sibling folder named `<PDF stem>_md`. Recursive mode preserves subfolders and exact Markdown filename stems. Active output, known generated work folders, Git/virtual-environment folders, and directory symlinks are excluded. Inputs mapping to reserved report names or case-insensitive output collisions are rejected before conversion. Choose a separate input/output structure for such a conflict; originals are never renamed automatically.

## Choose a profile

| Option | fast | balanced (default) |
|---|---|---|
| Embedded text | yes | yes |
| Equation repair crops | none | up to 40 |
| Table repair crops | none | up to 35 |
| Repair stage budget | not used | 10 seconds |
| Total repair budget | not used | 30 seconds per PDF |
| OCR/model calls | none | none |

Capped fallback detection also uses the total deadline. An in-flight native library call cannot be forcibly interrupted safely; reports record overruns. Explicit crop/budget options override profile values. Typical papers finish well before these limits.

```sh
pdf-md-convert batch ./papers --profile fast --output-dir ./fast-output
pdf-md-convert batch ./papers --max-equation-crops 20 --max-table-crops 20 --time-budget 5 --output-dir ./bounded-output
```

Appendix placement is the default. `--repair-placement inline` is experimental: only conservatively bounded reconstructions replace source blocks; ambiguous regions stay raw and are retained in audit data.

## Continue or regenerate

```sh
pdf-md-convert batch ./papers --recursive --output-dir ./converted --resume
pdf-md-convert batch ./papers --recursive --output-dir ./converted --overwrite
```

Resume reuses only completed entries with matching source, settings, version/dependencies, and output hashes. Edited Markdown or missing/edited sidecars cannot silently pass as valid completed output. A new input root must use a separate output directory. Overwrite regenerates selected primary Markdown and creates another isolated artifact attempt; old artifacts and `_review` edits remain intact. Review workpacks must be checked against the manifest/version they were created from; do not reuse stale reviews after regeneration.

Interrupt with Ctrl+C. Completed state is saved, the current document is marked interrupted, and exit status is 130. A later resume reuses valid completed documents. If an interruption occurred during final output publication and the output cannot be verified, use a new output directory rather than assume it is complete.

## Understand completion

Individual document errors are recorded and the batch continues. Read `_batch_manifest.json`, `_batch_qa_summary.md`, and per-document residual reports. Quality remains `needs_review` even on operational success.

The batch command writes its JSON result to stdout and backend diagnostics to stderr, so scripts can parse the result without treating library notices as JSON.

| Exit | Meaning for batch |
|---|---|
| 0 | Conversion operations completed; review may remain |
| 1 | One or more document operations failed |
| 2 | Invalid arguments, input/output preflight, or existing-output conflict |
| 3 | `--strict-qa` found missing referenced tables/equation crops |
| 130 | Interrupted |

Absent external appendices and low-confidence reconstruction queues are separate from hard missing-in-PDF findings. Neither a zero exit nor a crop count proves semantic accuracy. Run only one writer per output directory; use separate output directories for concurrent batches.
