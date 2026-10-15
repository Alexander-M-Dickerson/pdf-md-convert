# Local release readiness: 0.2.0rc1

Prepared 2026-10-08. This is a locally tested release candidate, not a published release or a claim of source-perfect conversion.

## Checks executed

- Windows, Python 3.12.12 and Python 3.14.3: **99 tests passed, 1 skipped** on each interpreter. The skip requires unavailable Windows symlink-creation permission.
- Bytecode compilation passed for source, tests, and release tools. The dependency lock is current.
- Base environments use PyMuPDF 1.27.2.3 and ftfy 6.3.1; no Marker or layout package is installed.
- Wheel and source-distribution installation were exercised from fresh environments outside the source checkout: console/module entry points, doctor, recursive batch, resume, review workpack, agent export, legacy audit workpack, reference indexing and benchmark commands.
- Distribution metadata, license/agent resources, archive allowlists, documentation links, and exclusion of private files were checked.

## Private conversion regression

The frozen corpus contains 66 papers and 3,415 pages. Inputs and detailed artifacts remain local and are excluded from every distribution.

| Measurement | Frozen engine baseline | Packaged engine | Public batch API |
|---|---:|---:|---:|
| Median batch seconds | 76.13 | 78.96 | 73.02 |
| p95 per-paper median seconds | 2.23 | 2.28 | 2.05 |

The engine used three alternating baseline/candidate repeats. Three public API repeats followed with the same dependencies and balanced appendix profile; their whole-call times were 73.02, 72.61, 85.62 seconds. Public API whole-call times include preflight, hashes, portable paths and manifest writes. Per-paper timings exclude batch preflight/bookkeeping. Different timing boundaries and sequential API measurement mean this is a regression check, not evidence of a speed improvement. Both median and p95 passed the no-more-than-20%-slower gate.

All 66 papers completed in all successful short-path runs. Retained engine Markdown matched the baseline for all 66 papers after normalizing generated paths and source-copy provenance. All 1,835 primary-Markdown crop/TSV references resolved.

Automated QA retained the same baseline coverage flags: one table and two equation crops. Source inspection classified the table as an absent Internet Appendix reference split across two pages. Both equations are present in their PDFs and were rendered separately for local review; their automatic crop-detection gaps remain unfixed. The engine also reports 270 missing high-confidence equation repairs. These are unresolved reconstruction work, not source limitations. The benchmark outputs are drafts, not final reviewed manuscripts; the package accurately exposes these queues rather than claiming all-clear quality.

The ten previously source-reviewed regions across three papers remained unchanged: text 15/224 errors, equation reconstruction 200/215, table reconstruction 20/48. **Broad accuracy acceptance remains incomplete.** These high equation reconstruction error counts must not be interpreted as solved math extraction. See [validation](validation.md) for scoring scope and historical comparison. Packaging did not establish an accuracy improvement.

## Limitations and unexecuted checks

- Deep Windows staging paths initially caused native crop writes and a test artifact write to fail. Repeating with shorter local paths resolved them without modifying the extraction engine. Use short output/temp roots; native libraries can retain path-length limits.
- Optional OCR/layout installation, model downloads, GPU execution, and automatic AI providers were not tested or enabled.
- Python 3.10 and Linux/macOS are covered by authored CI, not executed local results. No remote CI job has run as part of this preparation.
- Synthetic tests cannot represent every academic layout. Scans, ambiguous equations and complex tables still need source review; inline repairs remain experimental.
- No remote was created and nothing was pushed, uploaded, or published. The delivered project contains a local Git repository only. Local delivery/environment smoke checks are recorded in ignored `.local/delivery-receipt.json`.

## Artifacts

`dist/` contains a wheel, source distribution, clean source-project ZIP and `SHA256SUMS.txt`. The ZIP is generated from the source distribution's allowlisted contents, excluding Git history, environments, private inputs, conversions and local regression results. See [release procedure](release.md) to reproduce packaging checks.
