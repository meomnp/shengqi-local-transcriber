"""Build-time SHA-256 inventory for a finished portable bundle, not a signature."""
import argparse
import hashlib
import json
from pathlib import Path

MANIFEST = 'package-integrity.json'


def file_hash(path):
    digest = hashlib.sha256()
    with path.open('rb') as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b''):
            digest.update(chunk)
    return digest.hexdigest()


def create_manifest(root):
    root = Path(root).resolve(strict=True)
    if not (root / '声栖.exe').is_file():
        raise ValueError('Not a portable application directory')
    destination = root / MANIFEST
    if destination.exists():
        raise FileExistsError('Refusing to overwrite an existing integrity manifest')
    files = []
    for path in sorted(root.rglob('*')):
        if path.is_symlink():
            raise ValueError(f'Symlink not supported: {path.name}')
        if path.is_file():
            files.append({'path': path.relative_to(root).as_posix(),
                          'bytes': path.stat().st_size, 'sha256': file_hash(path)})
    destination.write_text(json.dumps({'schema': 1, 'files': files}, indent=2,
                                      ensure_ascii=False), encoding='utf-8')
    return len(files)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('directory')
    args = parser.parse_args()
    print(f'Inventoried {create_manifest(args.directory)} files')
