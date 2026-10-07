#!/bin/bash
set -euo pipefail

PROJECT_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
VERSION="0.8.2"
BUILD_ROOT="$HOME/Library/Application Support/声栖-build/$VERSION"
SOURCE_COPY="$BUILD_ROOT/source"
APP_DIR="$HOME/Applications/声栖 构建产物/$VERSION"
VENV="$BUILD_ROOT/venv"

if [[ "$(uname -s)" != "Darwin" || "$(uname -m)" != "arm64" ]]; then
  echo "此构建脚本需要 Apple Silicon Mac。"
  exit 1
fi
if [[ "$(sw_vers -productVersion | cut -d. -f1)" -lt 13 ]]; then
  echo "需要 macOS 13 或更高版本。"
  exit 1
fi
if [[ -z "${TRANSCRIBER_SMALL_MODEL_DIR:-}" || ! -d "$TRANSCRIBER_SMALL_MODEL_DIR" ]]; then
  echo "请先设置 TRANSCRIBER_SMALL_MODEL_DIR，指向已获许可且可用于发布的 small 模型目录。"
  exit 1
fi
if [[ -e "$BUILD_ROOT" || -e "$APP_DIR/声栖.app" ]]; then
  echo "版本 $VERSION 的构建目录或产物已存在。为避免覆盖，请先更换版本号或手动备份并处理旧产物。"
  exit 1
fi

mkdir -p "$BUILD_ROOT" "$APP_DIR" "$HOME/Applications" "$SOURCE_COPY"
python3.12 -m venv "$VENV"
"$VENV/bin/python" -m pip install --upgrade pip wheel
"$VENV/bin/python" -m pip install -r "$PROJECT_ROOT/macOS/requirements-macos.txt"

# Build in an isolated, versioned copy. No rm -rf and no edits to the working tree.
cp "$PROJECT_ROOT/local_transcriber.py" "$SOURCE_COPY/"
cp -R "$PROJECT_ROOT/assets" "$SOURCE_COPY/"
cp -R "$PROJECT_ROOT/macOS" "$SOURCE_COPY/macOS"
ICONSET="$SOURCE_COPY/macOS/shengqi.iconset"
mkdir -p "$ICONSET"
for size in 16 32 64 128 256 512; do
  sips -z "$size" "$size" "$SOURCE_COPY/assets/shengqi-logo.png" --out "$ICONSET/icon_${size}x${size}.png" >/dev/null
  double=$((size * 2))
  sips -z "$double" "$double" "$SOURCE_COPY/assets/shengqi-logo.png" --out "$ICONSET/icon_${size}x${size}@2x.png" >/dev/null
done
iconutil -c icns "$ICONSET" -o "$SOURCE_COPY/macOS/shengqi.icns"
cd "$SOURCE_COPY/macOS"
"$VENV/bin/pyinstaller" --noconfirm --clean local_transcriber_macos.spec
test -d "$SOURCE_COPY/macOS/dist/声栖.app"
ditto "$SOURCE_COPY/macOS/dist/声栖.app" "$APP_DIR/声栖.app"
codesign --force --deep --sign - "$APP_DIR/声栖.app"
codesign --verify --deep --strict "$APP_DIR/声栖.app"

echo "构建完成：$APP_DIR/声栖.app"
echo "此应用未经过 Apple 公证。此脚本没有在 Windows 上测试。"
