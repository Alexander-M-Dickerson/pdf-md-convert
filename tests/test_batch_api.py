import json
from pathlib import Path
import socket

import fitz
import pytest

from pdf_md_convert.api import ConversionOptions, convert_batch, convert_file, digest


def pdf(path, text='Synthetic research result. Revenue is 42. This passage is original demonstration text.'):
    path.parent.mkdir(parents=True, exist_ok=True)
    with fitz.open() as doc:
        page=doc.new_page(); page.insert_text((60,90), text); doc.save(path)
    return path


def test_nested_batch_preserves_unicode_names_and_is_offline(tmp_path, monkeypatch):
    source=tmp_path/'papers'
    a=pdf(source/'A paper.pdf'); b=pdf(source/'nested'/'A paper.PDF'); c=pdf(source/'nested'/'caf\u00e9.pdf')
    before={p:digest(p) for p in [a,b,c]}
    def no_network(*args, **kwargs): raise AssertionError('Unexpected network access')
    monkeypatch.setattr(socket.socket, 'connect', no_network)
    r=convert_batch(source, options=ConversionOptions(recursive=True))
    out=Path(r.output_dir)
    assert r.exit_code==0 and len(r.results)==3
    assert len({x.artifact_dir for x in r.results})==3
    assert (out/'nested/A paper.md').exists() and (out/'nested/caf\u00e9.md').exists()
    assert all(digest(p)==h for p,h in before.items())
    assert len(convert_batch(source, options=ConversionOptions(recursive=True,resume=True)).results)==3


def test_resume_requires_unchanged_source_settings_and_outputs(tmp_path):
    source=pdf(tmp_path/'a.pdf')
    r=convert_batch(source); out=Path(r.output_dir)
    assert convert_batch(source, options=ConversionOptions(resume=True)).results[0].status=='skipped'
    with pytest.raises(FileExistsError): convert_batch(source)
    with pytest.raises(FileExistsError): convert_batch(source, options=ConversionOptions(resume=True,profile='fast'))
    (out/'a.md').write_text('Reviewed by a human',encoding='utf-8')
    with pytest.raises(FileExistsError): convert_batch(source, options=ConversionOptions(resume=True))
    assert (out/'a.md').read_text(encoding='utf-8')=='Reviewed by a human'
    assert convert_batch(source, options=ConversionOptions(overwrite=True)).exit_code==0


def test_failed_document_does_not_abort_batch(tmp_path):
    pdf(tmp_path/'good.pdf'); (tmp_path/'bad.pdf').write_bytes(b'broken PDF')
    result=convert_batch(tmp_path)
    assert result.exit_code==1
    assert {r.status for r in result.results}=={'completed','failed'}
    assert json.loads(Path(result.manifest).read_text(encoding='utf-8'))['documents']


def test_strict_qa_is_separate_from_execution(tmp_path):
    source=pdf(tmp_path/'a.pdf','See Table 9 for empirical results.')
    result=convert_batch(source, options=ConversionOptions(profile='fast',strict_qa=True))
    assert result.results[0].status=='completed'
    assert result.exit_code==3
    report=(Path(result.output_dir)/'_batch_qa_summary.md').read_text(encoding='utf-8')
    assert 'Missing referenced tables: 9' in report


def test_batch_cli_separates_backend_notices_from_json(tmp_path, monkeypatch, capsys):
    from pdf_md_convert import api, cli
    source=pdf(tmp_path/'paper.pdf')
    real=api.convert_hybrid
    def noisy(*args, **kwargs):
        print('Backend diagnostic notice')
        return real(*args, **kwargs)
    monkeypatch.setattr(api,'convert_hybrid',noisy)
    assert cli.main(['batch',str(source)])==0
    captured=capsys.readouterr()
    assert json.loads(captured.out)['exit_code']==0
    assert 'Backend diagnostic notice' in captured.err


def test_interruption_preserves_completed_manifest(tmp_path, monkeypatch):
    from pdf_md_convert import api
    pdf(tmp_path/'a.pdf'); pdf(tmp_path/'b.pdf')
    real=api.convert_hybrid
    def stop_second(source,**kwargs):
        if Path(source).name=='b.pdf': raise KeyboardInterrupt
        return real(source,**kwargs)
    monkeypatch.setattr(api,'convert_hybrid',stop_second)
    with pytest.raises(KeyboardInterrupt): convert_batch(tmp_path)
    data=json.loads((tmp_path/f'{tmp_path.name}_md/_batch_manifest.json').read_text(encoding='utf-8'))
    assert [r['status'] for r in data['documents']]==['completed','interrupted']


def test_casefold_collision_preflight_and_reserved_names(tmp_path):
    pdf(tmp_path/'_batch_qa_summary.pdf')
    with pytest.raises(ValueError):convert_batch(tmp_path)
    assert not (tmp_path/f'{tmp_path.name}_md').exists()


def test_api_rejects_bad_options_and_does_not_exit(tmp_path):
    source=pdf(tmp_path/'a.pdf')
    with pytest.raises(ValueError):convert_file(source,options=ConversionOptions(time_budget=-1))
    with pytest.raises(ValueError):convert_file(tmp_path)


def test_moved_batch_retains_audit_links_and_reviewed_copies(tmp_path):
    import shutil,re
    from pdf_md_convert.agent_tools import review_workpack
    source=pdf(tmp_path/'input/a paper.pdf')
    r=convert_batch(source,options=ConversionOptions(repair_placement='inline'))
    old=Path(r.output_dir); moved=tmp_path/'moved';shutil.move(old,moved)
    md=moved/'a paper.md'
    links=re.findall(r'\]\(<([^>]+)>\)',md.read_text(encoding='utf-8'))
    assert links and all((md.parent/link).is_file() for link in links)
    result=review_workpack(moved,max_pages=1)
    assert result['status']=='unverified'
    assert (moved/'_review/reviewed/a paper.md').exists()
    with pytest.raises(FileExistsError):review_workpack(moved)


def test_changed_source_cannot_resume_or_be_reviewed(tmp_path):
    from pdf_md_convert.agent_tools import review_workpack
    source=pdf(tmp_path/'a.pdf');r=convert_batch(source)
    source.write_bytes(source.read_bytes()+b'\n% changed')
    with pytest.raises(FileExistsError):convert_batch(source,options=ConversionOptions(resume=True))
    with pytest.raises(ValueError):review_workpack(Path(r.output_dir))


def test_encrypted_pdf_is_document_failure(tmp_path):
    source=tmp_path/'encrypted.pdf'
    with fitz.open() as doc:
        doc.new_page().insert_text((60,90),'Protected synthetic document')
        doc.save(source,encryption=fitz.PDF_ENCRYPT_AES_256,owner_pw='owner',user_pw='reader')
    result=convert_batch(source)
    assert result.exit_code==1 and result.results[0].status=='failed'
    assert source.exists()


def test_readonly_input_and_ordinary_underscore_names(tmp_path):
    import stat
    source=pdf(tmp_path/'inputs/_ordinary.pdf'); original=digest(source)
    source.chmod(stat.S_IREAD)
    try:
        result=convert_batch(source,output_dir=tmp_path/'outputs')
        assert result.exit_code==0 and digest(source)==original
        assert list(source.parent.iterdir())==[source]
    finally:source.chmod(stat.S_IREAD|stat.S_IWRITE)


def test_directory_symlinks_are_not_followed(tmp_path):
    source=tmp_path/'inputs';pdf(source/'a.pdf');other=tmp_path/'elsewhere';pdf(other/'b.pdf')
    try:(source/'linked').symlink_to(other,target_is_directory=True)
    except OSError:pytest.skip('Creating directory symlinks is not available')
    result=convert_batch(source,options=ConversionOptions(recursive=True))
    assert len(result.results)==1
