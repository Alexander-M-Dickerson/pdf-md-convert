"""Validate local distribution metadata and exclude private/build content."""
from pathlib import Path
import subprocess
import sys
import tarfile
import zipfile

root=Path(__file__).resolve().parents[1]
artifacts=sorted((root/'dist').glob('*.whl'))+sorted((root/'dist').glob('*.tar.gz'))
if len(artifacts)!=2: raise SystemExit('Expected exactly one wheel and one source distribution')
subprocess.run([sys.executable,'-m','twine','check',*[str(p) for p in artifacts]],check=True)
for path in artifacts:
    if path.suffix=='.whl':
        with zipfile.ZipFile(path) as archive:names=archive.namelist()
        assert any('agent_bundle/.agents/skills/convert-paper/SKILL.md' in n for n in names), names
    else:
        with tarfile.open(path) as archive:names=archive.getnames()
        assert any(n.endswith('/AGENTS.md') for n in names)
        assert any(n.endswith('/docs/batch-guide.md') for n in names)
    forbidden={'.git','.venv','.cache','.local','outputs','inputs','__pycache__'}
    assert not [n for n in names if forbidden.intersection(Path(n).parts)], names
    assert not [n for n in names if n.lower().endswith(('.pdf','.png','.pyc','.env'))], names
    assert any(n.endswith('/LICENSE') for n in names), names
    print(path.name, len(names),'allowed members')
