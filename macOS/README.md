# macOS Apple Silicon 构建说明

本目录提供 Apple Silicon 构建脚本，供有 Mac 的人生成独立 `.app`，不是普通用户可直接打开的应用包。面向普通用户的 Mac 压缩包只含构建好的 `声栖.app`、许可声明和使用说明，不含源码、构建环境或构建脚本。

## 构建

要求 Apple Silicon、macOS 13 或更新版本、Python 3.12 和 Tk。将小模型目录放到构建机可访问的位置，并设置 `TRANSCRIBER_SMALL_MODEL_DIR` 指向该目录；模型权重不放在 GitHub 源码仓库。然后在 Finder 中运行 `build.command`。脚本使用带版本号的独立构建目录，不会清理或覆盖旧版应用、源文件或用户数据。

产物在 `~/Applications/声栖 构建产物/0.8.2/声栖.app`。构建脚本会 ad-hoc 签名，但不含 Apple Developer ID 签名或公证；首次打开时可能需要在 Finder 中右键并选择“打开”。

当前工作目录没有可用的 Apple Silicon `.app` 成品。Mac 便携包必须先在 Apple Silicon Mac 上构建；此处的 Windows 环境无法生成可运行的 macOS 应用。若提供未经功能验证的 Mac 包，包名与说明只标注“未经测试”，不得声称已确认启动、转写、模型加载或音频导出正常。

打包前仍须核对实际 `.app` 中的第三方依赖、模型和对应许可材料；功能是否测试与第三方文件是否完整是两回事。

本构建目录不会声称在 Windows 上完成 Mac 验证，也不会把源码、虚拟环境或构建日志放进 `.app` 交付压缩包。
