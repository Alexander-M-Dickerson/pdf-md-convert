#!/bin/sh
set -eu
if [ "$#" -ne 2 ]; then
  echo "Usage: sh examples/batch_cli.sh INPUT OUTPUT" >&2
  exit 2
fi
python -m pdf_md_convert batch "$1" --recursive --output-dir "$2"
