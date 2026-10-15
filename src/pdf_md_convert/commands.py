from __future__ import annotations
import argparse
from contextlib import redirect_stdout
from dataclasses import asdict
from importlib import metadata, util
import json
from pathlib import Path
import platform
import sys


def main(argv):
    command, *rest = argv
    parser = argparse.ArgumentParser(prog=f'pdf-md-convert {command}')
    if command == 'batch':
        from .api import ConversionOptions, convert_batch
        parser.add_argument('input')
        parser.add_argument('--output-dir')
        parser.add_argument('--profile', choices=['fast', 'balanced'], default='balanced')
        parser.add_argument('--recursive', action='store_true')
        group = parser.add_mutually_exclusive_group()
        group.add_argument('--resume', action='store_true')
        group.add_argument('--overwrite', action='store_true')
        parser.add_argument('--strict-qa', action='store_true')
        parser.add_argument('--repair-placement', choices=['appendix', 'inline'], default='appendix')
        parser.add_argument('--max-equation-crops', type=int)
        parser.add_argument('--max-table-crops', type=int)
        parser.add_argument('--time-budget', type=float, default=10.0)
        parser.add_argument('--max-total-repair-seconds', type=float, default=30.0)
        args = vars(parser.parse_args(rest)); source = args.pop('input'); output = args.pop('output_dir')
        # Backend notices must not corrupt the machine-readable result on stdout.
        with redirect_stdout(sys.stderr):
            result = convert_batch(source, output_dir=output, options=ConversionOptions(**args))
        print(json.dumps(result.to_dict(), indent=2, ensure_ascii=True)); return result.exit_code
    if command == 'doctor':
        parser.parse_args(rest)
        from . import __version__
        packages = {}
        for name in ['pymupdf', 'ftfy', 'pymupdf4llm', 'marker-pdf']:
            try: packages[name] = metadata.version(name)
            except metadata.PackageNotFoundError: packages[name] = None
        print(json.dumps(dict(version=__version__, python=sys.version, executable=sys.executable,
                              platform=platform.platform(), packages=packages,
                              default_backend='pymupdf', default_ocr=False, models_loaded=False), indent=2))
        return 0 if all(packages[n] for n in ['pymupdf', 'ftfy']) else 1
    if command == 'init-agent':
        from .agent_tools import export_agent
        parser.add_argument('--output-dir', type=Path, required=True)
        parser.add_argument('--overwrite', action='store_true')
        args = parser.parse_args(rest)
        print(json.dumps(dict(files=export_agent(args.output_dir, args.overwrite)), indent=2)); return 0
    if command == 'review-workpack':
        from .agent_tools import review_workpack
        parser.add_argument('output_dir', type=Path)
        parser.add_argument('--max-pages', type=int, default=20, help='Maximum additional page renders per document')
        args = parser.parse_args(rest)
        print(json.dumps(review_workpack(args.output_dir, args.max_pages), indent=2)); return 0
    raise ValueError(f'Unknown command: {command}')
