# Runnable examples

From the source project after installing its base environment:

```sh
uv run --no-default-groups python examples/make_demo.py --output-dir inputs/demo
uv run --no-default-groups pdf-md-convert batch inputs/demo --recursive --output-dir outputs/demo
uv run --no-default-groups pdf-md-convert review-workpack outputs/demo --max-pages 2
uv run --no-default-groups pdf-md-convert batch inputs/demo --recursive --output-dir outputs/demo --resume
```

The generator refuses an existing directory. It creates a text/equation/table example, a rotated nested PDF with the same stem, and an image-only PDF. All content is fictional project-authored material. Expected table cells are in `expected.json`; no private papers or downloaded fonts are needed.

Expect three primary Markdown files, `_batch_manifest.json`, QA summaries, and separate artifact directories. The image-only example should need visual review; an empty embedded-text body is not successful OCR. Open `_review/review.md` to see source evidence and independent reviewed drafts.

With an active environment, `python examples/batch_python.py INPUT --output-dir OUTPUT` uses the public API. PowerShell users can run `examples/batch_cli.ps1 -InputFolder INPUT -OutputFolder OUTPUT`; POSIX users can run `sh examples/batch_cli.sh INPUT OUTPUT`. Use a fresh destination for each example.
