# Validation and performance

## Separate three questions

1. Did the program complete and produce its expected artifacts?
2. Does the Markdown agree with an existing derivative manuscript?
3. Does the content match the rendered PDF?

Only the third establishes source fidelity. Expensive AI-generated manuscript.md files can contain useful corrections and also enrichment, mistakes, or unavailable figure data.

```sh
pdf-md-convert reference-index --root ./reference-corpus --output-dir ./catalog --history-root ./old-results
pdf-md-convert compare-reference --candidate ./converted/paper.md --reference ./reference/manuscript.md --pdf ./reference/manuscript.pdf --output-dir ./comparison
pdf-md-convert prepare-review --manifest ./catalog/benchmark_manifest.json --split development --output-dir ./source-review
pdf-md-convert experiment --manifest ./catalog/benchmark_manifest.json --split development --baseline-code ./baseline-checkout --output-dir ./benchmark --repeats 3 --compare-references --verified-references ./source-review/verified_regions.json
```

The indexer expects the documented academic corpus convention: `articles/**/manuscript.pdf` or `papers/**/manuscript.pdf` with adjacent manuscript.md, plus top-level PDFs for general sampling. Its default frozen sample needs at least 20 unused general candidates and 12 additional paired candidates. Use `reference-index --help` to reduce sample counts for smaller corpora. Arbitrary recursive conversion uses `batch --recursive` instead.

Reference comparison separates text, equations and table cells; excludes recognized AI enrichment; preserves unmatched content in denominators; and flags ambiguous alignment. Annotations may add reviewed exclusions or corrections with source evidence and frozen hashes. Unrecognized enrichment requires review. Missing structured tables/equations are missing reconstructions even when fragments remain in raw text.

Source-review drafts start unverified. Confirm the correct page, expected text/math/cells, image hash and review note. The existing acceptance suite requires four text passages, three equations and two table regions per paper, unless a category is genuinely absent and documented. Incomplete review cannot pass. Source-based unavailable reasons must not stand in for unfinished work.

Accuracy improvement gates require at least 10% relative error reduction separately for text, equations and tables, or preservation of zero error. Speed gates compare median batch and p95 per-paper median times, each no more than 20% above baseline. Repeats alternate baseline/candidate order. Holdouts used to develop a fix become regression data. Code hash changes during timing invalidate that run. CLI completion of an experiment only means reports were written; inspect the gates in JSON.

## Historical evidence, not a general accuracy claim

The private pre-packaging 66-paper corpus had 3,415 pages. Three paired runs gave median 69.52 seconds versus 72.09 seconds for an earlier engine. Those source documents are not distributed and this result is not publicly reproducible without them.

A source-verified smoke suite of ten regions across three papers measured: text 15/224 errors before and after; equation reconstruction 190/215 to 200/215; table reconstruction 48/48 to 20/48. This establishes a bounded table improvement, not broad accuracy improvement. The conservative equation recognizer rejects some plausible but unsupported text. Full accuracy acceptance remains incomplete.

Public tests use original synthetic content and expected results. They test contracts and known failure shapes, not the full diversity of academic publishing. The local release-readiness report records packaging-specific checks actually run.

## Measure the public batch layer

For packaging regressions, first run the engine experiment above with `--repair-placement appendix` and a frozen baseline checkout. Then run:

```sh
python tools/check_batch_regression.py --manifest ./catalog/benchmark_manifest.json --engine-runs ./benchmark --work-dir ./batch-check --verified-references ./source-review/verified_regions.json --repeats 3
```

Use the same full manifest for both runs (`--split all` on the engine experiment). The helper copies PDFs into an isolated input tree, runs the public API, records whole-call and per-paper times, compares retained engine Markdown, and compares previously reviewed regions. It keeps all source files intact. Use short local paths on Windows. Private paths and content appear in its local reports, which must remain outside public version control.

These API repeats follow the paired engine runs; they are not alternated with a fresh baseline. Treat the result as a bounded regression check. Inspect both speed and fidelity gates rather than relying on command exit alone. Current results are in [release readiness](release-readiness.md).
