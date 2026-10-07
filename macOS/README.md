# macOS Apple Silicon 构建说明

本目录提供与仓库当前主程序匹配的 Apple Silicon 构建脚本。它用于维护者在 Mac 上生成独立 `.app`，不是给普通用户直接运行的便携应用。普通用户无需下载源码；正式发布的 Mac 包应只包含构建好的 `声栖.app`、运行所需资源、许可声明和使用说明。

## 构建

要求 Apple Silicon、macOS 13 或更新版本、Python 3.12 和 Tk。将小模型目录放到构建机可访问的位置，并设置 `TRANSCRIBER_SMALL_MODEL_DIR` 指向该目录；模型权重不放在 GitHub 源码仓库。然后在 Finder 中运行 `build.command`。脚本使用带版本号的独立构建目录，不会清理或覆盖旧版应用、源文件或用户数据。

产物在 `~/Applications/声栖 构建产物/0.8.0/声栖.app`。构建脚本会 ad-hoc 签名，但不含 Apple Developer ID 签名或公证；首次打开时可能需要在 Finder 中右键并选择“打开”。提交给用户前需在目标 Mac 上检查可启动、加载 small 模型、转写和音频导出，并另行完成应用包的第三方依赖许可核对。

本构建目录不会声称在 Windows 上完成 Mac 验证，也不会把源码、虚拟环境或构建日志放进 `.app` 交付压缩包。
