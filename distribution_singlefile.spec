# Single-file Windows GUI app for ordinary users (no source files or runtime setup).
import os
from pathlib import Path
from PyInstaller.utils.hooks import collect_all

project = Path(SPECPATH)
datas, binaries, hiddenimports = [], [], []
for name in (
    "声栖_新包使用说明.md",
    "LICENSE",
    "开源组件与许可.txt",
):
    path = project / name
    if path.is_file():
        datas.append((str(path), "."))

small_model_dir = os.environ.get("TRANSCRIBER_SMALL_MODEL_DIR")
if small_model_dir:
    model_path = Path(small_model_dir)
    if not (model_path / "model.bin").is_file() or not (model_path / "config.json").is_file():
        raise FileNotFoundError(f"Incomplete small model directory: {model_path}")
    datas.append((str(model_path), "models/faster-whisper-small"))

for package in (
    "faster_whisper", "ctranslate2", "av", "tokenizers",
    "onnxruntime", "huggingface_hub", "opencc",
):
    package_datas, package_binaries, package_hidden = collect_all(package)
    datas += package_datas
    binaries += package_binaries
    hiddenimports += package_hidden

assets = project / "assets"
if assets.is_dir():
    datas.append((str(assets), "assets"))
licenses = project / "third_party_licenses"
if licenses.is_dir():
    datas.append((str(licenses), "third_party_licenses"))

cuda_dir = os.environ.get("TRANSCRIBER_CUDA_BIN")
if cuda_dir:
    for name in ("cublas64_12.dll", "cublasLt64_12.dll"):
        path = Path(cuda_dir) / name
        if not path.is_file():
            raise FileNotFoundError(path)
        binaries.append((str(path), "ctranslate2"))

a = Analysis(
    [str(project / "local_transcriber.py")], pathex=[str(project)],
    binaries=binaries, datas=datas, hiddenimports=hiddenimports,
    hookspath=[], hooksconfig={}, runtime_hooks=[], excludes=[], noarchive=False,
)
pyz = PYZ(a.pure)
exe = EXE(
    pyz, a.scripts, a.binaries, a.datas, [],
    name="声栖", debug=False, bootloader_ignore_signals=False,
    strip=False, upx=False, console=False,
    disable_windowed_traceback=False,
    icon=str(project / "assets" / "app.ico") if (project / "assets" / "app.ico").is_file() else None,
)
