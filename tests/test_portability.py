import json
from pathlib import Path
import subprocess
import sys
import fitz
import pytest
from pdf_md_convert.agent_tools import export_agent


def test_agent_bundle_is_packaged_self_contained_and_protected(tmp_path):
    paths=export_agent(tmp_path/'workspace')
    assert any(p.endswith('SKILL.md') for p in paths)
    skill=tmp_path/'workspace/.agents/skills/convert-paper'
    assert (skill/'references/fidelity.md').is_file()
    assert 'wrds2' not in (skill/'SKILL.md').read_text(encoding='utf-8')
    with pytest.raises(FileExistsError):export_agent(tmp_path/'workspace')


def test_workpack_uses_explicit_external_workspace(tmp_path):
    source=tmp_path/'source.pdf'
    with fitz.open() as doc:
        page=doc.new_page();page.insert_text((60,90),'Original synthetic academic paper.');doc.save(source)
    p=subprocess.run([sys.executable,'-m','pdf_md_convert','convert',str(source),
                      '--workspace',str(tmp_path/'work'),'--output-dir',str(tmp_path/'flat')],
                      cwd=tmp_path,capture_output=True,text=True,encoding='utf-8')
    assert p.returncode in {0,1},p.stderr
    result=json.loads(p.stdout)
    folder=Path(result['folders'][0]['folder'])
    assert folder.is_relative_to(tmp_path/'work')
    assert (folder/'conversion_work/manifest.json').is_file()
    assert (folder/'manuscript.md').is_file()


def test_skill_mirror_matches_packaged_resources():
    root=Path(__file__).resolve().parents[1]
    canonical=root/'src/pdf_md_convert/resources/agent_bundle/.agents'
    if not canonical.exists(): pytest.skip('Source-tree consistency check')
    for p in canonical.rglob('*'):
        if p.is_file():assert p.read_bytes()==(root/'.agents'/p.relative_to(canonical)).read_bytes()
