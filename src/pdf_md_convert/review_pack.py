"""Prepare source images and unverified reference drafts for visual calibration."""
from __future__ import annotations

import argparse
from pathlib import Path
import random
import re
from difflib import SequenceMatcher

import fitz

from pdf_md_convert.reference_validation import digest, parse_markdown, read_json, write_json, normalize_text


def prepare(manifest: Path, output: Path, split: str) -> dict:
    if (output / "verified_regions.json").exists(): raise ValueError("Review pack already exists; refusing to overwrite reviews")
    output.mkdir(parents=True, exist_ok=True)
    suite = {"schema_version":1,"manifest_sha256":digest(manifest),"regions":[],"unavailable":{},"review_tasks":[]}
    for record in read_json(manifest)["records"]:
        if split != "all" and record["split"] != split: continue
        pdf = Path(record["source"])
        if digest(pdf) != record["sha256"]: raise ValueError("Changed source PDF")
        rng = random.Random(int(record["sha256"][:16],16))
        refs = record.get("references",[])
        parsed = parse_markdown(Path(refs[0]["reference"]).read_text(encoding="utf-8-sig")) if refs else None
        with fitz.open(pdf) as doc:
            texts = [p.get_text("text") for p in doc]
            inventories = {k:[] for k in ["text","equation","table"]}
            if parsed:
                for block in parsed.blocks:
                    if block.kind in inventories and (block.kind != "text" or 25 <= len(block.text.split()) <= 130):
                        inventories[block.kind].append(block)
            else:
                from pdf_md_convert.reference_validation import Block
                for i,p in enumerate(doc):
                    for b in p.get_text("blocks"):
                        if 25 <= len(b[4].split()) <= 130 and not re.search(r"[=|]",b[4]):
                            inventories["text"].append(Block("text",b[4],0,0,page=i+1))
            for kind,count in [("text",4),("equation",3),("table",2)]:
                available = inventories[kind]
                chosen = rng.sample(available,min(count,len(available)))
                for j,block in enumerate(chosen,1):
                    page = None
                    if kind == "text":
                        target = normalize_text(block.text)
                        scores = [SequenceMatcher(None,target,normalize_text(t),autojunk=False).find_longest_match().size for t in texts]
                        page = max(range(len(scores)),key=scores.__getitem__)+1 if scores else None
                    elif block.page and 1 <= block.page <= len(doc): page = block.page
                    elif block.key:
                        pattern = r"\(\s*"+re.escape(block.key)+r"\s*\)" if kind == "equation" else r"\bTable\s+"+re.escape(block.key)+r"\b"
                        candidates = [i+1 for i,t in enumerate(texts) if re.search(pattern,t,re.I)]
                        if candidates: page=candidates[-1]
                    region_id=f"{record['id']}-{kind}-{j}"
                    crop_name=""
                    if page:
                        crop_name=f"{record['id']}/page_{page:03d}.png"; dest=output/crop_name
                        dest.parent.mkdir(parents=True,exist_ok=True)
                        if not dest.exists(): doc[page-1].get_pixmap(matrix=fitz.Matrix(1.6,1.6),alpha=False).save(dest)
                    cells = block.cells
                    # Keep the source grid intact; reviewers may bound it with selection.
                    suite["regions"].append({"id":region_id,"source_sha256":record["sha256"],"kind":kind,
                                              "page":page,"key":block.key,"expected":cells if kind=="table" else block.text,
                                              "reference_line":block.line,"reference":refs[0]["reference"] if refs else None,
                                              "crop":crop_name,"crop_sha256":digest(output/crop_name) if crop_name else None,
                                              "verified":False,"review_note":"",
                                              "warning":"Page location is a suggestion; verify against the image. Draft is not ground truth."})
                if len(chosen)<count:
                    suite["review_tasks"].append({"source_sha256":record["sha256"],"kind":kind,"needed":count-len(chosen),
                                                  "reason":"Reference inventory insufficient; inspect source independently. Do not assume absent."})
    write_json(output/"verified_regions.json",suite)
    lines=["# Source verification workpack","","All drafts are **unverified**. No OCR was run.","",
           "Check the suggested page and transcribe against the image. Correct `expected`, `page`, and `key`; record the source evidence in `review_note` and set `verified` only after inspection.",
           "For a bounded table region add `selection: [row_start, row_end, col_start, col_end]` using zero-based, end-exclusive indices and bound `expected` accordingly.",
           "Record truly absent categories under `unavailable[source_sha256][kind]` with a source-based reason. Pending work does not qualify.","","## Regions",""]
    for r in suite["regions"]: lines.append(f"- {r['id']}: [suggested source page]({r['crop']}) — {r['kind']}; reference line {r['reference_line']}")
    (output/"review.md").write_text("\n".join(lines)+"\n",encoding="utf-8")
    return suite


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest",type=Path,required=True)
    parser.add_argument("--output-dir",type=Path,required=True)
    parser.add_argument("--split",choices=["development","holdout","all"],default="development")
    args=parser.parse_args(argv)
    suite=prepare(args.manifest,args.output_dir,args.split)
    print(f"Prepared {len(suite['regions'])} unverified drafts; {len(suite['review_tasks'])} source-inventory tasks")
    return 0
