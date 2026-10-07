"""Create a local, allowlisted source review folder. Never publishes or runs Git."""
import argparse
import hashlib
import json
from pathlib import Path
import shutil

SOURCE_FILES = (
    'local_transcriber.py', 'distribution.spec', 'requirements-desktop.txt', '.gitignore',
    'prepare_logo_assets.py', 'test_logo_assets.py',
    'test_output_rules.py', 'test_distribution_paths.py', 'test_gui_layout.py',
    'test_packaged_audio.py', 'export_source.py', 'test_source_export.py',
    'assets/app.ico', 'assets/shengqi-logo.png', 'assets/shengqi-logo-64.png',
    'package_integrity.py', '校验程序完整性.ps1', 'README.md', 'LICENSE', '开源组件与许可.txt',
    '创建桌面快捷方式.ps1', 'finish_windows_package.ps1', '声栖_新包使用说明.md',
    'third_party_licenses/CTranslate2-4.8.1-LICENSE.txt',
    'third_party_licenses/tokenizers-0.23.1-LICENSE.txt',
    'third_party_licenses/SOURCES.md',
    'third_party_licenses/Systran-faster-whisper-small-MODEL-NOTICE.txt',
    'third_party_licenses/Systran-faster-whisper-small-MODEL-MIT.txt',
    'third_party_licenses/PyAV-BSD-3-Clause-LICENSE.txt',
    'macOS/README.md', 'macOS/requirements-macos.txt',
    'macOS/local_transcriber_macos.spec', 'macOS/build.command', 'macOS/launch.command',
)


def export_source(root: Path, destination: Path):
    root = root.resolve(strict=True)
    destination = destination.absolute()
    if destination.exists():
        raise FileExistsError('Destination exists; choose a new review folder')
    resolved = []
    for relative in SOURCE_FILES:
        source = (root / relative).resolve(strict=True)
        if not source.is_relative_to(root) or not source.is_file():
            raise ValueError(f'Source is outside project or not a file: {relative}')
        resolved.append((relative, source))
    destination.mkdir(parents=True, exist_ok=False)
    manifest = []
    for relative, source in resolved:
        target = destination / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, target)
        manifest.append({'path': relative, 'bytes': target.stat().st_size,
                         'sha256': hashlib.sha256(target.read_bytes()).hexdigest()})
    (destination / 'SOURCE_REVIEW_MANIFEST.json').write_text(
        json.dumps({'status': 'source_release_candidate',
                    'application_license': 'MIT',
                    'files': manifest}, indent=2, ensure_ascii=False), encoding='utf-8')
    return manifest


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--destination', required=True)
    args = parser.parse_args()
    files = export_source(Path(__file__).parent, Path(args.destination))
    print(f'Local review only: copied {len(files)} allowlisted files. No upload performed.')
