# -*- mode: python ; coding: utf-8 -*-
import os
from pathlib import Path
from PyInstaller.utils.hooks import collect_all

spec_dir = Path(SPECPATH or ".").resolve()
project = spec_dir.parent
datas = [(str(project / "assets" / "shengqi-logo.png"), "assets")]
binaries = []
hiddenimports = []

for package in (
    "faster_whisper",
    "ctranslate2",
    "av",
    "tokenizers",
    "huggingface_hub",
    "opencc",
):
    package_datas, package_binaries, package_hidden = collect_all(package)
    datas += package_datas
    binaries += package_binaries
    hiddenimports += package_hidden

model_dir = os.environ.get("TRANSCRIBER_SMALL_MODEL_DIR")
if model_dir:
    model_path = Path(model_dir).expanduser().resolve(strict=True)
    if not model_path.is_dir():
        raise ValueError("TRANSCRIBER_SMALL_MODEL_DIR must point to a model directory")
    datas.append((str(model_path), "models/faster-whisper-small"))

icon_path = project / "macOS" / "shengqi.icns"

a = Analysis(
    [str(project / "local_transcriber.py")],
    pathex=[str(project)],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=["torch", "torchaudio", "torchvision"],
    noarchive=False,
    optimize=0,
)
pyz = PYZ(a.pure)
exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="Shengqi",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,
    disable_windowed_traceback=False,
    target_arch="arm64",
    icon=str(icon_path) if icon_path.exists() else None,
)
coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    name="Shengqi",
)
app = BUNDLE(
    coll,
    name="声栖.app",
    bundle_identifier="com.shengqi.transcriber",
    version="0.8.2",
    info_plist={
        "CFBundleDisplayName": "声栖｜本地音视频转文字",
        "LSMinimumSystemVersion": "13.0",
        "NSHighResolutionCapable": True,
    },
)
