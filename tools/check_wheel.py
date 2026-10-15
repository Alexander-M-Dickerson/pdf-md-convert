"""Install and exercise a built distribution outside the source checkout."""
import argparse
import json
import os
from pathlib import Path
import subprocess
import sys
import venv

parser=argparse.ArgumentParser(description=__doc__)
parser.add_argument('--work-dir',type=Path,required=True)
parser.add_argument('--artifact',type=Path,help='Defaults to the built wheel; also accepts the sdist')
args=parser.parse_args()
root=Path(__file__).resolve().parents[1]; work=args.work_dir.resolve()
if work.exists(): raise SystemExit('Use a fresh check directory')
work.mkdir(parents=True); (work/'tmp').mkdir(); (work/'outside').mkdir()
env=os.environ.copy(); env.pop('PYTHONPATH',None)
env.update(PYTHONNOUSERSITE='1',PIP_CACHE_DIR=str(work/'cache'),TEMP=str(work/'tmp'),TMP=str(work/'tmp'),PYTHONUTF8='1')
venv.EnvBuilder(with_pip=True).create(work/'env')
python=work/'env'/('Scripts/python.exe' if os.name=='nt' else 'bin/python')
artifact=args.artifact.resolve() if args.artifact else next((root/'dist').glob('*.whl'))
def run(command,allowed=(0,)):
    p=subprocess.run([str(python),*command],cwd=work/'outside',env=env,text=True,capture_output=True,encoding='utf-8')
    if p.returncode not in allowed: raise RuntimeError(f'{command}: {p.returncode}\n{p.stdout}\n{p.stderr}')
    return p.stdout
run(['-m','pip','install',str(artifact)])
installed=run(['-c','import pdf_md_convert; print(pdf_md_convert.__file__)']).strip()
assert str(work/'env').lower() in installed.lower(), installed
run(['-c',"import importlib.util; assert importlib.util.find_spec('pymupdf4llm') is None; assert importlib.util.find_spec('marker') is None"])
doctor=json.loads(run(['-m','pdf_md_convert','doctor']))
run(['-m','pdf_md_convert','--help'])
console=python.parent/('pdf-md-convert.exe' if os.name=='nt' else 'pdf-md-convert')
subprocess.run([str(console),'--version'],cwd=work/'outside',env=env,check=True)
inputs=work/'papers'; output=work/'converted'
run([str(root/'examples/make_demo.py'),'--output-dir',str(inputs)])
batch=json.loads(run(['-m','pdf_md_convert','batch',str(inputs),'--recursive','--output-dir',str(output)]))
assert len(batch['results'])==3 and batch['exit_code']==0
resume=json.loads(run(['-m','pdf_md_convert','batch',str(inputs),'--recursive','--output-dir',str(output),'--resume']))
assert all(r['status']=='skipped' for r in resume['results'])
run(['-m','pdf_md_convert','review-workpack',str(output),'--max-pages','2'])
run(['-m','pdf_md_convert','init-agent','--output-dir',str(work/'agent')])
assert (work/'agent/.agents/skills/convert-paper/SKILL.md').is_file()
legacy=run(['-m','pdf_md_convert','convert',str(inputs/'Study.pdf'),'--workspace',str(work/'workspace'),
            '--output-dir',str(work/'legacy-flat')],allowed=(0,1))
legacy_data=json.loads(legacy)
folder=Path(legacy_data['folders'][0]['folder'])
assert folder.is_relative_to(work/'workspace') and (folder/'manuscript.md').exists()
run(['-m','pdf_md_convert','validate',str(folder),'--json'],allowed=(0,1))
corpus=work/'corpus'
run(['-c',"from pathlib import Path; import fitz; root=Path("+repr(str(corpus))+"); "
     "texts=['alpha bravo charlie delta echo','foxtrot golf hotel india juliet','kilo lima mike november oscar','papa quebec romeo sierra tango']; "
     "exec(\"for i,text in enumerate(texts):\\n folder=root/'articles'/str(i); folder.mkdir(parents=True); doc=fitz.open(); p=doc.new_page(); p.insert_text((60,90),text); doc.save(folder/'manuscript.pdf'); doc.close(); (folder/'manuscript.md').write_text(text,encoding='utf-8')\")"])
catalog=work/'catalog'
run(['-m','pdf_md_convert','reference-index','--root',str(corpus),'--output-dir',str(catalog),'--count','2','--paired-count','1'])
run(['-m','pdf_md_convert','prepare-review','--manifest',str(catalog/'benchmark_manifest.json'),'--split','all','--output-dir',str(work/'source-review')])
run(['-m','pdf_md_convert','experiment','--manifest',str(catalog/'benchmark_manifest.json'),'--split','all',
     '--output-dir',str(work/'benchmark'),'--repeats','1','--compare-references'])
report=json.loads((work/'benchmark/benchmark_summary.json').read_text(encoding='utf-8'))
assert report['conversion_error_gate'] and report['code_unchanged_gate']
(work/'result.json').write_text(json.dumps(dict(artifact=artifact.name,installed_from_wheel_or_sdist=True,
    doctor=doctor,checks=['console','module','batch','resume','review','agent-export','workpack','validate','reference-index','prepare-review','installed-benchmark']),indent=2),encoding='utf-8')
print('Installed distribution checks passed:', artifact.name)
