import json
from pathlib import Path

from pdf_md_convert.reference_validation import (
    parse_markdown, compare_parsed, compare_reference, math_tokens, digest, split_cells,
)


def test_enrichment_equation_index_does_not_count_as_source():
    text = '# Title\n\n## AI Reading Guide\n\n### Equation Quick-Reference\n$$x=y\\tag{1}$$\n\n## Abstract\nReal text.\n\n$$x=y\\tag{1}$$'
    parsed = parse_markdown(text)
    assert [b.kind for b in parsed.blocks].count('equation') == 1
    assert any(b.text == 'Real text.' for b in parsed.blocks)
    assert parsed.excluded


def test_editorial_note_excludes_only_explicit_note():
    parsed = parse_markdown('Real source.\n\n> **Reading note:** interpret this.\n\nMore source.')
    assert [b.text for b in parsed.blocks] == ['Real source.', 'More source.']


def test_missing_table_stays_in_denominator():
    ref = parse_markdown('## Table 1\n\n|row|x|\n|---|---|\n|a|-1.2|')
    report = compare_parsed(parse_markdown(''),ref)
    assert report['metrics']['table']['error_rate'] == 1
    assert report['metrics']['table']['missing_items'] == 1


def test_moved_table_matches_but_changed_sign_is_error():
    ref = '## Table 1\n\n|row|x|\n|---|---|\n|a|-1.2|'
    report = compare_parsed(parse_markdown('Text.\n\n'+ref.replace('-1.2','1.2')),parse_markdown(ref))
    assert report['metrics']['table']['matched_items'] == 1
    assert report['metrics']['table']['errors'] == 1


def test_blank_cells_escaped_pipes_and_scripts_are_preserved():
    assert split_cells('| |a\\|b| |') == ['', 'a|b', '']
    assert math_tokens('x^{2}') != math_tokens('x2')
    assert math_tokens(r'\beta x') != math_tokens(r'\betax')


def test_duplicate_equation_id_is_ambiguous_not_a_perfect_match():
    ref = parse_markdown('$$x=y\\tag{1}$$')
    report = compare_parsed(parse_markdown('$$x=y\\tag{1}$$\n\n$$x=z\\tag{1}$$'),ref)
    assert report['metrics']['equation']['ambiguous_items'] == 1
    assert report['metrics']['equation']['exact_items'] == 0


def test_source_hash_mismatch_blocks_scoring(tmp_path):
    pdf=tmp_path/'manuscript.pdf'; pdf.write_bytes(b'original')
    ref=tmp_path/'manuscript.md'; ref.write_text('source')
    candidate=tmp_path/'candidate.md'; candidate.write_text('source')
    work=tmp_path/'conversion_work'; work.mkdir()
    (work/'manifest.json').write_text(json.dumps({'source_sha256':'incorrect'}))
    result=compare_reference(candidate,ref,pdf,tmp_path/'reports')
    assert result['status']=='blocked'
    assert 'metrics' not in result
    assert pdf.read_bytes()==b'original'


def test_inline_comment_does_not_discard_prose():
    p=parse_markdown('Before <!-- metadata --> after.')
    assert p.blocks[0].text=='Before  after.'


def test_inconsistent_pages_do_not_prevent_text_alignment():
    a=parse_markdown('*(Page 1)*\n\nThe result is precisely 42 percent.')
    b=parse_markdown('*(Page 302)*\n\nThe result is precisely 42 percent.')
    assert compare_parsed(a,b)['metrics']['text']['errors']==0
