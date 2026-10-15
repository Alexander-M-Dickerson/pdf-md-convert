# Architecture and extension points

## Conversion path

`pdf-md-convert batch` dispatches through `commands.py` to `api.convert_batch`. Preflight discovers PDF inputs, checks output collisions and ownership, hashes sources, and validates resumable artifacts. Each document receives its own artifact directory and attempt identifier. The public Markdown path retains the original PDF stem and relative directory.

`hybrid_backend.convert_hybrid` runs PyMuPDF embedded-text extraction, detects residual risks, and performs bounded deterministic equation/table repair. The default batch profile explicitly selects this backend even when optional layout or OCR packages are installed. Source geometry and rendered crops provide evidence; uncertain reconstructions remain in the review queue. Existing engine modules remain available for diagnostics and controlled experiments.

The batch layer converts generated artifact references to relative paths, publishes the primary Markdown, records artifact hashes, and updates its manifest after every document. It catches document-level failures so later files still run. It does not parallelize documents: use independent output directories for independent processes. Do not introduce shared-output concurrency without adding a locking and recovery design.

## Review path

`agent_tools.review_workpack` checks source hashes, copies deterministic Markdown into a separate review tree, and collects uncertain regions, missing exhibits, and risky pages. Additional page rendering is capped. Unrendered tasks remain explicitly pending. The tool creates instructions and evidence; it does not invoke an assistant or claim to have reviewed anything.

An assistant follows the bundled skill, inspects source images, edits reviewed copies, and records corrections with page and source provenance. Review status and semantic accuracy require explicit evidence. A completed conversion, a source crop, or high agreement with another generated manuscript cannot establish accuracy on its own.

## Code map

| Area | Modules |
|---|---|
| Public API and batch contracts | `api.py` |
| Command dispatch and diagnostics | `cli.py`, `commands.py`, `legacy_cli.py` |
| Conversion and residual detection | `hybrid_backend.py`, `layout.py` |
| Equation and table geometry | `equation_regions.py`, `math_geometry.py`, `table_regions.py` |
| Optional OCR adapter | `marker_backend.py` |
| Agent exports and review evidence | `agent_tools.py`, `resources/agent_bundle/` |
| Legacy audit workpacks | `convert_pdf.py`, `prepare_workpack.py`, `draft_raw_chunks.py`, `assemble_markdown.py` |
| Validation and experiments | `reference_validation.py`, `verified_accuracy.py`, `benchmark.py`, `review_pack.py` |

## Making improvements

Freeze input hashes, dependencies, code, baseline outputs, and development/holdout assignments before modifying extraction. Change one narrow behavior, add a meaningful synthetic regression for the failure, and measure text, equation, and table fidelity separately against reviewed source regions. Repeat runtime measurements with alternating execution order. Measure public batch overhead as well as the extraction engine. Preserve failed and ambiguous cases in denominators.

The package currently exposes agent-guided augmentation rather than a model-provider API. A future automatic provider should be an optional adapter with explicit user configuration, document-sharing controls, per-region budgets, recorded model/version provenance, and source-based validation. It must not become a hidden dependency of ordinary local conversion.
