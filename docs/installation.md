# Installation

The recommended runtime is Python 3.12. Python 3.10 is the minimum. Windows is the local verification platform; Linux/macOS CI configuration is included, but prepared CI is not evidence of an executed run.

## Source project with uv

From the extracted project directory:

```sh
uv sync --no-default-groups --python 3.12
uv run --no-default-groups pdf-md-convert doctor
```

`uv` can provision Python when needed. Installing dependencies requires network access unless cached; subsequent base conversion is local. The lockfile fixes contributor dependency versions, including optional extras, but optional extras are not installed by default.

## Standard Python and pip

Windows PowerShell, with Python already on PATH:

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install .
.\.venv\Scripts\python.exe -m pdf_md_convert doctor
```

Linux/macOS:

```sh
python3 -m venv .venv
.venv/bin/python -m pip install .
.venv/bin/python -m pdf_md_convert doctor
```

Activation is optional when using the explicit interpreter path. Do not assume the Windows `py` launcher knows about uv-managed interpreters.

## Local wheel

In an active environment, from the project folder containing the supplied distribution:

```sh
python -m pip install ./dist/pdf_md_convert-0.2.0rc1-py3-none-any.whl
python -m pdf_md_convert doctor
```

This package has not been published to PyPI by the preparation process. A bare install by project name is not the supported installation recipe for this local release.

## Optional backends

From a source checkout with uv:

```sh
uv sync --no-default-groups --extra layout
uv sync --no-default-groups --extra ocr
```

Or, with pip, install `.[layout]` or `.[ocr]` from the project directory. These are alternatives, not required setup steps. Layout installs PyMuPDF4LLM. OCR installs Marker and its dependency stack; first actual model-backed execution may download model weights, take substantial time, and use significant memory. Check upstream model terms. Do not install OCR merely to make base diagnostics pass.

The tested PyMuPDF line is constrained to 1.27.x. Upgrade it only with fidelity and speed checks. No API keys or `.env` file are required.
