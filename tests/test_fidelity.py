import time
import fitz
from pdf_md_convert.equation_regions import candidate_latex, equation_body_lines, repair_equation_regions
from pdf_md_convert.hybrid_backend import normalize_fast_markdown
from pdf_md_convert.math_geometry import encode_math, reconstruct
from pdf_md_convert.layout import CachedDocument, ordered_blocks, assemble_inline
from pdf_md_convert.table_regions import markdown_table_to_tsv


def test_equation_tag_removal_preserves_constants_and_denominators():
    for n in [1,2,20]:
        latex,confidence,_=candidate_latex(f'x = {n}\n({n})',str(n))
        assert f'x = {n}' in latex
    assert equation_body_lines('x = y\n2\n(2)','2') == ['x = y','2']


def test_unicode_scripts_and_command_boundaries():
    assert encode_math('x²')=='x^{2}'
    assert encode_math('y₂')=='y_{2}'
    assert encode_math('βx')==r'\beta{}x'
    assert encode_math('x¹²')=='x^{12}'


def test_fraction_components_do_not_become_aligned_system():
    latex,confidence,_=candidate_latex('eta =\nPn\ni=1 x\nPn\ni=1 y\n(1)','1')
    assert latex is None
    assert confidence!='high'


def test_lexical_hyphen_is_not_destroyed():
    assert 'risk-adjusted' in normalize_fast_markdown('risk-\nadjusted')


def test_geometry_reconstructs_superscript(tmp_path):
    doc=fitz.open(); p=doc.new_page()
    p.insert_text((80,100),'x',fontsize=12)
    p.insert_text((87,94),'2',fontsize=8)
    p.insert_text((94,100),'= y',fontsize=12)
    p.insert_text((220,100),'(1)',fontsize=12)
    value,reasons=reconstruct(p,fitz.Rect(75,75,250,110),'1')
    assert value is not None and '^{2}' in value
    doc.close()


def test_expired_budget_does_not_render(tmp_path):
    pdf=tmp_path/'a.pdf'; doc=fitz.open();p=doc.new_page();p.insert_text((80,100),'x = 1 (1)');doc.save(pdf);doc.close()
    result=repair_equation_regions(pdf,tmp_path/'out',[1],max_crops=40,time_budget=10,deadline=time.perf_counter()-1)
    assert result==[]
    assert not (tmp_path/'out').exists()


def test_tsv_preserves_blank_and_escaped_cells():
    assert markdown_table_to_tsv('| a | b | c |\n|---|---|---|\n| |a\\|b| |')=='a\tb\tc\n\ta|b\t\n'


def test_suffix_tables_do_not_satisfy_each_other():
    from pdf_md_convert.table_regions import table_number_aliases
    assert not table_number_aliases('8a') & table_number_aliases('8b')


def test_external_citation_and_supplementary_appendix():
    from pdf_md_convert.hybrid_backend import referenced_tables, referenced_internet_appendix_tables
    assert referenced_tables('van Binsbergen et al. (2025, Table A8)')==[]
    assert referenced_tables('Flow of Funds Accounts (Table L 128)')==[]
    assert referenced_internet_appendix_tables('Supplementary Appendix Table A10')==['A10']
    assert referenced_internet_appendix_tables('analysis in Table D.1\nin the Internet Appendix.')==['D1']


def test_left_equation_tag_with_damaged_operator_layer():
    from pdf_md_convert.equation_regions import tag_from_text
    assert tag_from_text('(4) X(t,T ) ( ) C(t, r; K)dK') == '4'
    assert tag_from_text('(4) The fourth item') is None


def test_character_per_line_caption():
    from pdf_md_convert.table_regions import actual_table_caption_lines
    assert actual_table_caption_lines('T\na\nb\nl\ne\n \nI\nI\nAbnormal Returns')==[('II','Abnormal Returns')]


def test_dense_table_preserves_group_headers_and_first_numeric_column():
    from pdf_md_convert.table_regions import reconstruct_clustered_table
    def word(x, y, text, width=16): return (x,y,x+width,y+8,text)
    rows = [[word(195,10,'Assets',42),word(255,10,'Debt',42)],
            [word(x,22,t) for x,t in zip([192,222,252,282],['USA','Foreign','USA','Foreign'])]]
    for y,label,values in [(40,'1970-1990',['.952','.951','19.829','19.563']),
                            (55,'1990-2020',['.920','.960','11.658','23.872']),
                            (70,'1970-2020',['.921','.960','11.810','23.727'])]:
        rows.append([word(70,y,label,95)] + [word(x,y,v) for x,v in zip([192,222,252,282],values)])
    md,confidence,_ = reconstruct_clustered_table(rows)
    cells = [line.split('\t') for line in markdown_table_to_tsv(md).splitlines()]
    assert confidence == 'medium'
    assert cells[0][1:] == ['Assets USA','Assets Foreign','Debt USA','Debt Foreign']
    assert cells[1] == ['1970-1990','.952','.951','19.829','19.563']


def test_inline_refuses_partial_source_overlap_and_preserves_prose():
    from pdf_md_convert.equation_regions import EquationRegion
    mapping = [{'page':1,'blocks':[{'id':'p1-b0','bbox':[0,0,100,50],
                'text':'Important prose and x = 1 (1)','excluded_margin':False}]}]
    region = EquationRegion(1,'1',[50,0,100,50],'x = 1 (1)','crop.png','$$x=1$$','high',[])
    md,events = assemble_inline(mapping,[region],[],lambda text:text)
    assert 'Important prose and x = 1 (1)' in md
    assert events[0]['status']=='unresolved'


def test_rotated_page_uses_text_coordinates_and_transforms_raster_clip(tmp_path):
    pdf=tmp_path/'rotated.pdf'
    with fitz.open() as doc:
        page=doc.new_page(width=612,height=792)
        page.insert_text((180,670),'x = y + z')
        page.set_rotation(90)
        doc.save(pdf)
    cached=CachedDocument(pdf)
    try:
        page=cached[0]
        assert page.rect.height==792
        pix=page.get_pixmap(clip=fitz.Rect(170,640,270,690))
        assert pix.width==50 and pix.height==100
        assert min(pix.samples)<255
    finally: cached.close()


def test_reference_footnotes_and_external_abbreviations_are_not_local_equations():
    from pdf_md_convert.hybrid_backend import referenced_equations
    assert referenced_equations('equations\n15From equations (13) and (14)')==['13','14']
    assert referenced_equations('equation\n10This choice')==[]
    assert referenced_equations('see equation (16) in CGW).')==[]
    assert referenced_equations('Equation (10a) and equation (B9)')==['10a','B9']
