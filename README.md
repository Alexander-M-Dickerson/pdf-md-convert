# pdf-md-convert

Fast local academic PDF-to-Markdown conversion, with evidence for human and AI-assisted review.

The Python engine extracts embedded text, performs capped deterministic equation/table repairs, and retains source crops, table TSVs, raw text, and QA findings. An AI assistant can then review difficult regions against the PDF. **The package does not call an AI service.** Normal conversion needs no API key, OCR model, GPU, or network connection after installation.

**Release candidate: 0.2.0rc1.** Conversion completion is not verified accuracy. Scans, damaged math encodings, and complex tables may need visual review. Inline repairs are experimental. See [limitations](docs/limitations.md) before relying on equations or numeric tables.

## Install from this project

Recommended: Python 3.12 and [uv](https://docs.astral.sh/uv/getting-started/installation/).

```powershell
uv sync --no-default-groups --python 3.12
uv run --no-default-groups pdf-md-convert doctor
uv run --no-default-groups pdf-md-convert batch "C:\papers" --recursive
```

The last command writes to `C:\papers\papers_md`. Exact PDF stems and relative subfolders are preserved. On Linux/macOS, substitute a path such as `./papers`; the `uv` commands are the same.

Alternatively, with an active Python virtual environment:

```sh
python -m pip install .
pdf-md-convert batch ./papers --output-dir ./converted --recursive
```

No package has been uploaded to PyPI by this release preparation. Install from this project or the supplied local wheel. See [installation](docs/installation.md), including wheel installation and optional dependencies.

## First batch

```sh
pdf-md-convert batch ./papers --output-dir ./converted --recursive
pdf-md-convert batch ./papers --output-dir ./converted --recursive --resume
pdf-md-convert review-workpack ./converted --max-pages 20
```

`balanced` is the default: PyMuPDF, up to 40 equation crops and 35 table crops, 10-second repair-stage budgets, and a 30-second total repair budget per PDF. OCR is off. Use `--profile fast` for embedded text without repair crops. `--repair-placement inline` opts into experimental source-position replacement.

Reruns do not silently replace existing Markdown. `--resume` reuses outputs only when recorded hashes and settings match; use a new output directory for another experiment. `--overwrite` explicitly regenerates the selected Markdown while retaining old artifact attempts.

## Outputs

```text
converted/
  First paper.md
  subfolder/Second paper.md
  _batch_manifest.json
  _batch_qa_summary.md
  _batch_qa_summary.json
  _artifacts/<document-id>/<attempt>/    # raw text, source mapping, crops, TSVs, reports
  _review/                             # only created by review-workpack
    review.md
    tasks.json
    corrections.json
    handover.md
    reviewed/                          # independent drafts for human/AI editing
```

Start with `_batch_qa_summary.md`. Inspect `_review/review.md` and the source images before correcting equations or tables. A `needs_review` quality status is expected: the converter never certifies an entire manuscript merely because extraction succeeded.

## Use from Python

```python
from pdf_md_convert.api import ConversionOptions, convert_batch

batch = convert_batch(
    "papers",
    output_dir="converted",
    options=ConversionOptions(profile="balanced", recursive=True),
)
for document in batch.results:
    print(document.relative_input, document.status, document.quality_status)
if batch.exit_code:
    print("Inspect the manifest and QA report:", batch.manifest)
```

## Use with an AI assistant

The repository includes `AGENTS.md` and a `convert-paper` skill. To export the user-facing instructions into another workspace:

```sh
pdf-md-convert init-agent --output-dir ./paper-workspace
```

Then ask an assistant with local file and image access:

> Follow the included convert-paper skill. Convert my papers with the balanced profile, prepare a review workpack, and inspect flagged equations and tables against the PDF images. Preserve deterministic outputs; write corrections to the reviewed copies and record unresolved limitations.

This is an agent-guided workflow, not automatic AI execution. Your chosen assistant's file handling and service terms apply when you give it documents. See the [AI workflow](docs/ai-workflow.md).

## Documentation

- [Installation](docs/installation.md) and [batch how-to](docs/batch-guide.md)
- [Python API](docs/python-api.md) and [CLI reference](docs/cli.md)
- [Architecture and extension points](docs/architecture.md)
- [Output formats](docs/output-formats.md) and [AI workflow](docs/ai-workflow.md)
- [Validation](docs/validation.md), [limitations](docs/limitations.md), and [troubleshooting](docs/troubleshooting.md)
- [Runnable examples](examples/README.md), [contributing](CONTRIBUTING.md), and [local release procedure](docs/release.md)
- [Executed release checks and current limitations](docs/release-readiness.md)

## Evidence and license

The pre-packaging implementation completed a local 66-paper, 3,415-page regression in a median 69.52 seconds, versus 72.09 seconds for its earlier baseline. These machine-specific timings are not a performance guarantee. A small source-reviewed sample improved table reconstruction, left text unchanged, and worsened equation reconstruction coverage; broad accuracy acceptance remains incomplete. [Validation details](docs/validation.md)

AGPL-3.0-only. See [LICENSE](LICENSE) and [dependency notices](NOTICE.md). No private papers or model weights are distributed. Rights in input PDFs and their content remain with their owners.
