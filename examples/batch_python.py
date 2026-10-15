"""Run from anywhere after installing the package."""
import argparse
from pdf_md_convert.api import ConversionOptions, convert_batch

parser=argparse.ArgumentParser()
parser.add_argument('input');parser.add_argument('--output-dir',required=True)
args=parser.parse_args()
result=convert_batch(args.input,output_dir=args.output_dir,options=ConversionOptions(recursive=True))
for document in result.results:
    print(document.relative_input, document.status, document.quality_status, document.error or '')
raise SystemExit(result.exit_code)
