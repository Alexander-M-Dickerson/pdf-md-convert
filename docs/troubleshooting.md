# Troubleshooting

Run `pdf-md-convert doctor` first; it reports the actual executable and installed versions without loading models. Missing optional extras are not a failure of the base converter.

| Symptom | Action |
|---|---|
| Command not found | Use the virtual environment's Python with `-m pdf_md_convert`, or `uv run --no-default-groups pdf-md-convert` |
| Windows py launcher says no Python | Use uv-managed Python or the explicit `.venv/Scripts/python.exe`; do not assume launcher registration |
| Permission/cache/temp error | Choose a writable output; set UV_CACHE_DIR, TEMP and TMP to local writable directories; retry the same command before changing code |
| "Output path too long for Windows", error 206, or a native crop file-open error | Use a short output root, such as `C:\pdf-out`; crop/sidecar paths add about 160 characters of internal directories. Batch checks this before converting; crops that still fail are listed in `missing_crop_images` |
| Existing output conflict | Use a new output directory, or resume unchanged results; overwrite only when replacement is intended |
| Source/reference hash changed | Reinspect the source and rebuild the affected catalog/review; do not bypass hash checks |
| Scan produces little text | Expected without OCR; review source images or explicitly choose an optional OCR workflow |
| Missing equation/table | Inspect source crops and reference context; distinguish absent appendices from failed extraction |
| Marker executable missing | Install the optional OCR extra in the same environment; core batch conversion does not need it |
| Corrupt/encrypted PDF | Inspect the per-document error; provide a readable authorized copy rather than altering the original |
| Old workpack command rejects a path | Use current package commands and an explicit --workspace; legacy checkout-derived paths are unsupported |

Do not use GPU or generic multiprocessing probes to diagnose the default deterministic path; it does not require either. Preserve actual traceback and command details. Do not replace process isolation with threads because an unrelated IPC primitive is blocked.

Detailed local output can contain source filenames and paths. Keep generated outputs outside public version control. No telemetry or API credentials are configured by this package.
