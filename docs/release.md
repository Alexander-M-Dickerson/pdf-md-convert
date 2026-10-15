# Prepare a local release

These steps build and test locally. They do not create a remote, push Git, upload to PyPI, publish a release, or upload private regression data.

```sh
uv sync --group dev --group release
uv run python -m compileall -q src tests
uv run python -m pytest -q
uv run python -m build
uv run python tools/check_dist.py
uv run python tools/check_wheel.py --work-dir .local/wheel-check
uv run python tools/check_wheel.py --work-dir .local/sdist-check --artifact dist/pdf_md_convert-0.2.0rc1.tar.gz
uv run python tools/create_source_archive.py
```

`check_dist.py` checks metadata and archive contents; `check_wheel.py` installs the wheel into a fresh isolated environment, then exercises it from outside the checkout. The wheel check installs dependencies from the configured package index or cache; it does not run OCR models. Use a new check directory each time. Source-distribution installation is also checked before preparing the handover.

Version is single-sourced in `src/pdf_md_convert/__init__.py`. The AGPL license and dependency notices must be included in both distributions. Keep the lockfile updated when dependency constraints change. A development lock resolves optional extras but does not install them unless requested.

Package a source-project ZIP from an explicit allowlist. Exclude .git history, .venv, caches, private inputs, conversions, .local data, and credentials. Write SHA-256 checksums for the wheel, sdist, and ZIP. Preserve the local release-readiness report alongside the artifacts, distinguishing executed checks from configured CI.

For engine or dependency changes run the private regression workflow in [validation](validation.md) when the corpus is available. Private artifacts remain local and ignored. Include only aggregate, appropriately qualified metrics in public documentation.

Prepared CI tests Windows/Linux Python 3.10, 3.12, 3.14 and macOS Python 3.12. It contains no publication job. Configuration is not proof that those remote jobs have run.
