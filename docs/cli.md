# CLI reference

`pdf-md-convert` and `python -m pdf_md_convert` are equivalent entry points. Use batch for normal conversion. Advanced hybrid/convert/marker retain legacy overwrite and output conventions; always supply a fresh explicit output directory.

`convert` prepares a legacy semantic workpack; it is not the recommended fast batch command. `validate` checks that workpack format, not a batch manifest. Reference agreement is not verified source accuracy. Optional model operations only occur when explicitly invoking Marker options.

## batch

```text
usage: pdf-md-convert batch [-h] [--output-dir OUTPUT_DIR]
                            [--profile {fast,balanced}] [--recursive]
                            [--resume | --overwrite] [--strict-qa]
                            [--repair-placement {appendix,inline}]
                            [--max-equation-crops MAX_EQUATION_CROPS]
                            [--max-table-crops MAX_TABLE_CROPS]
                            [--time-budget TIME_BUDGET]
                            [--max-total-repair-seconds MAX_TOTAL_REPAIR_SECONDS]
                            input

positional arguments:
  input

options:
  -h, --help            show this help message and exit
  --output-dir OUTPUT_DIR
  --profile {fast,balanced}
  --recursive
  --resume
  --overwrite
  --strict-qa
  --repair-placement {appendix,inline}
  --max-equation-crops MAX_EQUATION_CROPS
  --max-table-crops MAX_TABLE_CROPS
  --time-budget TIME_BUDGET
  --max-total-repair-seconds MAX_TOTAL_REPAIR_SECONDS
```

## doctor

```text
usage: pdf-md-convert doctor [-h]

options:
  -h, --help  show this help message and exit
```

## init-agent

```text
usage: pdf-md-convert init-agent [-h] --output-dir OUTPUT_DIR [--overwrite]

options:
  -h, --help            show this help message and exit
  --output-dir OUTPUT_DIR
  --overwrite
```

## review-workpack

```text
usage: pdf-md-convert review-workpack [-h] [--max-pages MAX_PAGES] output_dir

positional arguments:
  output_dir

options:
  -h, --help            show this help message and exit
  --max-pages MAX_PAGES
                        Maximum additional page renders per document
```

## hybrid

```text
usage: pdf-md-convert hybrid [-h] [--output-dir OUTPUT_DIR]
                             [--repair-placement {inline,appendix}]
                             [--max-repair-pages MAX_REPAIR_PAGES]
                             [--torch-device TORCH_DEVICE]
                             [--repair-equations]
                             [--max-equation-crops MAX_EQUATION_CROPS]
                             [--repair-tables]
                             [--max-table-crops MAX_TABLE_CROPS]
                             [--time-budget TIME_BUDGET]
                             [--fast-backend {pymupdf4llm,pymupdf}]
                             [--local-repair-tier {off,deterministic,marker-page}]
                             [--max-total-repair-seconds MAX_TOTAL_REPAIR_SECONDS]
                             input

positional arguments:
  input

options:
  -h, --help            show this help message and exit
  --output-dir OUTPUT_DIR
  --repair-placement {inline,appendix}
  --max-repair-pages MAX_REPAIR_PAGES
  --torch-device TORCH_DEVICE
  --repair-equations
  --max-equation-crops MAX_EQUATION_CROPS
  --repair-tables
  --max-table-crops MAX_TABLE_CROPS
  --time-budget TIME_BUDGET
  --fast-backend {pymupdf4llm,pymupdf}
  --local-repair-tier {off,deterministic,marker-page}
  --max-total-repair-seconds MAX_TOTAL_REPAIR_SECONDS
```

## convert

```text
usage: pdf-md-convert convert [-h] [--mode {fast,balanced,audit}]
                              [--workspace WORKSPACE] [--force]
                              [--output-dir OUTPUT_DIR]
                              input

positional arguments:
  input

options:
  -h, --help            show this help message and exit
  --mode {fast,balanced,audit}
  --workspace WORKSPACE
  --force
  --output-dir OUTPUT_DIR
```

## marker

```text
usage: pdf-md-convert marker [-h] [--output-dir OUTPUT_DIR]
                             [--page-range PAGE_RANGE] [--use-llm]
                             [--force-ocr] [--marker-bin MARKER_BIN]
                             [--torch-device TORCH_DEVICE]
                             [--keep-marker-output]
                             input

positional arguments:
  input

options:
  -h, --help            show this help message and exit
  --output-dir OUTPUT_DIR
  --page-range PAGE_RANGE
  --use-llm
  --force-ocr
  --marker-bin MARKER_BIN
  --torch-device TORCH_DEVICE
  --keep-marker-output
```

## validate

```text
usage: pdf-md-convert validate [-h] [--json] folder

positional arguments:
  folder

options:
  -h, --help  show this help message and exit
  --json
```

## cleanup-equations

```text
usage: pdf-md-convert cleanup-equations [-h]
                                        [--flat-output-dir FLAT_OUTPUT_DIR]
                                        [--workspace WORKSPACE]
                                        input

positional arguments:
  input

options:
  -h, --help            show this help message and exit
  --flat-output-dir FLAT_OUTPUT_DIR
  --workspace WORKSPACE
```

## compare-reference

```text
usage: __main__.py [-h] --candidate CANDIDATE --reference REFERENCE --pdf PDF
                   --output-dir OUTPUT_DIR [--annotations ANNOTATIONS]

Offline fidelity comparison. Agreement with a derivative is not source
accuracy. The reference and source are read-only. Ambiguous alignment is
reported, never silently interpreted as a correct conversion. No OCR or model
imports live here.

options:
  -h, --help            show this help message and exit
  --candidate CANDIDATE
  --reference REFERENCE
  --pdf PDF
  --output-dir OUTPUT_DIR
  --annotations ANNOTATIONS
```

## reference-index

```text
usage: __main__.py [-h] --root ROOT --output-dir OUTPUT_DIR
                   [--history-root HISTORY_ROOT] [--count COUNT]
                   [--paired-count PAIRED_COUNT]

Index read-only reference manuscripts and freeze random benchmark inputs

options:
  -h, --help            show this help message and exit
  --root ROOT
  --output-dir OUTPUT_DIR
  --history-root HISTORY_ROOT
  --count COUNT
  --paired-count PAIRED_COUNT
```

## prepare-review

```text
usage: __main__.py [-h] --manifest MANIFEST --output-dir OUTPUT_DIR
                   [--split {development,holdout,all}]

Prepare source images and unverified reference drafts for visual calibration.

options:
  -h, --help            show this help message and exit
  --manifest MANIFEST
  --output-dir OUTPUT_DIR
  --split {development,holdout,all}
```

## experiment

```text
usage: pdf-md-convert experiment [-h] [--output-dir OUTPUT_DIR]
                                 [--count COUNT] [--seed SEED]
                                 [--comparison-dir COMPARISON_DIR]
                                 [--local-repair-tier {off,deterministic,marker-page}]
                                 [--max-total-repair-seconds MAX_TOTAL_REPAIR_SECONDS]
                                 [roots ...]

positional arguments:
  roots

options:
  -h, --help            show this help message and exit
  --output-dir OUTPUT_DIR
  --count COUNT
  --seed SEED
  --comparison-dir COMPARISON_DIR
  --local-repair-tier {off,deterministic,marker-page}
  --max-total-repair-seconds MAX_TOTAL_REPAIR_SECONDS
```

## Frozen-manifest experiment

```text
usage: __main__.py [-h] --manifest MANIFEST
                   [--split {development,holdout,all}] --output-dir OUTPUT_DIR
                   [--baseline-code BASELINE_CODE] [--repeats REPEATS]
                   [--repair-placement {inline,appendix}]
                   [--compare-references]
                   [--verified-references VERIFIED_REFERENCES]

Frozen local corpora, reference registry, repeated conversion and accuracy
gates.

options:
  -h, --help            show this help message and exit
  --manifest MANIFEST
  --split {development,holdout,all}
  --output-dir OUTPUT_DIR
  --baseline-code BASELINE_CODE
  --repeats REPEATS
  --repair-placement {inline,appendix}
  --compare-references
  --verified-references VERIFIED_REFERENCES
```
