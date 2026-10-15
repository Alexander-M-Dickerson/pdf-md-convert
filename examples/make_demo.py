"""Generate original, deterministic demonstration documents; never overwrite."""
import argparse
import json
from pathlib import Path
import fitz


def make_demo(output: Path):
    if output.exists(): raise FileExistsError(f'Choose a fresh demo directory: {output}')
    output.mkdir(parents=True)
    with fitz.open() as doc:
        p=doc.new_page()
        p.insert_text((60,65),'Synthetic Research Demonstration',fontsize=18)
        p.insert_textbox(fitz.Rect(60,90,520,175),
            'This original demonstration contains an equation and a small numeric table. '
            'The values are fictional and are included solely to test conversion. '
            'Revenue is risk-adjusted; missing values remain blank.', fontsize=11)
        p.insert_text((80,200),'x',fontsize=12);p.insert_text((87,194),'2',fontsize=8)
        p.insert_text((95,200),'= y + 1',fontsize=12);p.insert_text((480,200),'(1)',fontsize=12)
        p.insert_text((60,250),'Table 1. Synthetic outcomes',fontsize=12)
        cells=[['Group','Value','Count'],['Alpha','-1.25','12'],['Beta','0.50',''],['Gamma','2.00','8']]
        for i,row in enumerate(cells):
            for j,value in enumerate(row):p.insert_text((65+j*130,285+i*25),value,fontsize=10)
        for y in [268,295,320,345,370]:p.draw_line((60,y),(450,y))
        for x in [60,190,320,450]:p.draw_line((x,268),(x,370))
        doc.save(output/'Study.pdf')
    (output/'nested').mkdir()
    with fitz.open() as doc:
        p=doc.new_page(width=612,height=792)
        p.insert_text((80,650),'Rotated source: x = 1 + y (2)',fontsize=12)
        p.set_rotation(90);doc.save(output/'nested/Study.PDF')
    with fitz.open() as image_source:
        p=image_source.new_page();p.insert_text((60,100),'Image-only demonstration: visual review is required.',fontsize=14)
        data=p.get_pixmap(matrix=fitz.Matrix(1.4,1.4)).tobytes('png')
        with fitz.open() as doc:
            page=doc.new_page();page.insert_image(page.rect,stream=data);doc.save(output/'image-only.pdf')
    (output/'expected.json').write_text(json.dumps(dict(table=cells,
        equation='x^{2} = y + 1', image_only_requires_review=True),indent=2),encoding='utf-8')


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--output-dir',type=Path,required=True)
    make_demo(parser.parse_args().output_dir)
