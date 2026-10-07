#!/bin/bash
set -euo pipefail
APP="$HOME/Applications/声栖 构建产物/0.8.0/声栖.app"
if [[ ! -d "$APP" ]]; then
  echo "尚未构建。请先按 macOS/README.md 配置环境并运行 build.command。"
  exit 1
fi
open "$APP"
