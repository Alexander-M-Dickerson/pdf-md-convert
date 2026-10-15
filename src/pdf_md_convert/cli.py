from __future__ import annotations
import sys
from . import __version__


def main(argv=None):
    argv = list(argv) if argv is not None else sys.argv[1:]
    if argv == ["--version"]:
        print(f"pdf-md-convert {__version__}"); return 0
    if not argv or argv[0] in {"-h", "--help"}:
        print("pdf-md-convert: local PDF conversion and agent-assisted review\n")
        print("Recommended: batch INPUT [--recursive] [--profile balanced] [--output-dir PATH]")
        print("Commands: batch, doctor, init-agent, review-workpack, hybrid, convert, marker,")
        print("          validate, cleanup-equations, compare-reference, reference-index,")
        print("          prepare-review, experiment")
        print("Use COMMAND --help for all options. Conversion completion is not verified accuracy.")
        return 0
    try:
        if argv[0] in {"batch", "doctor", "init-agent", "review-workpack"}:
            from .commands import main as run
        else:
            from .legacy_cli import main as run
        return run(argv)
    except KeyboardInterrupt:
        print("Interrupted; completed batch state is preserved.", file=sys.stderr); return 130
    except (ValueError, OSError) as exc:
        print(f"Error: {exc}", file=sys.stderr); return 2


if __name__ == "__main__":
    raise SystemExit(main())
