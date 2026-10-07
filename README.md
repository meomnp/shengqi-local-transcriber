# 声栖｜本地音视频转文字

声栖把电脑里的视频、录音、课程、会议和剧集素材转成文字稿、字幕或结构化数据，也可以只把视频中的声音导出为独立音频文件。媒体在本机处理，不需要在线转写服务，也不会上传素材。

## 使用方式

Windows 单文件版解压后直接运行 `声栖.exe`。主界面分为“转成文字”和“提取音频”两项任务：可以选择一个文件，也可以选择包含多个素材的文件夹。转写结果默认保存为 Markdown 阅读稿；高级选项还支持 SRT、TXT、JSON、语言选择、模型选择和输出范围。

“提取音频”就是从视频中取出声音轨道，另存为 MP3、M4A、WAV 或 FLAC，不进行语音识别。MP3 兼容性好且体积小，WAV 文件较大，转换格式不会提升原视频音质。音频格式选项只影响音频提取，不影响转文字。

Windows 0.8.2 单文件包内带有 small 模型，可断网转写；其他模型如果未随包提供，可能需要联网下载。自动识别结果可能有误，人名、数字和重要台词应对照原素材核验。macOS Apple Silicon 版：未经测试。

输入语言可自动识别，也可手选中文、英语、日语、韩语、法语、德语、西班牙语、俄语、葡萄牙语、阿拉伯语、印地语、印尼语、意大利语、泰语、越南语、土耳其语或马来语。输出语言目前仅支持“保留原语言”或“翻译成英文”，不支持任选翻译目标；turbo 不用于翻译成英文。输出格式支持 SRT、纯文字 TXT、带时间码 TXT、Markdown 和结构化 JSON；JSON 是整理后的转写数据，不是识别引擎的原始内部响应。

主界面提供“最近任务”，本机最多保存 100 条任务元数据（路径、状态和数量），不保存转写正文。可打开结果目录或复制路径；清空历史不会删除导出结果。

Mac Apple Silicon 版可在 GitHub Actions 中手动运行 `.github/workflows/build-macos.yml` 构建。仓库是公开的，工作流使用标准 Mac runner；输出只作为保留 1 天的短期下载文件，不在源码仓库长期保存。为遵守 GitHub Free 的 Actions 存储额度，超过 480 MB 时工作流会停止上传，不触发付费超额。

## 本地源码开发

需要 Python 3.12。开发环境依赖见 `requirements-desktop.txt`。音视频处理依赖 PyAV；项目运行及 PyInstaller 构建还会用到 `faster-whisper`、OpenCC 和 Pillow。构建 Windows 单文件应用：

```powershell
python -m pip install -r requirements-desktop.txt
$env:TRANSCRIBER_SMALL_MODEL_DIR = 'C:\path\to\faster-whisper-small'
python -m PyInstaller --noconfirm distribution_singlefile.spec
```

如未设置模型目录，构建出的应用会按运行时规则使用用户已有模型缓存；模型下载或分发时须遵守各自上游条款。CUDA 运行库仅在明确提供其文件并完成许可证核对时加入。发行者应先逐项审查实际捆绑依赖、许可证、NOTICE 和 FFmpeg 构建配置。

命令行入口也可被本机脚本调用。例如转写单文件：

```powershell
LocalTranscriber-CLI.exe --input-file 'D:\media\sample.mp4' --output-folder 'D:\output' --model small --language zh
```

只提取音频：

```powershell
LocalTranscriber-CLI.exe --input-file 'D:\media\sample.mp4' --output-folder 'D:\output' --extract-audio-only --audio-format mp3
```

常用选项包括 `--input-folder`、`--recursive`、`--output-format srt|txt_plain|txt_timed|md|json`、`--output-scope per_episode|combined|both`、`--device auto|cuda|cpu`、`--self-test` 与 `--load-model`。运行 `LocalTranscriber-CLI.exe --help` 查看完整参数。

运行自动化测试：

```powershell
python -m unittest discover -v -p 'test_*.py'
```

`test_packaged_audio.py` 需要已构建的 CLI 参数 `--exe <path>`，只生成合成音频测试数据，不读取个人素材。`export_source.py` 仅导出白名单内的源文件到新的本地审阅目录，不会执行 Git、上传或发布。

## 隐私与许可

声栖不会联网上传待处理媒体；若选择下载未包含的模型，下载过程会访问模型托管服务。应用源码按 MIT 许可证发布，见 `LICENSE`。第三方依赖和模型继续适用各自许可证，见 `third_party_licenses/` 与 `开源组件与许可.txt`。本仓库只发布源码、构建说明和必要的许可文本，不发布单文件应用二进制、模型权重、个人媒体、运行日志或本地构建缓存。

Windows 与 macOS 的应用包是独立交付物，不是源码仓库的一部分。公开分发预编译应用前，应核对实际打包依赖、NOTICE、PyAV/FFmpeg 构建配置及模型来源；本仓库的 MIT 许可不应被理解为对所有第三方二进制/模型的授权。

## 自行修改与迭代

有兴趣研究或继续开发的用户，可以从 [GitHub 上的声栖源码仓库](https://github.com/meomnp/shengqi-local-transcriber) 下载/克隆本仓库，安装 Python 3.12 与 `requirements-desktop.txt` 中的依赖，修改源码后运行测试。普通用户无需下载源码或安装 Python，直接使用对应系统的应用包即可。
