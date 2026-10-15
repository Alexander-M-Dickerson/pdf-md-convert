# Contributing

Use Python 3.12 for the documented local environment. Runtime metadata requires Python 3.10 or newer; CI is prepared for 3.10, 3.12 and 3.14. Only actually executed checks are release evidence.

```sh
uv sync --group dev --group release
uv run python -m pytest -q
uv run python -m compileall -q src tests
uv run python -m build
uv run python -m twine check dist/*
```

Use `UV_CACHE_DIR`, `TEMP`, and `TMP` pointing to writable local directories when a restricted environment blocks a package manager or test runner. Do not patch application behavior to work around a generic environment probe.

Use synthetic fixtures with explicit expected content. Preserve the existing fidelity tests. Test corrupt files, interrupted runs, edited outputs, changed inputs, nested names, optional dependencies, and moved output folders.

For extraction changes, follow [validation](docs/validation.md). A crop is evidence, not a correct reconstruction. Do not tune on a holdout and continue calling it unseen.

Agent changes start in `src/pdf_md_convert/resources/agent_bundle`; run `python tools/sync_agent_bundle.py` to update the repository skill mirror. Keep frontmatter, UI metadata, and referenced commands consistent.

Standard commands do not publish anything. See [release procedure](docs/release.md). Keep generated artifacts, private inputs, and secrets outside version control. Contributions are distributed under AGPL-3.0-only.
