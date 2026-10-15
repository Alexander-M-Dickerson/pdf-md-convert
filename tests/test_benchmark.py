import json
import fitz
import pytest
from pdf_md_convert.benchmark import build_registry, build_manifest, timing_summary
from pdf_md_convert.reference_validation import digest, parse_markdown, compare_parsed
from pdf_md_convert.verified_accuracy import evaluate_suite, score_regions


def test_registry_checks_hashes_and_manifest_sampling_is_reproducible(tmp_path):
    for i,text in enumerate(['alpha bravo charlie delta echo','foxtrot golf hotel india juliet','kilo lima mike november oscar','papa quebec romeo sierra tango']):
        folder=tmp_path/'articles'/str(i);folder.mkdir(parents=True)
        with fitz.open() as doc:
            p=doc.new_page();p.insert_text((50,100),text);doc.save(folder/'manuscript.pdf')
        (folder/'manuscript.md').write_text(text)
    registry=build_registry(tmp_path)
    a=build_manifest(tmp_path,registry,[],count=2,paired_count=1)
    b=build_manifest(tmp_path,registry,[],count=2,paired_count=1)
    assert a==b
    assert len({r['sha256'] for r in a['records']})==3
    assert all(r['references'] for r in a['records'])


def test_timing_records_failures_even_if_fast():
    summary=timing_summary([[{'seconds':.1,'error':'bad PDF'}]])
    assert summary['errors']


def test_unreviewed_drafts_cannot_pass_verified_accuracy(tmp_path):
    suite=tmp_path/'suite.json'
    suite.write_text(json.dumps({'regions':[{'id':'one','source_sha256':'abc','verified':False}]}))
    r=evaluate_suite(suite,[{'id':'one','source':'a.pdf','sha256':'abc'}],tmp_path,False)
    assert r['status']=='incomplete'
    assert r['coverage_issues']


def test_verified_equation_loss_is_an_error():
    r=score_regions([{'id':'eq','kind':'equation','page':1,'key':'1','expected':r'$$x^{2}=y\tag{1}$$'}],r'$$x2=y\tag{1}$$')
    assert r['metrics']['equation']['errors']>0


def test_table_relocation_does_not_affect_identity_and_blanks_are_scored():
    reference='## Table 8a\n\n|a|b|\n|---|---|\n|x||'
    candidate='## Table 8b\n\n|a|b|\n|---|---|\n|x|0|'
    result=compare_parsed(parse_markdown(candidate),parse_markdown(reference))
    assert result['metrics']['table']['matched_items']==0


def test_single_digit_script_braces_are_presentation_only():
    from pdf_md_convert.reference_validation import math_tokens
    assert math_tokens(r'\beta{}x^2')==math_tokens(r'\beta x^{2}')
