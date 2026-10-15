"""Compare the public batch API to a frozen engine run without publishing private inputs."""
import argparse
import hashlib
import json
from pathlib import Path
import shutil
import statistics
import time

from pdf_md_convert.api import ConversionOptions, convert_batch
from pdf_md_convert.benchmark import timing_summary
from pdf_md_convert.verified_accuracy import score_regions


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--manifest', type=Path, required=True)
    parser.add_argument('--engine-runs', type=Path, required=True)
    parser.add_argument('--work-dir', type=Path, required=True)
    parser.add_argument('--verified-references', type=Path)
    parser.add_argument('--repeats', type=int, default=3)
    args = parser.parse_args()
    if args.repeats < 1 or args.work_dir.exists():
        parser.error('Use a fresh work directory and positive repeat count')
    records = json.loads(args.manifest.read_text(encoding='utf-8'))['records']
    inputs = args.work_dir.resolve()/'inputs'
    for record in records:
        source = Path(record['source'])
        assert hashlib.sha256(source.read_bytes()).hexdigest() == record['sha256']
        target = inputs/record['id']/source.name
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, target)
    runs = []; wall = []; batches = []
    for repeat in range(args.repeats):
        print(f'Public batch repeat {repeat+1}: {len(records)} papers', flush=True)
        start = time.perf_counter()
        batch = convert_batch(inputs, output_dir=args.work_dir/f'batch_{repeat+1}',
                              options=ConversionOptions(recursive=True))
        wall.append(time.perf_counter()-start)
        batches.append(batch)
        (args.work_dir/f'run_{repeat+1}.json').write_text(json.dumps(
            dict(wall_seconds=wall[-1], batch=batch.to_dict()),indent=2)+'\n',encoding='utf-8')
        runs.append([dict(id=r.relative_input.split('/')[0], seconds=r.seconds,
                          **({'error':r.error} if r.error else {})) for r in batch.results])
    baseline = json.loads((args.engine_runs/'benchmark_summary.json').read_text(encoding='utf-8'))['baseline']
    summary = dict(candidate=timing_summary(runs), whole_call_seconds=wall,
                   median_whole_call_seconds=statistics.median(wall), baseline=baseline,
                   timing_design='Three public API runs after alternating baseline/engine runs; same host and dependencies.')
    summary['runtime_ratios'] = dict(
        median_batch_seconds=statistics.median(wall)/baseline['median_batch_seconds'],
        p95_paper_seconds=summary['candidate']['p95_paper_seconds']/baseline['p95_paper_seconds'])
    summary['speed_gate'] = all(v <= 1.2 for v in summary['runtime_ratios'].values())
    summary['conversion_error_gate'] = all(b.exit_code == 0 for b in batches)
    differences = []
    # Compare the engine's retained Markdown byte-for-byte after normalizing its generated absolute paths.
    for record in records:
        name = Path(record['source']).stem+'.md'
        baseline_root = (args.engine_runs/'baseline_1'/record['id']).resolve()
        result = next(r for r in batches[0].results if r.relative_input.split('/')[0] == record['id'])
        if result.status != 'completed':
            differences.append(record['id']+': conversion failed')
            continue
        artifact_root = (Path(batches[0].output_dir)/result.artifact_dir).resolve()
        before = (baseline_root/name).read_text(encoding='utf-8')
        after = (artifact_root/name).read_text(encoding='utf-8')
        for file in baseline_root.rglob('*'):
            if file.is_file():
                relative = file.relative_to(baseline_root).as_posix()
                before = before.replace(str(file), relative).replace(file.as_posix(), relative)
        # Source provenance is the only intentional input-path change.
        before = before.replace(str(Path(record['source'])), str(inputs/record['id']/Path(record['source']).name))
        if before != after:
            differences.append(record['id'])
    summary['markdown_differences'] = differences
    if args.verified_references:
        regions = json.loads(args.verified_references.read_text(encoding='utf-8'))['regions']
        verified = []
        for record in records:
            subset = [r for r in regions if r['source_sha256'] == record['sha256'] and r.get('verified')]
            if not subset: continue
            name = Path(record['source']).stem+'.md'
            if not (args.work_dir/'batch_1'/record['id']/name).is_file():
                continue
            before = (args.engine_runs/'baseline_1'/record['id']/name).read_text(encoding='utf-8')
            after = (args.work_dir/'batch_1'/record['id']/name).read_text(encoding='utf-8')
            verified.append(dict(id=record['id'], baseline=score_regions(subset,before)['metrics'],
                                 candidate=score_regions(subset,after)['metrics']))
        summary['reviewed_region_comparison'] = verified
        summary['reviewed_regions_unchanged'] = all(r['baseline']==r['candidate'] for r in verified)
    (args.work_dir/'summary.json').write_text(json.dumps(summary,indent=2)+'\n',encoding='utf-8')
    print(json.dumps({k:v for k,v in summary.items() if k not in {'baseline','candidate','reviewed_region_comparison'}},indent=2))
    return 0 if summary['conversion_error_gate'] and summary['speed_gate'] and not differences else 1


if __name__ == '__main__':
    raise SystemExit(main())
