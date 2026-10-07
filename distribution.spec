# Portable build: dependencies are installed in the selected Python environment.
# Set TRANSCRIBER_SMALL_MODEL_DIR to include an already-downloaded offline model.
# No private workstation paths belong in the public build recipe.
import os
from pathlib import Path
from PyInstaller.utils.hooks import collect_all
from PyInstaller.building.datastruct import Tree

project = Path(SPECPATH)
datas, binaries, hiddenimports = [], [], []
datas.append((str(project / '声栖_新包使用说明.md'), '.'))
datas.append((str(project / '校验程序完整性.ps1'), '.'))
small_model_dir = os.environ.get('TRANSCRIBER_SMALL_MODEL_DIR')
if small_model_dir:
    model_path = Path(small_model_dir)
    if not (model_path / 'model.bin').is_file() or not (model_path / 'config.json').is_file():
        raise FileNotFoundError(f'Incomplete small model directory: {model_path}')
    datas += [
        (source, str(Path(destination).parent))
        for destination, source, _typecode in Tree(
            str(model_path), prefix='models/faster-whisper-small'
        )
    ]
for package in ('faster_whisper', 'ctranslate2', 'av', 'tokenizers', 'onnxruntime', 'huggingface_hub', 'opencc'):
    package_datas, package_binaries, package_hidden = collect_all(package)
    datas += package_datas
    binaries += package_binaries
    hiddenimports += package_hidden

# Optional CUDA runtime must be explicitly supplied and its licence retained.
cuda_dir = os.environ.get('TRANSCRIBER_CUDA_BIN')
if cuda_dir:
    for name in ('cublas64_12.dll', 'cublasLt64_12.dll'):
        path = Path(cuda_dir) / name
        if not path.is_file():
            raise FileNotFoundError(path)
        binaries.append((str(path), 'ctranslate2'))

icon = project / 'assets' / 'app.ico'
if (project / 'assets').is_dir():
    datas.append((str(project / 'assets'), 'assets'))
if (project / 'third_party_licenses').is_dir():
    datas.append((str(project / 'third_party_licenses'), 'third_party_licenses'))

a = Analysis([str(project / 'local_transcriber.py')], pathex=[str(project)],
             binaries=binaries, datas=datas, hiddenimports=hiddenimports,
             hookspath=[], hooksconfig={}, runtime_hooks=[], excludes=[], noarchive=False)
pyz = PYZ(a.pure)
gui = EXE(pyz, a.scripts, [], exclude_binaries=True, name='声栖',
          console=False, strip=False, upx=False, icon=str(icon) if icon.is_file() else None)
cli = EXE(pyz, a.scripts, [], exclude_binaries=True, name='LocalTranscriber-CLI',
          console=True, strip=False, upx=False, icon=str(icon) if icon.is_file() else None)
coll = COLLECT(gui, cli, a.binaries, a.datas, strip=False, upx=False, name='声栖')
