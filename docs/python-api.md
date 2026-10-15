# Python API

```python
from pathlib import Path
from pdf_md_convert.api import ConversionOptions, convert_file, convert_batch

options = ConversionOptions(profile="balanced", recursive=True)
batch = convert_batch("papers", output_dir="converted", options=options)
for item in batch.results:
    if item.status == "failed":
        print(item.relative_input, item.error)
    else:
        print(Path(batch.output_dir) / item.output_markdown)

single = convert_file("one.pdf", output_dir="one-output")
print(single.status, single.qa)
```

`ConversionOptions` fields: `profile` (`balanced` or `fast`), `recursive`, `resume`, `overwrite`, `strict_qa`, `repair_placement` (`appendix` or experimental `inline`), optional `max_equation_crops`, optional `max_table_crops`, `time_budget` (10), and `max_total_repair_seconds` (30). Resume and overwrite are mutually exclusive. `resolved()` returns the effective deterministic engine settings.

`BatchResult`: absolute `output_dir` and `manifest`, selected document `results`, `exit_code`, and `seconds`. `to_dict()` is JSON-serializable.

`DocumentResult`: stable batch-local `id`, original absolute `source`, `relative_input`, `source_sha256`, output-relative `output_markdown` and `artifact_dir`, `status`, `quality_status`, `seconds`, `page_count`, `qa`, output hash mapping `outputs`, conversion `signature`, and optional `error`.

Operational document failures are results with `status="failed"`; `convert_file` returns that result too. Invalid API arguments raise `ValueError`; protected existing output raises `FileExistsError`; filesystem setup errors remain Python exceptions. KeyboardInterrupt propagates after completed batch state is saved. Public API functions do not call SystemExit.

Returned `results` cover the current input discovery. Previously recorded entries remain in the manifest even when a source file is later removed. Every document carries its own conversion signature.

Calls are sequential within a batch. Use independent output directories if your application orchestrates parallel processes. Internal modules and legacy workpack dictionaries are not the stable public API.
