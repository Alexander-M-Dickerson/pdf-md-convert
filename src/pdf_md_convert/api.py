"""Stable batch API. Execution success does not imply source-verified accuracy."""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from importlib.metadata import version
from pathlib import Path
import hashlib
import json
import os
import re
import time
import uuid

from . import __version__
from .hybrid_backend import convert_hybrid


def digest(path: Path) -> str:
    h = hashlib.sha256()
    with path.open('rb') as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b''):
            h.update(chunk)
    return h.hexdigest()


def atomic_json(path: Path, value) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(path.name + '.tmp')
    temp.write_text(json.dumps(value, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    temp.replace(path)


@dataclass(frozen=True)
class ConversionOptions:
    profile: str = 'balanced'
    recursive: bool = False
    resume: bool = False
    overwrite: bool = False
    strict_qa: bool = False
    repair_placement: str = 'appendix'
    max_equation_crops: int | None = None
    max_table_crops: int | None = None
    time_budget: float = 10.0
    max_total_repair_seconds: float = 30.0

    def resolved(self) -> dict:
        if self.profile not in {'fast', 'balanced'}:
            raise ValueError('profile must be fast or balanced')
        if self.repair_placement not in {'appendix', 'inline'}:
            raise ValueError('repair_placement must be appendix or inline')
        if self.resume and self.overwrite:
            raise ValueError('resume and overwrite are mutually exclusive')
        eq = self.max_equation_crops if self.max_equation_crops is not None else 40
        table = self.max_table_crops if self.max_table_crops is not None else 35
        if min(eq, table, self.time_budget, self.max_total_repair_seconds) < 0:
            raise ValueError('Crop limits and time budgets cannot be negative')
        return dict(repair_equations=self.profile == 'balanced', repair_tables=self.profile == 'balanced',
                    max_equation_crops=eq, max_table_crops=table, time_budget=self.time_budget,
                    max_total_repair_seconds=self.max_total_repair_seconds,
                    repair_placement=self.repair_placement, max_repair_pages=0,
                    local_repair_tier='deterministic', fast_backend='pymupdf')


@dataclass
class DocumentResult:
    id: str
    source: str
    relative_input: str
    source_sha256: str
    output_markdown: str
    status: str = 'running'
    quality_status: str = 'not_checked'
    seconds: float = 0.0
    page_count: int = 0
    artifact_dir: str = ''
    qa: dict = field(default_factory=dict)
    outputs: dict = field(default_factory=dict)
    error: str | None = None
    signature: dict = field(default_factory=dict)


@dataclass
class BatchResult:
    output_dir: str
    manifest: str
    results: list[DocumentResult]
    exit_code: int
    seconds: float

    def to_dict(self):
        return asdict(self)


def _discover(source: Path, output: Path, recursive: bool) -> list[Path]:
    if source.is_file():
        if source.suffix.lower() != '.pdf':
            raise ValueError('Input file must have a .pdf extension')
        return [source]
    if not source.is_dir():
        raise ValueError(f'Input does not exist: {source}')
    files = []
    for base, dirs, names in os.walk(source, followlinks=False):
        dirs[:] = [d for d in dirs if d not in {'.git', '.venv', '_artifacts', '_review', '_workpacks'}
                   and not (Path(base)/d).is_symlink()
                   and not (Path(base)/d/'_batch_manifest.json').is_file()
                   and not (Path(base)/d/'_hybrid_conversion_summary.json').is_file()
                   and (Path(base)/d).resolve() != output]
        for name in names:
            p = Path(base)/name
            if p.suffix.lower() == '.pdf' and not p.is_symlink():
                files.append(p)
        if not recursive:
            break
    if not files:
        raise ValueError(f'No PDFs found: {source}')
    return sorted(files, key=lambda p: (p.relative_to(source).as_posix().casefold(), p.as_posix()))


def _contained(root: Path, relative: str) -> Path:
    path = (root / relative).resolve()
    if root not in path.parents:
        raise ValueError(f'Managed path escapes output root: {relative}')
    return path


def _hard_qa(qa: dict) -> bool:
    return bool(qa.get('missing_referenced_tables') or qa.get('missing_referenced_equation_crops'))


def _portable_artifacts(artifact: Path, output: Path, final_md: Path, source_md: Path) -> None:
    """Only rewrite known generated paths, never arbitrary links in source prose."""
    files = [p for p in artifact.rglob('*') if p.is_file()]
    known = sorted(files, key=lambda p: len(str(p)), reverse=True)
    path_map = {spelling: p.relative_to(output).as_posix() for p in known for spelling in [str(p), p.as_posix()]}
    for file in files:
        if file.suffix == '.json':
            def rewrite(v):
                if isinstance(v, dict):
                    return {k: rewrite(x) for k, x in v.items()}
                if isinstance(v, list):
                    return [rewrite(x) for x in v]
                if isinstance(v, str):
                    return path_map.get(v, v)
                return v
            atomic_json(file, rewrite(json.loads(file.read_text(encoding='utf-8'))))
        elif file.suffix == '.md':
            text = file.read_text(encoding='utf-8')
            for p in known:
                rel = Path(os.path.relpath(p, file.parent)).as_posix()
                text = text.replace(str(p), rel).replace(p.as_posix(), rel)
            file.write_text(text, encoding='utf-8')
    text = source_md.read_text(encoding='utf-8')
    # Engine-generated metadata and audit links use these managed prefixes.
    for prefix in ['_audit/', '_equation_crops/', '_table_crops/', '_table_tsv/']:
        rel = Path(os.path.relpath(artifact / prefix, final_md.parent)).as_posix().rstrip('/') + '/'
        text = text.replace(prefix, rel)
    final_md.parent.mkdir(parents=True, exist_ok=True)
    temp = final_md.with_name(final_md.name + '.tmp')
    temp.write_text(text, encoding='utf-8')
    temp.replace(final_md)


def convert_batch(input_path: str | Path, *, output_dir: str | Path | None = None,
                  options: ConversionOptions | None = None) -> BatchResult:
    options = options or ConversionOptions()
    settings = options.resolved()
    source = Path(input_path).expanduser().resolve()
    output = (Path(output_dir).expanduser().resolve() if output_dir else
              source / f'{source.name}_md' if source.is_dir() else source.parent / f'{source.stem}_md')
    if output == source or output in source.parents:
        raise ValueError('Output must not equal or contain the input path')
    pdfs = _discover(source, output, options.recursive)
    root = source if source.is_dir() else source.parent
    manifest_path = output / '_batch_manifest.json'
    existing = json.loads(manifest_path.read_text(encoding='utf-8')) if manifest_path.exists() else {}
    if existing and existing.get('schema_version') != 1:
        raise ValueError('Unsupported batch manifest version')
    if existing and existing.get('input_root') != str(root):
        raise ValueError('Output belongs to another input root; choose a separate output directory')
    previous = {r['relative_input']: r for r in existing.get('documents', [])}
    signature = dict(version=__version__, dependencies={n: version(n) for n in ['pymupdf', 'ftfy']}, settings=settings)
    prepared = []; targets = set()
    for pdf in pdfs:
        rel = pdf.relative_to(root)
        target_rel = rel.with_suffix('.md').as_posix()
        if rel.parts[0] in {'_artifacts', '_review', '_workpacks'} or target_rel.casefold() in {'_batch_qa_summary.md'} or target_rel.casefold() in targets:
            raise ValueError(f'Reserved or colliding output path: {target_rel}')
        targets.add(target_rel.casefold())
        target = _contained(output, target_rel)
        h = digest(pdf)
        old = previous.get(rel.as_posix())
        if target.exists() and not options.overwrite:
            valid = (options.resume and old and old.get('status') in {'completed', 'skipped'}
                     and old['source_sha256'] == h and old.get('signature', existing.get('signature')) == signature
                     and old.get('outputs') and all(_contained(output, p).is_file()
                         and digest(_contained(output, p)) == v for p,v in old['outputs'].items()))
            if not valid:
                raise FileExistsError(f'Existing or edited output: {target}. Use a new output directory or --overwrite.')
        else:
            valid = False
        ident = re.sub(r'[^A-Za-z0-9._-]', '_', pdf.stem)[:32] + '-' + hashlib.sha256(rel.as_posix().encode()).hexdigest()[:12]
        prepared.append((pdf, rel, target, h, old if valid else None, ident))
    output.mkdir(parents=True, exist_ok=True)
    results = []; started = time.perf_counter()
    def save():
        documents = dict(previous)
        documents.update({r.relative_input: asdict(r) for r in results})
        atomic_json(manifest_path, dict(schema_version=1, input_root=str(root), signature=signature,
                                       documents=list(documents.values())))
        atomic_json(output/'_batch_qa_summary.json', dict(schema_version=1, papers=[
            dict(id=r.id, status=r.status, quality_status=r.quality_status, qa=r.qa, error=r.error) for r in results]))
        lines = ['# Batch QA', '', 'Execution completion is not source-verified accuracy.', '']
        for r in results:
            lines += [f'- `{r.relative_input}`: {r.status}; {r.quality_status}' + (f'; {r.error}' if r.error else '')]
            for key, label in [
                ('missing_referenced_tables', 'Missing referenced tables'),
                ('missing_referenced_equation_crops', 'Missing referenced equation crops'),
                ('missing_high_confidence_equation_repairs', 'Equation reconstruction review queue'),
                ('missing_internet_appendix_tables', 'External appendix table references'),
                ('low_text_pages', 'Low-text pages requiring inspection'),
            ]:
                if r.qa.get(key):
                    lines.append(f"  - {label}: {', '.join(map(str, r.qa[key]))}")
        (output/'_batch_qa_summary.md').write_text('\n'.join(lines)+'\n', encoding='utf-8')
    for pdf, rel, target, h, old, ident in prepared:
        if old:
            result = DocumentResult(**old); result.status = 'skipped'; results.append(result); save(); continue
        result = DocumentResult(ident, str(pdf), rel.as_posix(), h, target.relative_to(output).as_posix())
        result.signature = signature
        results.append(result)
        artifact = output/'_artifacts'/ident/uuid.uuid4().hex[:12]
        result.artifact_dir = artifact.relative_to(output).as_posix()
        save(); tick = time.perf_counter()
        try:
            summary = convert_hybrid(str(pdf), output_dir_arg=str(artifact), **settings)
            entry = summary['results'][0]
            residual = json.loads(Path(entry['residuals_json']).read_text(encoding='utf-8'))
            result.qa = residual['qa']; result.page_count = entry['page_count']
            result.qa['low_text_pages'] = [r['page'] for r in residual['risks'] if r['text_chars'] < 100]
            result.quality_status = 'needs_review'
            _portable_artifacts(artifact, output, target, Path(entry['output_markdown']))
            result.outputs = {p.relative_to(output).as_posix(): digest(p) for p in artifact.rglob('*') if p.is_file()}
            result.outputs[target.relative_to(output).as_posix()] = digest(target)
            result.status = 'completed'
        except KeyboardInterrupt:
            result.status = 'interrupted'; result.seconds = time.perf_counter()-tick; save(); raise
        except (Exception, SystemExit) as exc:
            result.status = 'failed'; result.error = f'{type(exc).__name__}: {exc}'
        result.seconds = time.perf_counter()-tick
        save()
    code = 1 if any(r.status == 'failed' for r in results) else 3 if options.strict_qa and any(_hard_qa(r.qa) for r in results) else 0
    return BatchResult(str(output), str(manifest_path), results, code, time.perf_counter()-started)


def convert_file(input_path: str | Path, *, output_dir: str | Path | None = None,
                 options: ConversionOptions | None = None) -> DocumentResult:
    if not Path(input_path).expanduser().is_file():
        raise ValueError('convert_file requires one existing PDF')
    return convert_batch(input_path, output_dir=output_dir, options=options).results[0]
