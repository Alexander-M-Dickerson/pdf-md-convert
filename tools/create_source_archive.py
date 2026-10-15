"""Create a clean project ZIP and SHA-256 manifest from checked distributions."""
import hashlib
from pathlib import Path, PurePosixPath
import tarfile
import zipfile

root = Path(__file__).resolve().parents[1]
dist = root/'dist'
sources = list(dist.glob('*.tar.gz'))
wheels = list(dist.glob('*.whl'))
if len(sources) != 1 or len(wheels) != 1:
    raise SystemExit('Expected exactly one checked sdist and wheel in dist/')
sdist = sources[0]
archive = dist/(sdist.name.removesuffix('.tar.gz')+'-source.zip')
with tarfile.open(sdist, 'r:gz') as source, zipfile.ZipFile(archive, 'w', zipfile.ZIP_DEFLATED) as target:
    for member in source.getmembers():
        parts = PurePosixPath(member.name).parts
        if '..' in parts or PurePosixPath(member.name).is_absolute() or not parts:
            raise ValueError(f'Unsafe distribution member: {member.name}')
        if not member.isfile() or parts[-1] == 'PKG-INFO':
            continue
        stream = source.extractfile(member)
        if stream is None:
            raise ValueError(f'Unreadable distribution member: {member.name}')
        target.writestr('/'.join(['pdf-md-convert', *parts[1:]]), stream.read())
files = [wheels[0], sdist, archive]
(dist/'SHA256SUMS.txt').write_text(''.join(
    hashlib.sha256(p.read_bytes()).hexdigest()+'  '+p.name+'\n' for p in files), encoding='ascii')
print('Created', archive.name, 'and SHA256SUMS.txt')
