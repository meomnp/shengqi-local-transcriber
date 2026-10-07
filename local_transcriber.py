from __future__ import annotations

import argparse
import gc
import json
import os
import queue
import re
import subprocess
import sys
import threading
import time
import traceback
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path
from typing import Callable, Iterable


for _stream in (sys.stdout, sys.stderr):
    if _stream is not None and hasattr(_stream, "reconfigure"):
        try:
            _stream.reconfigure(encoding="utf-8")
        except (OSError, ValueError):
            pass


APP_NAME = "声栖｜本地音视频转文字"
APP_VERSION = "0.8.0"
IS_FROZEN = bool(getattr(sys, "frozen", False))
APP_ROOT = Path(sys.executable).resolve().parent if IS_FROZEN else Path(__file__).resolve().parent
BUILD_RUNTIME = Path(
    os.environ.get(
        "LOCAL_TRANSCRIBER_BUILD_RUNTIME",
        str(APP_ROOT / "runtime"),
    )
)
if sys.platform == "darwin":
    default_model_root = (
        Path.home() / "Library" / "Application Support" / "本地视频台词转写器" / "models"
    )
elif os.name == "nt":
    default_model_root = Path(os.environ.get("LOCALAPPDATA", str(Path.home() / "AppData" / "Local"))) / "LocalTranscriber" / "models"
else:
    default_model_root = Path(os.environ.get("XDG_DATA_HOME", str(Path.home() / ".local" / "share"))) / "local-transcriber" / "models"
MODEL_ROOT = Path(
    os.environ.get("LOCAL_TRANSCRIBER_MODEL_DIR", str(default_model_root))
).resolve()
MEDIA_EXTENSIONS = {
    ".mp4", ".mkv", ".mov", ".avi", ".wmv", ".m4v", ".webm", ".flv",
    ".ts", ".m2ts", ".mp3", ".wav", ".m4a", ".aac", ".flac", ".ogg", ".opus",
}
VIDEO_EXTENSIONS = {
    ".mp4", ".mkv", ".mov", ".avi", ".wmv", ".m4v", ".webm", ".flv",
    ".ts", ".m2ts",
}
AUDIO_EXPORT_FORMATS = {
    "mp3": {
        "extension": ".mp3", "container": "mp3", "codec": "libmp3lame",
        "sample_format": "s16p", "bit_rate": 192_000,
    },
    "m4a": {
        "extension": ".m4a", "container": "ipod", "codec": "aac",
        "sample_format": "fltp", "bit_rate": 192_000,
    },
    "wav": {
        "extension": ".wav", "container": "wav", "codec": "pcm_s16le",
        "sample_format": "s16", "bit_rate": None,
    },
    "flac": {
        "extension": ".flac", "container": "flac", "codec": "flac",
        "sample_format": "s16", "bit_rate": None,
    },
}
GENERATED_MEDIA_FOLDERS = {'提取音频', '逐集台词', '逐个文件', '_转写记录', '切片工程'}
DLL_DIRECTORY_HANDLES: list[object] = []


@dataclass
class Options:
    input_folder: str
    output_folder: str
    model: str = "small"
    language: str = "zh"
    device: str = "auto"
    recursive: bool = False
    write_srt: bool = False
    write_txt: bool = False
    write_combined: bool = True
    overwrite: bool = False
    beam_size: int = 5
    initial_prompt: str = ""
    input_file: str = ""
    task: str = "transcribe"
    write_timed: bool = False
    chinese_script: str = "simplified"
    write_md: bool = False
    write_json: bool = False
    # 新界面只使用一个格式下拉框和一个范围下拉框。留空时继续兼容旧命令行参数。
    output_format: str = ""
    output_scope: str = ""


EXPORT_FORMATS = {"srt", "txt_plain", "txt_timed", "md", "json"}
EXPORT_SCOPES = {"per_episode", "combined", "both"}


def resolve_export_plan(options: Options) -> dict:
    """Normalize the new two-dropdown export selection while retaining old CLI calls."""
    if options.output_format or options.output_scope:
        if options.output_format not in EXPORT_FORMATS:
            raise ValueError("请选择有效的输出格式。")
        if options.output_scope not in EXPORT_SCOPES:
            raise ValueError("请选择有效的输出范围。")
        scope = "combined" if options.input_file else options.output_scope
        return {
            "modern": True,
            "format": options.output_format,
            "scope": scope,
            "per_episode": scope in {"per_episode", "both"},
            "combined": scope in {"combined", "both"},
        }
    return {
        "modern": False,
        "format": "",
        "scope": "legacy",
        "per_episode": options.write_srt or options.write_txt,
        "combined": options.write_combined or options.write_timed or options.write_md or options.write_json,
    }


def prepare_runtime() -> None:
    MODEL_ROOT.mkdir(parents=True, exist_ok=True)
    os.environ.pop("SSLKEYLOGFILE", None)
    os.environ["HF_HOME"] = str(MODEL_ROOT / "hf_home")
    os.environ["HUGGINGFACE_HUB_CACHE"] = str(MODEL_ROOT / "hub")
    os.environ["XDG_CACHE_HOME"] = str(MODEL_ROOT / "xdg")
    os.environ["HF_HUB_DISABLE_XET"] = "1"
    if not IS_FROZEN and BUILD_RUNTIME.is_dir():
        if str(BUILD_RUNTIME) not in sys.path:
            sys.path.insert(0, str(BUILD_RUNTIME))
    if hasattr(os, "add_dll_directory"):
        bundle_root = Path(getattr(sys, "_MEIPASS", APP_ROOT))
        candidates = (
            BUILD_RUNTIME / "av.libs",
            BUILD_RUNTIME / "bin",
            bundle_root,
            bundle_root / "av.libs",
            bundle_root / "bin",
            bundle_root / "ctranslate2",
        )
        for directory in candidates:
            if directory.is_dir():
                DLL_DIRECTORY_HANDLES.append(os.add_dll_directory(str(directory)))


def natural_key(value: str) -> list[object]:
    return [int(part) if part.isdigit() else part.casefold() for part in re.split(r"(\d+)", value)]


def format_timestamp(seconds: float, srt: bool = False) -> str:
    milliseconds = max(0, round(seconds * 1000))
    hours, remainder = divmod(milliseconds, 3_600_000)
    minutes, remainder = divmod(remainder, 60_000)
    secs, millis = divmod(remainder, 1000)
    separator = "," if srt else "."
    return f"{hours:02d}:{minutes:02d}:{secs:02d}{separator}{millis:03d}"


def probe_media_duration(path: Path) -> float:
    """Read the complete media-container duration without uploading the file."""
    import av

    candidates: list[float] = []
    with av.open(str(path), mode="r") as container:
        if container.duration is not None:
            candidates.append(float(container.duration) / 1_000_000)
        for stream in container.streams:
            if stream.duration is not None and stream.time_base is not None:
                candidates.append(float(stream.duration * stream.time_base))
    valid = [value for value in candidates if value > 0]
    if not valid:
        raise ValueError(f"无法读取媒体完整时长：{path}")
    return max(valid)


def episode_timeline(episodes: list[dict]) -> tuple[list[dict], float]:
    """Map complete source files onto an untrimmed, naturally ordered master timeline."""
    offset = 0.0
    rows: list[dict] = []
    for episode in episodes:
        duration = max(0.0, float(episode.get("duration_seconds", 0.0)))
        row = {
            "order": int(episode["order"]),
            "label": episode_label(episode["relative_source"], int(episode["order"])),
            "relative_source": episode["relative_source"],
            "duration_seconds": duration,
            "duration": format_timestamp(duration),
            "master_start_seconds": offset,
            "master_start": format_timestamp(offset),
            "master_end_seconds": offset + duration,
            "master_end": format_timestamp(offset + duration),
            "duration_source": episode.get("duration_source", "unknown"),
        }
        rows.append(row)
        offset += duration
    return rows, offset


def write_duration_report(path: Path, episodes: list[dict], title: str, single: bool = False) -> None:
    rows, total = episode_timeline(episodes)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8-sig", newline="\n") as file:
        heading = "媒体完整时长" if single else "各文件完整时长与自然顺序母版表"
        file.write(f"# 《{title}》{heading}\n\n")
        file.write("本表按完整源文件自然顺序计算，尚未进行剧情重排或删减。\n\n")
        file.write("| 顺序 | 集数／文件 | 源文件 | 完整时长 | 母版累计起点 | 母版累计终点 | 时长依据 |\n")
        file.write("|---:|---|---|---:|---:|---:|---|\n")
        for row in rows:
            source = str(row["relative_source"]).replace("|", "／")
            basis = "媒体容器" if row["duration_source"] == "media_container" else "转写结果估算"
            file.write(
                f"| {row['order']} | {row['label']} | `{source}` | {row['duration']} | "
                f"{row['master_start']} | {row['master_end']} | {basis} |\n"
            )
        file.write(f"\n未删减自然顺序母版总时长：**{format_timestamp(total)}**\n")


def discover_media(input_folder: Path, output_folder: Path, recursive: bool) -> list[Path]:
    iterator: Iterable[Path] = input_folder.rglob("*") if recursive else input_folder.iterdir()
    input_resolved = input_folder.resolve()
    output_resolved = output_folder.resolve()
    files: list[Path] = []
    for path in iterator:
        if not path.is_file() or path.suffix.casefold() not in MEDIA_EXTENSIONS:
            continue
        relative = path.relative_to(input_folder)
        if any(part in GENERATED_MEDIA_FOLDERS for part in relative.parts[:-1]):
            continue
        if output_resolved != input_resolved:
            try:
                path.resolve().relative_to(output_resolved)
                continue
            except ValueError:
                pass
        files.append(path)
    return sorted(files, key=lambda p: natural_key(str(p.relative_to(input_folder))))


def discover_video_files(input_folder: Path, recursive: bool) -> list[Path]:
    """Find video sources only; existing audio files are not duplicated."""
    iterator: Iterable[Path] = input_folder.rglob("*") if recursive else input_folder.iterdir()
    files = [
        path
        for path in iterator
        if path.is_file() and path.suffix.casefold() in VIDEO_EXTENSIONS
        and not any(part in GENERATED_MEDIA_FOLDERS for part in path.relative_to(input_folder).parts[:-1])
    ]
    return sorted(files, key=lambda path: natural_key(str(path.relative_to(input_folder))))


def extract_audio_track(
    source: Path,
    destination: Path,
    audio_format: str = "mp3",
    cancel_event: threading.Event | None = None,
    on_frame_progress: Callable[[float], None] | None = None,
) -> None:
    """Decode a video's audio stream and write a compatible local audio file."""
    if audio_format not in AUDIO_EXPORT_FORMATS:
        raise ValueError("不支持的音频格式。")
    prepare_runtime()
    import av

    profile = AUDIO_EXPORT_FORMATS[audio_format]
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_name(destination.name + ".part")
    if temporary.exists():
        temporary.unlink()
    try:
        with av.open(str(source), mode="r") as input_container:
            input_stream = next(
                (stream for stream in input_container.streams if stream.type == "audio"),
                None,
            )
            if input_stream is None:
                raise ValueError("视频中没有可提取的音轨。")
            source_rate = int(input_stream.codec_context.sample_rate or 48_000)
            sample_rate = source_rate if 8_000 <= source_rate <= 48_000 else 48_000
            duration_seconds = float(input_container.duration or 0) / 1_000_000
            if not duration_seconds and input_stream.duration is not None:
                duration_seconds = float(input_stream.duration * input_stream.time_base)
            processed_samples = 0
            last_reported = -1.0
            source_layout = getattr(input_stream.codec_context.layout, "name", "")
            layout = "mono" if source_layout == "mono" else "stereo"

            with av.open(str(temporary), mode="w", format=str(profile["container"])) as output_container:
                output_stream = output_container.add_stream(str(profile["codec"]), rate=sample_rate)
                output_stream.layout = layout
                if profile["bit_rate"]:
                    output_stream.bit_rate = int(profile["bit_rate"])
                resampler = av.AudioResampler(
                    format=str(profile["sample_format"]),
                    layout=layout,
                    rate=sample_rate,
                )
                for frame in input_container.decode(input_stream):
                    if cancel_event and cancel_event.is_set():
                        raise InterruptedError("已停止音频提取。")
                    processed_samples += frame.samples
                    if on_frame_progress and duration_seconds > 0:
                        fraction = min(processed_samples / (source_rate * duration_seconds), 0.99)
                        if fraction - last_reported >= 0.02:
                            on_frame_progress(fraction)
                            last_reported = fraction
                    for converted in resampler.resample(frame):
                        converted.pts = None
                        for packet in output_stream.encode(converted):
                            output_container.mux(packet)
                for converted in resampler.resample(None):
                    converted.pts = None
                    for packet in output_stream.encode(converted):
                        output_container.mux(packet)
                for packet in output_stream.encode(None):
                    output_container.mux(packet)
        os.replace(temporary, destination)
    except Exception:
        if temporary.exists():
            temporary.unlink()
        raise


def extract_audio_batch(
    input_folder: str,
    output_folder: str,
    input_file: str = "",
    recursive: bool = False,
    audio_format: str = "mp3",
    overwrite: bool = False,
    on_log: Callable[[str], None] = print,
    on_progress: Callable[[int, int, str], None] | None = None,
    on_file_progress: Callable[[int, int, str, float], None] | None = None,
    cancel_event: threading.Event | None = None,
) -> dict:
    """Extract one video or a naturally sorted folder without loading Whisper."""
    source_root = Path(input_folder).expanduser().resolve()
    chosen_output = Path(output_folder).expanduser().resolve()
    if not source_root.is_dir():
        raise NotADirectoryError(f"找不到输入文件夹：{source_root}")
    if audio_format not in AUDIO_EXPORT_FORMATS:
        raise ValueError("请选择有效的音频格式。")
    if input_file:
        source = Path(input_file).expanduser().resolve()
        if not source.is_file() or source.suffix.casefold() not in VIDEO_EXTENSIONS:
            raise ValueError("请选择支持的视频文件；音频文件无需再次提取音频。")
        files = [source]
    else:
        files = discover_video_files(source_root, recursive)
    if not files:
        raise FileNotFoundError("所选位置中没有发现支持的视频文件。")

    audio_root = chosen_output / "提取音频"
    audio_root.mkdir(parents=True, exist_ok=True)
    extension = str(AUDIO_EXPORT_FORMATS[audio_format]["extension"])
    results: list[dict] = []
    failures: list[dict] = []
    skipped = 0
    total = len(files)
    on_log(f"发现 {total} 个视频文件；本功能不会加载语音识别模型。")
    for index, source in enumerate(files, start=1):
        if cancel_event and cancel_event.is_set():
            on_log("已收到停止请求；不会开始下一个文件。")
            break
        relative = Path(source.name) if input_file else source.relative_to(source_root)
        destination = (audio_root / relative).with_suffix(extension)
        if on_progress:
            on_progress(index - 1, total, str(relative))
        if destination.exists() and not overwrite:
            skipped += 1
            on_log(f"[{index}/{total}] 已存在，跳过：{destination.name}")
            results.append({"source": str(source), "audio": str(destination), "reused": True})
        else:
            try:
                on_log(f"[{index}/{total}] 正在提取音频：{relative}")
                if on_file_progress:
                    extract_audio_track(
                        source, destination, audio_format, cancel_event,
                        on_frame_progress=lambda fraction: on_file_progress(index, total, str(relative), fraction),
                    )
                else:
                    extract_audio_track(source, destination, audio_format, cancel_event)
                results.append({"source": str(source), "audio": str(destination), "reused": False})
            except InterruptedError:
                on_log("当前文件尚未完成，已清理临时文件并停止。")
                break
            except Exception as error:
                failures.append({"source": str(source), "error": str(error)})
                on_log(f"[{index}/{total}] 提取失败：{relative}（{error}）")
        if on_progress:
            on_progress(index, total, str(relative))

    manifest = {
        "app": APP_NAME,
        "version": APP_VERSION,
        "operation": "extract_audio",
        "created_at": datetime.now().isoformat(timespec="seconds"),
        "input_folder": str(source_root),
        "input_file": str(Path(input_file).resolve()) if input_file else "",
        "output_folder": str(audio_root),
        "audio_format": audio_format,
        "total_discovered": total,
        "completed": len([item for item in results if not item["reused"]]),
        "available": len(results),
        "skipped": skipped,
        "failed": len(failures),
        "cancelled": bool(cancel_event and cancel_event.is_set()),
        "results": results,
        "failures": failures,
    }
    record_root = chosen_output / "_转写记录"
    write_internal_json(record_root / "音频提取任务清单.json", manifest)
    on_log(f"音频提取完成：新增 {manifest['completed']}，已有 {skipped}，失败 {len(failures)}。")
    on_log(f"音频目录：{audio_root}")
    if not results and failures:
        raise RuntimeError(f"没有成功提取音频：{failures[0]['error']}")
    return manifest


def write_srt(path: Path, segments: list[dict], heading: str = "") -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8-sig", newline="\n") as file:
        index = 1
        if heading:
            # SRT没有元数据区；使用1毫秒说明条目备注文件名和完整时长，台词时间码保持不变。
            file.write(f"{index}\n00:00:00,000 --> 00:00:00,001\n{heading}\n\n")
            index += 1
        for segment in segments:
            file.write(f"{index}\n")
            file.write(
                f"{format_timestamp(segment['start'], srt=True)} --> "
                f"{format_timestamp(segment['end'], srt=True)}\n"
            )
            file.write(f"{segment['text']}\n\n")
            index += 1


def write_series_srt(path: Path, episodes: list[dict], title: str = "") -> None:
    """Write one review-oriented SRT while preserving every episode's original timecodes."""
    path.parent.mkdir(parents=True, exist_ok=True)
    cue_index = 1
    with path.open("w", encoding="utf-8-sig", newline="\n") as file:
        total_duration = sum(float(episode.get("duration_seconds", 0.0)) for episode in episodes)
        file.write(f"{cue_index}\n")
        file.write("00:00:00,000 --> 00:00:00,001\n")
        file.write(
            f"【《{title}》｜音视频数量 {len(episodes)}｜全部完整时长 "
            f"{format_timestamp(total_duration)}｜以下时间码均为各自源文件原始时间】\n\n"
        )
        cue_index += 1
        for episode in episodes:
            label = episode_label(episode["relative_source"], int(episode["order"]))
            duration = format_timestamp(float(episode.get("duration_seconds", 0.0)))
            file.write(f"{cue_index}\n")
            file.write("00:00:00,000 --> 00:00:00,001\n")
            file.write(f"【{label}｜{episode['relative_source']}｜完整时长 {duration}】\n\n")
            cue_index += 1
            for segment in episode["segments"]:
                file.write(f"{cue_index}\n")
                file.write(
                    f"{format_timestamp(segment['start'], srt=True)} --> "
                    f"{format_timestamp(segment['end'], srt=True)}\n"
                )
                file.write(f"{segment['text']}\n\n")
                cue_index += 1


def write_txt(
    path: Path,
    title: str,
    source: Path,
    segments: list[dict],
    duration_seconds: float = 0.0,
    timed: bool = True,
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8-sig", newline="\n") as file:
        file.write(f"{title}\n")
        file.write(f"来源：{source}\n")
        file.write(f"完整时长：{format_timestamp(duration_seconds)}\n")
        file.write("说明：本稿由本地语音识别生成；人名、同音字和断句需结合原视频校对。\n\n")
        for segment in segments:
            prefix = (
                f"[{format_timestamp(segment['start'])} → {format_timestamp(segment['end'])}] "
                if timed else ""
            )
            file.write(f"{prefix}{segment['text']}\n")


def write_internal_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="\n") as file:
        json.dump(payload, file, ensure_ascii=False, indent=2)


def load_internal_json(path: Path) -> dict | None:
    try:
        with path.open("r", encoding="utf-8") as file:
            return json.load(file)
    except (OSError, ValueError):
        return None


def convert_chinese_script(segments: list[dict], target: str) -> list[dict]:
    if target == "preserve":
        return [dict(segment) for segment in segments]
    if target not in {"simplified", "traditional"}:
        raise ValueError("未知的中文字形选项。")
    from opencc import OpenCC
    converter = OpenCC("t2s" if target == "simplified" else "s2t")
    return [dict(segment, text=converter.convert(segment["text"])) for segment in segments]


def episode_label(relative_source: str, order: int) -> str:
    stem = Path(relative_source).stem
    match = re.search(r"第\s*([0-9零〇一二两三四五六七八九十百]+)\s*[集话話]", stem)
    if match:
        return f"第{match.group(1)}集"
    match = re.search(r"(?:\bEP(?:ISODE)?[\s._-]*|^)(\d+)(?=$|[\s._-])", stem, re.I)
    if match:
        return f"第{int(match.group(1))}集"
    return f"第{order}个文件"


def write_combined(path: Path, input_folder: Path, episodes: list[dict], title: str = "", single: bool = False, translated: bool = False, timed: bool = True) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8-sig", newline="\n") as file:
        file.write(f"《{title or input_folder.name}》{'英文译稿' if translated else '文字稿'}（本地机器识别）\n")
        file.write(f"来源文件夹：{input_folder}\n")
        file.write(f"生成时间：{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n")
        total_duration = sum(float(episode.get("duration_seconds", 0.0)) for episode in episodes)
        file.write(f"音视频数量：{len(episodes)}\n")
        file.write(f"全部音视频总时长：{format_timestamp(total_duration)}\n")
        file.write("说明：各集时间码均保留对应源文件的原始时间，从本集00:00开始；机器转写需结合原视频校对。\n\n")
        for episode in episodes:
            file.write("=" * 72 + "\n")
            label = '来源文件' if single else episode_label(episode['relative_source'], episode['order'])
            file.write(f"{label}｜{episode['relative_source']}\n")
            if episode.get("duration_seconds") is not None:
                file.write(f"完整时长：{format_timestamp(float(episode['duration_seconds']))}\n")
            file.write("=" * 72 + "\n\n")
            for segment in episode["segments"]:
                prefix = f"[{format_timestamp(segment['start'])} → {format_timestamp(segment['end'])}] " if timed else ''
                file.write(f"{prefix}{segment['text']}\n")
            file.write("\n")


def write_markdown(path: Path, input_folder: Path, episodes: list[dict], title: str = "", single: bool = False, translated: bool = False) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8-sig", newline="\n") as file:
        file.write(f"# 《{title or input_folder.name}》{'英文译稿' if translated else '台词稿'}\n\n")
        file.write(f"- 来源：`{input_folder}`\n")
        file.write(f"- 生成时间：{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n")
        total = sum(float(episode.get("duration_seconds", 0.0)) for episode in episodes)
        file.write(f"- 音视频数量：**{len(episodes)}**\n")
        file.write(f"- 全部音视频总时长：**{format_timestamp(total)}**\n")
        file.write("- 时间码说明：每一集都保留对应源文件的原始时间，从本集 `00:00` 开始，不转换为累计时间。\n")
        file.write("- 说明：本稿由本地语音识别生成；每个时间码均对应本章节所列的源文件。\n\n")
        file.write("## 文件与完整时长\n\n")
        file.write("| 顺序 | 集数／文件 | 源文件 | 完整时长 | 时长依据 |\n")
        file.write("|---:|---|---|---:|---|\n")
        for episode in episodes:
            source = str(episode["relative_source"]).replace("|", "／")
            label = "单个音视频" if single else episode_label(source, int(episode["order"]))
            basis = "媒体容器" if episode.get("duration_source") == "media_container" else "转写结果估算"
            file.write(
                f"| {episode['order']} | {label} | `{source}` | "
                f"{format_timestamp(float(episode.get('duration_seconds', 0.0)))} | {basis} |\n"
            )
        file.write("\n")
        for episode in episodes:
            label = "单个音视频" if single else episode_label(episode["relative_source"], episode["order"])
            file.write(f"## {label}\n\n")
            file.write(f"源文件：`{episode['relative_source']}`\n\n")
            if episode.get("duration_seconds") is not None:
                file.write(f"完整时长：`{format_timestamp(float(episode['duration_seconds']))}`\n\n")
            for segment in episode["segments"]:
                file.write(
                    f"- **{format_timestamp(segment['start'])}–{format_timestamp(segment['end'])}** "
                    f"{segment['text']}\n"
                )
            file.write("\n")


def write_episode_markdown(
    path: Path,
    episode: dict,
    title: str,
    translated: bool = False,
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    label = episode_label(episode["relative_source"], int(episode["order"]))
    duration = float(episode.get("duration_seconds", 0.0))
    basis = "媒体容器" if episode.get("duration_source") == "media_container" else "转写结果估算"
    with path.open("w", encoding="utf-8-sig", newline="\n") as file:
        file.write(f"# 《{title}》{label}{'英文译稿' if translated else '台词稿'}\n\n")
        file.write(f"- 源文件：`{episode['relative_source']}`\n")
        file.write(f"- 完整时长：**{format_timestamp(duration)}**（{basis}）\n")
        file.write("- 时间码说明：以下时间均相对于本源文件开头，不以第一句说话时间重新归零。\n\n")
        file.write("## 台词\n\n")
        for segment in episode["segments"]:
            file.write(
                f"- **{format_timestamp(segment['start'])}–{format_timestamp(segment['end'])}** "
                f"{segment['text']}\n"
            )


def write_transcript_json(
    path: Path,
    input_folder: Path,
    episodes: list[dict],
    title: str = "",
    single: bool = False,
    translated: bool = False,
) -> None:
    """Write a stable, application-facing transcript instead of exposing cache internals."""
    total_duration = sum(float(episode.get("duration_seconds", 0.0)) for episode in episodes)
    payload = {
        "schema_version": "1.0",
        "app": APP_NAME,
        "app_version": APP_VERSION,
        "title": title or input_folder.name,
        "mode": "single_file" if single else "series",
        "content": "english_translation" if translated else "transcript",
        "source_folder": str(input_folder),
        "created_at": datetime.now().isoformat(timespec="seconds"),
        "notice": "本稿由本地语音识别生成；人名、同音字、断句和时间码需结合原视频校对。",
        "media_count": len(episodes),
        "total_media_duration_seconds": round(total_duration, 3),
        "total_media_duration": format_timestamp(total_duration),
        "timestamp_basis": "Each episode keeps its own source-file timecodes starting at 00:00; timestamps are not converted to a cumulative master timeline.",
        "episodes": [],
    }
    for episode in episodes:
        payload["episodes"].append(
            {
                "order": int(episode["order"]),
                "label": "单个音视频" if single else episode_label(episode["relative_source"], episode["order"]),
                "source_file": episode["relative_source"],
                "duration_seconds": round(float(episode.get("duration_seconds", 0.0)), 3),
                "duration": format_timestamp(float(episode.get("duration_seconds", 0.0))),
                "duration_source": episode.get("duration_source", "unknown"),
                "segments": [
                    {
                        "start_seconds": round(float(segment["start"]), 3),
                        "end_seconds": round(float(segment["end"]), 3),
                        "start": format_timestamp(segment["start"]),
                        "end": format_timestamp(segment["end"]),
                        "text": segment["text"],
                    }
                    for segment in episode["segments"]
                ],
            }
        )
    write_internal_json(path, payload)


def export_extension(output_format: str) -> str:
    return {"srt": ".srt", "txt_plain": ".txt", "txt_timed": ".txt", "md": ".md", "json": ".json"}[output_format]


def write_episode_export(
    output_format: str,
    path: Path,
    input_folder: Path,
    episode: dict,
    title: str,
    translated: bool = False,
) -> None:
    source = input_folder / episode["relative_source"]
    label = episode_label(episode["relative_source"], int(episode["order"]))
    duration = float(episode.get("duration_seconds", 0.0))
    if output_format == "srt":
        write_srt(
            path,
            episode["segments"],
            heading=f"【{label}｜{episode['relative_source']}｜完整时长 {format_timestamp(duration)}】",
        )
    elif output_format in {"txt_plain", "txt_timed"}:
        write_txt(
            path,
            label,
            source,
            episode["segments"],
            duration_seconds=duration,
            timed=output_format == "txt_timed",
        )
    elif output_format == "md":
        write_episode_markdown(path, episode, title, translated)
    elif output_format == "json":
        write_transcript_json(
            path,
            input_folder,
            [episode],
            title=f"{title}｜{label}",
            single=False,
            translated=translated,
        )
    else:
        raise ValueError("不支持的输出格式。")


def combined_export_path(output_folder: Path, title: str, suffix: str, output_format: str) -> Path:
    if output_format == "srt":
        return output_folder / f"{title}_{suffix}_分集原时间码.srt"
    if output_format == "txt_plain":
        return output_folder / f"{title}_{suffix}_纯文字.txt"
    if output_format == "txt_timed":
        return output_folder / f"{title}_{suffix}_带时间码.txt"
    if output_format == "md":
        return output_folder / f"{title}_{suffix}.md"
    if output_format == "json":
        return output_folder / f"{title}_{suffix}.json"
    raise ValueError("不支持的输出格式。")


def write_full_export(
    output_format: str,
    path: Path,
    input_folder: Path,
    episodes: list[dict],
    title: str,
    single: bool = False,
    translated: bool = False,
) -> None:
    if output_format == "srt":
        if single:
            episode = episodes[0]
            write_srt(
                path,
                episode["segments"],
                heading=(
                    f"【{episode['relative_source']}｜完整时长 "
                    f"{format_timestamp(float(episode.get('duration_seconds', 0.0)))}】"
                ),
            )
        else:
            write_series_srt(path, episodes, title)
    elif output_format in {"txt_plain", "txt_timed"}:
        write_combined(
            path,
            input_folder,
            episodes,
            title,
            single,
            translated,
            timed=output_format == "txt_timed",
        )
    elif output_format == "md":
        write_markdown(path, input_folder, episodes, title, single, translated)
    elif output_format == "json":
        write_transcript_json(path, input_folder, episodes, title, single, translated)
    else:
        raise ValueError("不支持的输出格式。")


def choose_device(requested: str) -> tuple[str, str, dict]:
    prepare_runtime()
    import ctranslate2

    cuda_count = ctranslate2.get_cuda_device_count()
    if requested == "cuda" and cuda_count < 1:
        raise RuntimeError("已要求使用CUDA，但当前未检测到可用NVIDIA GPU。")
    device = "cuda" if requested == "cuda" or (requested == "auto" and cuda_count > 0) else "cpu"
    compute_type = "int8_float16" if device == "cuda" else "int8"
    details = {
        "cuda_device_count": cuda_count,
        "supported_compute_types": sorted(ctranslate2.get_supported_compute_types(device)),
    }
    return device, compute_type, details


def resolve_model_source(model_name: str) -> str:
    """Prefer an offline model bundled with the app, then a user cache, then download."""
    for candidate in (
        APP_ROOT / "models" / f"faster-whisper-{model_name}",
        APP_ROOT / "models" / model_name,
        APP_ROOT / "_internal" / "models" / f"faster-whisper-{model_name}",
        APP_ROOT / "_internal" / "models" / model_name,
        Path(getattr(sys, "_MEIPASS", APP_ROOT)) / "models" / f"faster-whisper-{model_name}",
        Path(getattr(sys, "_MEIPASS", APP_ROOT)) / "models" / model_name,
        MODEL_ROOT / f"faster-whisper-{model_name}",
        MODEL_ROOT / model_name,
    ):
        if (candidate / "model.bin").is_file() and (candidate / "config.json").is_file():
            return str(candidate)
    return model_name


def transcribe_folder(
    options: Options,
    on_log: Callable[[str], None] = print,
    on_progress: Callable[[int, int, str], None] | None = None,
    cancel_event: threading.Event | None = None,
    on_segment_progress: Callable[[int, int, str, float], None] | None = None,
) -> dict:
    input_folder = Path(options.input_folder).expanduser().resolve()
    output_folder = Path(options.output_folder).expanduser().resolve()
    if not input_folder.is_dir():
        raise NotADirectoryError(f"找不到输入文件夹：{input_folder}")
    export_plan = resolve_export_plan(options)
    if not export_plan["modern"] and not options.write_srt and not options.write_txt and not options.write_combined and not options.write_timed and not options.write_md and not options.write_json:
        raise ValueError("至少选择一种输出格式。")

    if options.task not in ('transcribe', 'translate'):
        raise ValueError('不支持的任务类型。')
    if options.task == 'translate' and options.model == 'turbo':
        raise ValueError('翻译成英文请选择 small、medium 或 large-v3；turbo 不用于翻译。')
    output_folder.mkdir(parents=True, exist_ok=True)
    if options.input_file:
        source = Path(options.input_file).resolve()
        if not source.is_file() or source.suffix.lower() not in MEDIA_EXTENSIONS:
            raise ValueError('请选择支持的本地音视频文件。')
        if source.parent != input_folder:
            raise ValueError('单文件输入目录不匹配。')
        files = [source]
    else:
        files = discover_media(input_folder, output_folder, options.recursive)
    if not files:
        raise FileNotFoundError("所选文件夹中没有发现支持的音视频文件。")

    device, compute_type, device_details = choose_device(options.device)
    on_log(f"发现 {len(files)} 个音视频文件。")
    on_log(f"正在加载模型 {options.model}（{device}/{compute_type}），同一批次只加载一次……")
    from faster_whisper import WhisperModel

    model_source = resolve_model_source(options.model)
    if model_source != options.model:
        on_log(f"使用内置离线模型：{model_source}")
    try:
        model = WhisperModel(
            model_source,
            device=device,
            compute_type=compute_type,
            download_root=str(MODEL_ROOT),
        )
    except Exception as error:
        if options.device != "auto" or device != "cuda":
            raise
        reason = str(error)[:240]
        on_log(f"自动模式无法启动 NVIDIA 推理（{reason}），已切换到 CPU/int8。")
        device, compute_type, device_details = choose_device("cpu")
        device_details = {**device_details, "auto_fallback_reason": reason}
        model = WhisperModel(
            model_source,
            device=device,
            compute_type=compute_type,
            download_root=str(MODEL_ROOT),
        )
    on_log("模型加载完成，开始逐个转写。")

    per_episode_root = output_folder / "逐个文件"
    data_root = output_folder / "_转写记录"
    episodes: list[dict] = []
    results: list[dict] = []
    total = len(files)

    for index, source in enumerate(files, start=1):
        if cancel_event and cancel_event.is_set():
            on_log("已收到停止请求；不会开始下一个文件。")
            break
        relative = source.relative_to(input_folder)
        relative_base = relative.with_suffix("")
        output_base = per_episode_root / relative_base
        srt_path = output_base.with_suffix(".srt")
        txt_path = output_base.with_suffix(".txt")
        data_path = data_root / (str(relative) + f'.{options.task}.json')
        if on_progress:
            on_progress(index - 1, total, str(relative))

        cached = load_internal_json(data_path)
        source_stat = source.stat()
        cache_valid = bool(
            cached
            and cached.get("source") == str(source)
            and cached.get("source_size") == source_stat.st_size
            and cached.get("source_mtime_ns") == source_stat.st_mtime_ns
            and cached.get("model") == options.model
            and cached.get("language_requested") == options.language
            and cached.get("task", "transcribe") == options.task
            and cached.get("initial_prompt", "") == options.initial_prompt
            and cached.get("beam_size", 5) == options.beam_size
        )
        if cache_valid and not options.overwrite:
            segments = cached.get("segments", [])
            on_log(f"[{index}/{total}] 已有匹配结果，跳过识别：{relative}")
        else:
            on_log(f"[{index}/{total}] 正在转写：{relative}")
            kwargs = {
                "language": options.language or None,
                "task": options.task,
                "beam_size": options.beam_size,
                "vad_filter": True,
                "condition_on_previous_text": True,
            }
            if options.initial_prompt.strip():
                kwargs["initial_prompt"] = options.initial_prompt.strip()
            def transcribe_current_file(active_model):
                generator, info = active_model.transcribe(str(source), **kwargs)
                current_segments = []
                cancelled = False
                last_reported = -1.0
                estimated_duration = float(info.duration or 0.0)
                for segment in generator:
                    if cancel_event and cancel_event.is_set():
                        cancelled = True
                        break
                    text = segment.text.strip()
                    if text:
                        current_segments.append(
                            {"start": segment.start, "end": segment.end, "text": text}
                        )
                    if on_segment_progress and estimated_duration > 0:
                        fraction = min(float(segment.end) / estimated_duration, 0.99)
                        if fraction - last_reported >= 0.02:
                            on_segment_progress(index, total, str(relative), fraction)
                            last_reported = fraction
                return current_segments, info, cancelled

            try:
                segments, info, cancelled_current = transcribe_current_file(model)
            except Exception as error:
                if options.device != "auto" or device != "cuda":
                    raise
                reason = str(error)[:240]
                on_log(
                    f"NVIDIA 推理不可用（{reason}）；自动模式改用 CPU/int8，"
                    f"当前文件将从头重试。"
                )
                model = None
                gc.collect()
                device, compute_type, device_details = choose_device("cpu")
                device_details = {**device_details, "auto_fallback_reason": reason}
                model = WhisperModel(
                    model_source,
                    device=device,
                    compute_type=compute_type,
                    download_root=str(MODEL_ROOT),
                )
                if on_segment_progress:
                    on_segment_progress(index, total, str(relative), 0.0)
                segments, info, cancelled_current = transcribe_current_file(model)
            if cancelled_current:
                on_log(f"当前文件在完成前被停止，未写入不完整结果：{relative}")
                break
            cached = {
                "source": str(source),
                "relative_source": str(relative),
                "source_size": source_stat.st_size,
                "source_mtime_ns": source_stat.st_mtime_ns,
                "model": options.model,
                "task": options.task,
                "initial_prompt": options.initial_prompt,
                "beam_size": options.beam_size,
                "language_requested": options.language,
                "language_detected": info.language,
                "language_probability": info.language_probability,
                "duration_seconds": info.duration,
                "device": device,
                "compute_type": compute_type,
                "segments": segments,
            }

        transcription_duration = float(cached.get("duration_seconds", 0.0) or 0.0)
        try:
            media_duration = probe_media_duration(source)
            duration_source = "media_container"
        except Exception as error:
            media_duration = transcription_duration or max(
                (float(segment.get("end", 0.0)) for segment in segments),
                default=0.0,
            )
            duration_source = "transcription_estimate"
            on_log(f"[{index}/{total}] 未能读取媒体容器完整时长，暂用转写结果估算：{relative}（{error}）")
        cached["media_duration_seconds"] = media_duration
        cached["media_duration_source"] = duration_source
        write_internal_json(data_path, cached)

        detected_language = str(cached.get("language_detected", "") or "").strip()
        detected_probability = cached.get("language_probability")
        if detected_language:
            if isinstance(detected_probability, (int, float)):
                on_log(f"[{index}/{total}] 识别语言：{detected_language}（置信度 {float(detected_probability):.0%}）")
            else:
                on_log(f"[{index}/{total}] 识别语言：{detected_language}")

        output_segments = convert_chinese_script(segments, options.chinese_script)
        episode = {
            "order": index,
            "relative_source": str(relative),
            "duration_seconds": media_duration,
            "duration_source": duration_source,
            "segments": output_segments,
        }
        episodes.append(episode)

        episode_export_path: Path | None = None
        if export_plan["modern"] and export_plan["per_episode"]:
            episode_export_path = output_base.with_suffix(export_extension(export_plan["format"]))
            series_title = Path(options.input_file).stem if options.input_file else input_folder.name
            write_episode_export(
                export_plan["format"],
                episode_export_path,
                input_folder,
                episode,
                series_title,
                options.task == "translate",
            )
        elif not export_plan["modern"]:
            if options.write_srt:
                write_srt(srt_path, output_segments)
            if options.write_txt:
                write_txt(txt_path, relative.stem, source, output_segments, media_duration, timed=True)
        results.append(
            {
                "source": str(source),
                "duration_seconds": media_duration,
                "duration": format_timestamp(media_duration),
                "duration_source": duration_source,
                "segments": len(segments),
                "srt": str(srt_path) if not export_plan["modern"] and options.write_srt else None,
                "txt": str(txt_path) if not export_plan["modern"] and options.write_txt else None,
                "export": str(episode_export_path) if episode_export_path else None,
                "record": str(data_path),
                "reused": cache_valid and not options.overwrite,
            }
        )
        if on_progress:
            on_progress(index, total, str(relative))

    title = Path(options.input_file).stem if options.input_file else input_folder.name
    if options.input_file:
        suffix = '英文译稿' if options.task == 'translate' else '文字稿'
    else:
        suffix = '合并英文译稿' if options.task == 'translate' else '合并文字稿'
    combined_path = output_folder / f"{title}_{suffix}.txt"
    full_export_path: Path | None = None
    if export_plan["modern"] and export_plan["combined"] and episodes:
        full_export_path = combined_export_path(output_folder, title, suffix, export_plan["format"])
        write_full_export(
            export_plan["format"],
            full_export_path,
            input_folder,
            episodes,
            title,
            bool(options.input_file),
            options.task == "translate",
        )
    if not export_plan["modern"] and options.write_combined and episodes:
        write_combined(combined_path, input_folder, episodes, title, bool(options.input_file), options.task == 'translate', timed=False)
    timed_path = output_folder / f'{title}_{suffix}_带时间码.txt'
    if not export_plan["modern"] and options.write_timed and episodes:
        write_combined(timed_path, input_folder, episodes, title, bool(options.input_file), options.task == 'translate', timed=True)
    markdown_path = output_folder / f'{title}_{suffix}_Markdown阅读版.md'
    if not export_plan["modern"] and options.write_md and episodes:
        write_markdown(markdown_path, input_folder, episodes, title, bool(options.input_file), options.task == 'translate')
    transcript_json_path = output_folder / f'{title}_{suffix}_结构化.json'
    if not export_plan["modern"] and options.write_json and episodes:
        write_transcript_json(transcript_json_path, input_folder, episodes, title, bool(options.input_file), options.task == 'translate')
    duration_report_path = output_folder / (
        f"{title}_媒体完整时长.md" if options.input_file else f"{title}_各文件完整时长与自然顺序母版表.md"
    )
    if not export_plan["modern"] and episodes:
        write_duration_report(duration_report_path, episodes, title, bool(options.input_file))

    manifest = {
        "app": APP_NAME,
        "version": APP_VERSION,
        "created_at": datetime.now().isoformat(timespec="seconds"),
        "options": asdict(options),
        "device": device,
        "compute_type": compute_type,
        "device_details": device_details,
        "total_discovered": total,
        "completed": len(results),
        "cancelled": bool(cancel_event and cancel_event.is_set()),
        "combined_txt": str(combined_path) if not export_plan["modern"] and options.write_combined and episodes else None,
        "full_export": str(full_export_path) if full_export_path else None,
        "results": results,
        "timed_txt": str(timed_path) if not export_plan["modern"] and options.write_timed and episodes else None,
        "markdown": str(markdown_path) if not export_plan["modern"] and options.write_md and episodes else None,
        "structured_json": str(transcript_json_path) if not export_plan["modern"] and options.write_json and episodes else None,
        "duration_report": str(duration_report_path) if not export_plan["modern"] and episodes else None,
    }
    manifest_path = (data_root / "转写任务清单.json") if export_plan["modern"] else (output_folder / "转写任务清单.json")
    write_internal_json(manifest_path, manifest)
    on_log(f"本批次完成：{len(results)}/{total} 个文件。")
    on_log(f"输出目录：{output_folder}")
    return manifest


def self_test(load_model: bool = False) -> dict:
    device, compute_type, details = choose_device("auto")
    result = {
        "status": "ok",
        "app": APP_NAME,
        "version": APP_VERSION,
        "python": sys.executable,
        "standalone": IS_FROZEN,
        "app_root": str(APP_ROOT),
        "build_runtime": str(BUILD_RUNTIME) if not IS_FROZEN else None,
        "model_root": str(MODEL_ROOT),
        "model_source": resolve_model_source("small"),
        "device": device,
        "compute_type": compute_type,
        **details,
    }
    if load_model:
        from faster_whisper import WhisperModel

        WhisperModel(
            resolve_model_source("small"),
            device=device,
            compute_type=compute_type,
            download_root=str(MODEL_ROOT),
        )
        result["model_small_loaded"] = True
    return result


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="本地批量提取音视频文字，可按文件单独导出或合并成一份。")
    parser.add_argument("--input-folder", help="包含多个音视频的文件夹")
    parser.add_argument("--input-file", help="单个本地音视频文件")
    parser.add_argument("--task", choices=['transcribe', 'translate'], default='transcribe', help="原语言转写，或翻译成英文")
    parser.add_argument("--output-folder", help="输出目录；默认是所选素材文件夹本身")
    parser.add_argument("--model", default="small", choices=["small", "medium", "large-v3", "turbo"])
    parser.add_argument("--language", default="zh")
    parser.add_argument("--device", default="auto", choices=["auto", "cuda", "cpu"])
    parser.add_argument("--recursive", action="store_true", help="递归读取子文件夹")
    parser.add_argument("--no-srt", action="store_true")
    parser.add_argument("--no-txt", action="store_true")
    parser.add_argument("--srt", action="store_true", help="额外为每个文件导出SRT字幕")
    parser.add_argument("--txt", action="store_true", help="额外为每个文件导出TXT")
    parser.add_argument("--no-combined", action="store_true")
    parser.add_argument("--timed-txt", action="store_true", help="额外输出带时间码总稿")
    parser.add_argument("--chinese-script", choices=["simplified", "traditional", "preserve"], default="simplified", help="统一中文字形")
    parser.add_argument("--md", action="store_true", help="额外输出适合AI读取的分层Markdown")
    parser.add_argument("--json", action="store_true", help="额外输出供其他应用读取的结构化JSON")
    parser.add_argument("--output-format", choices=sorted(EXPORT_FORMATS), help="新导出方式：选择一种格式")
    parser.add_argument("--output-scope", choices=sorted(EXPORT_SCOPES), help="新导出方式：每个文件、合并稿或两者")
    parser.add_argument("--overwrite", action="store_true")
    parser.add_argument("--beam-size", type=int, default=5)
    parser.add_argument("--initial-prompt", default="", help="可填写人物名和专有词，帮助识别")
    parser.add_argument("--extract-audio-only", action="store_true", help="只从视频提取音频，不进行文字识别")
    parser.add_argument("--audio-format", choices=sorted(AUDIO_EXPORT_FORMATS), default="mp3", help="提取音频的格式")
    parser.add_argument("--self-test", action="store_true")
    parser.add_argument("--load-model", action="store_true", help="自检时实际加载已缓存的small模型")
    parser.add_argument("--gui", action="store_true", help="打开图形界面")
    return parser


def run_cli(args: argparse.Namespace) -> int:
    if args.self_test:
        print(json.dumps(self_test(args.load_model), ensure_ascii=False, indent=2))
        return 0
    if args.input_file:
        args.input_folder = str(Path(args.input_file).resolve().parent)
    if not args.input_folder:
        raise ValueError("命令行调用必须提供 --input-folder；不带参数运行会打开图形界面。")
    output = args.output_folder or str(Path(args.input_folder).resolve())
    if args.extract_audio_only:
        manifest = extract_audio_batch(
            args.input_folder,
            output,
            input_file=args.input_file or "",
            recursive=args.recursive,
            audio_format=args.audio_format,
            overwrite=args.overwrite,
            on_log=lambda message: print(message, flush=True),
            on_progress=lambda done, total, name: print(
                json.dumps({"event": "progress", "done": done, "total": total, "file": name}, ensure_ascii=False),
                flush=True,
            ),
        )
        print(json.dumps({"event": "complete", "manifest": manifest}, ensure_ascii=False))
        return 0
    options = Options(
        input_folder=args.input_folder,
        output_folder=output,
        model=args.model,
        language=args.language,
        device=args.device,
        recursive=args.recursive,
        write_srt=args.srt and not args.no_srt,
        write_txt=args.txt and not args.no_txt,
        write_combined=not args.no_combined,
        overwrite=args.overwrite,
        beam_size=args.beam_size,
        initial_prompt=args.initial_prompt,
        input_file=args.input_file or '',
        task=args.task,
        write_timed=args.timed_txt,
        chinese_script=args.chinese_script,
        write_md=args.md,
        write_json=args.json,
        output_format=args.output_format or "",
        output_scope=args.output_scope or "",
    )
    manifest = transcribe_folder(
        options,
        on_log=lambda message: print(message, flush=True),
        on_progress=lambda done, total, name: print(
            json.dumps({"event": "progress", "done": done, "total": total, "file": name}, ensure_ascii=False),
            flush=True,
        ),
    )
    print(json.dumps({"event": "complete", "manifest": manifest}, ensure_ascii=False))
    return 0


def launch_gui() -> None:
    import tkinter as tk
    from tkinter import filedialog, messagebox, ttk

    class App:
        def __init__(self, root: tk.Tk) -> None:
            self.root = root
            self.root.title(APP_NAME + '（界面预览）' if os.environ.get('LOCAL_TRANSCRIBER_UI_PREVIEW') == '1' else APP_NAME)
            asset_roots = (
                Path(getattr(sys, '_MEIPASS', APP_ROOT)) / 'assets',
                APP_ROOT / 'assets',
                Path(__file__).resolve().parent / 'assets',
            )
            icon_path = next((root / 'app.ico' for root in asset_roots if (root / 'app.ico').is_file()), None)
            if icon_path:
                try:
                    self.root.iconbitmap(str(icon_path))
                except tk.TclError:
                    pass
            logo_path = next((root / 'shengqi-logo-64.png' for root in asset_roots if (root / 'shengqi-logo-64.png').is_file()), None)
            self.brand_image = None
            if logo_path:
                try:
                    self.brand_image = tk.PhotoImage(file=str(logo_path)).subsample(2, 2)
                except tk.TclError:
                    self.brand_image = None
            screen_width = self.root.winfo_screenwidth()
            screen_height = self.root.winfo_screenheight()
            self.root.geometry(f"{min(1020, max(900, screen_width - 80))}x{min(760, max(600, screen_height - 100))}")
            self.root.minsize(900, 600)
            self.events: queue.Queue = queue.Queue()
            self.cancel_event = threading.Event()
            self.worker: threading.Thread | None = None
            self.close_after_worker = False

            self.input_var = tk.StringVar()
            self.mode_var = tk.StringVar(value='剧集转写')
            self.task_page_var = tk.StringVar(value='转成文字')
            self.language_var = tk.StringVar(value='自动识别（推荐）')
            self.task_var = tk.StringVar(value='保留原语言')
            self.script_var = tk.StringVar(value='简体中文（默认）')
            self.scripts = {'简体中文（默认）': 'simplified', '繁体中文': 'traditional', '保持识别结果': 'preserve'}
            self.languages = {
                '自动识别（推荐）': '', '中文': 'zh', '英语': 'en', '日语': 'ja', '韩语': 'ko',
                '法语': 'fr', '德语': 'de', '西班牙语': 'es', '俄语': 'ru', '葡萄牙语': 'pt',
                '阿拉伯语': 'ar', '印地语（印度）': 'hi', '印尼语': 'id', '意大利语': 'it',
                '泰语': 'th', '越南语': 'vi', '土耳其语': 'tr', '马来语': 'ms',
            }
            self.output_var = tk.StringVar()
            self.model_var = tk.StringVar(value="small")
            self.device_labels = {
                '自动选择（推荐）': 'auto',
                '使用处理器 CPU': 'cpu',
            }
            if sys.platform != 'darwin':
                self.device_labels['使用 NVIDIA 显卡'] = 'cuda'
            self.device_var = tk.StringVar(value='自动选择（推荐）')
            self.recursive_var = tk.BooleanVar(value=True)
            self.output_formats = {
                "Markdown 阅读稿（带时间码）": "md",
                "TXT 带时间码（方便定位）": "txt_timed",
                "TXT 纯文字（方便阅读）": "txt_plain",
                "SRT 字幕（原视频时间码）": "srt",
                "JSON 结构化数据": "json",
            }
            self.output_scopes = {
                "每个文件单独导出": "per_episode",
                "合并成一份（按文件分章节）": "combined",
                "每个文件＋合并稿": "both",
                "单个文件": "combined",
            }
            self.output_format_var = tk.StringVar(value="Markdown 阅读稿（带时间码）")
            self.output_scope_var = tk.StringVar(value="合并成一份（按文件分章节）")
            self.audio_formats = {
                "MP3（推荐，兼容且体积小）": "mp3",
                "M4A（体积小）": "m4a",
                "WAV（无压缩，体积大）": "wav",
                "FLAC（无损压缩）": "flac",
            }
            self.audio_format_var = tk.StringVar(value="MP3（推荐，兼容且体积小）")
            self.audio_help_var = tk.StringVar()
            self.format_help_var = tk.StringVar()
            self.advanced_open = False
            self.advanced_toggle_var = tk.StringVar(value='高级设置（一般不用改）  ▸')
            self.log_open = False
            self.log_toggle_var = tk.StringVar(value='查看详细日志  ▸')
            self.overwrite_var = tk.BooleanVar(value=False)
            self.status_var = tk.StringVar(value="文件夹批量模式：请选择包含待处理音视频的文件夹。")
            self.phase_var = tk.StringVar(value='准备就绪')
            self.progress_summary_var = tk.StringVar(value='尚未开始｜选择素材后会显示文件数量和总体进度')
            self.started_at: float | None = None
            self.progress_done = 0.0
            self.progress_total = 0
            self.last_result: str | None = None
            self.source_help_var = tk.StringVar()
            self.model_help_var = tk.StringVar()
            self.preview_var = tk.StringVar()
            self.source_summary_token = 0
            self.build_ui(ttk)
            for variable in (self.input_var, self.output_var, self.task_var, self.output_format_var, self.output_scope_var):
                variable.trace_add('write', lambda *_: self.refresh_output_preview())
            self.model_var.trace_add('write', lambda *_: self.refresh_model_help())
            self.task_var.trace_add('write', lambda *_: self.refresh_model_help())
            self.audio_format_var.trace_add('write', lambda *_: self.refresh_audio_help())
            self.refresh_mode_ui()
            self.set_task_page(self.task_page_var.get())
            self.refresh_audio_help()
            self.refresh_model_help()
            self.root.after(120, self.poll_events)
            self.root.after(1000, self.refresh_elapsed_clock)
            self.root.protocol('WM_DELETE_WINDOW', self.on_close)

        def build_ui(self, ttk_module) -> None:
            style = ttk_module.Style(self.root)
            style.theme_use('clam')
            self.root.configure(bg='#F7F4EF')
            self.root.option_add('*Font', ('Microsoft YaHei UI', 10))
            style.configure('TFrame', background='#F7F4EF')
            style.configure('TLabel', background='#F7F4EF', foreground='#302D2A')
            style.configure('TButton', padding=(14, 8))
            style.configure('Accent.TButton', background='#984B2F', foreground='white', font=('Microsoft YaHei UI', 10, 'bold'))
            style.map('Accent.TButton', background=[('active', '#783B29')])
            style.configure('Visible.Vertical.TScrollbar', background='#B8795F',
                            troughcolor='#E8DDD3', bordercolor='#E8DDD3',
                            arrowcolor='#FFFFFF', lightcolor='#B8795F',
                            darkcolor='#B8795F', arrowsize=18, width=16)
            style.map('Visible.Vertical.TScrollbar',
                      background=[('pressed', '#783B29'), ('active', '#984B2F')])
            style.configure('Horizontal.TProgressbar', background='#984B2F',
                            troughcolor='#E8DDD3', bordercolor='#E8DDD3')
            style.configure('Card.TLabelframe', background='white', padding=10, relief='solid', borderwidth=1)
            style.configure('Card.TLabelframe.Label', background='#F7F4EF', foreground='#8D422E', font=('Microsoft YaHei UI', 11, 'bold'))
            style.configure('Mode.TRadiobutton', background='white', foreground='#243047', padding=(10, 5), font=('Microsoft YaHei UI', 10, 'bold'))
            style.configure('Export.TCheckbutton', background='white', foreground='#243047', padding=(5, 4))
            outer = ttk_module.Frame(self.root, padding=(16, 10))
            outer.pack(fill='both', expand=True)
            outer.columnconfigure(0, weight=1)
            outer.rowconfigure(0, weight=1)
            body = ttk_module.Frame(outer)
            body.grid(row=0, column=0, sticky='nsew')
            self.form_canvas = tk.Canvas(body, background='#F7F4EF', highlightthickness=0)
            form_scroll = ttk_module.Scrollbar(body, orient='vertical',
                                               style='Visible.Vertical.TScrollbar',
                                               takefocus=True, command=self.form_canvas.yview)
            self.form_canvas.configure(yscrollcommand=form_scroll.set)
            self.form_canvas.pack(side='left', fill='both', expand=True)
            form_scroll.pack(side='right', fill='y')
            frame = ttk_module.Frame(self.form_canvas, padding=(4, 4, 8, 12))
            form_window = self.form_canvas.create_window((0, 0), window=frame, anchor='nw')
            frame.bind('<Configure>', lambda _event: self.form_canvas.configure(scrollregion=self.form_canvas.bbox('all')))
            self.form_canvas.bind('<Configure>', lambda event: self.form_canvas.itemconfigure(form_window, width=event.width))
            self.root.bind_all('<MouseWheel>', self.scroll_form)
            self.root.bind_all('<Button-4>', self.scroll_form)
            self.root.bind_all('<Button-5>', self.scroll_form)
            frame.columnconfigure(1, weight=1)
            if self.brand_image:
                ttk_module.Label(frame, image=self.brand_image).grid(row=0, column=0, rowspan=2, sticky='w', padx=(0, 12))
            ttk_module.Label(frame, text='声栖', font=('Microsoft YaHei UI', 24, 'bold'), foreground='#8D422E').grid(row=0, column=1 if self.brand_image else 0, columnspan=2 if self.brand_image else 3, sticky='w')
            ttk_module.Label(
                frame,
                text='本地音视频转文字与音频提取  ·  文件只在本机处理',
                foreground='#6D655D',
            ).grid(row=1, column=1 if self.brand_image else 0, columnspan=2 if self.brand_image else 3, sticky='w', pady=(3, 8))

            guide = ttk_module.LabelFrame(frame, text='快速开始', style='Card.TLabelframe')
            guide.grid(row=2, column=0, columnspan=3, sticky='ew', pady=(0, 8))
            guide.columnconfigure(0, weight=1)
            ttk_module.Label(
                guide,
                text='选文件或文件夹 → 确认保存位置 → 点击底部开始按钮',
                background='white',
                foreground='#243047',
                font=('Microsoft YaHei UI', 10, 'bold'),
            ).grid(row=0, column=0, sticky='w')
            ttk_module.Label(
                guide,
                text='支持剧集、直播录制、课程、会议、采访和下载视频。默认设置适合直接开始。',
                background='white',
                foreground='#6D655D',
            ).grid(row=1, column=0, sticky='w', pady=(5, 0))
            ttk_module.Button(guide, text='打开使用说明', command=self.open_help).grid(row=0, column=1, rowspan=2, padx=(16, 0))

            mode_card = ttk_module.LabelFrame(frame, text='1  选择素材', style='Card.TLabelframe')
            mode_card.grid(row=4, column=0, columnspan=3, sticky='ew')
            ttk_module.Label(mode_card, text='选择一个文件可处理单条素材；选择文件夹可处理整部剧或一批素材。', background='white', foreground='#625B54').pack(anchor='w', padx=8, pady=(0, 7))
            choose_row = ttk_module.Frame(mode_card)
            choose_row.pack(fill='x')
            ttk_module.Button(choose_row, text='选择视频 / 音频文件', command=self.choose_media_file, style='Accent.TButton').pack(side='left', padx=(0, 8))
            ttk_module.Button(choose_row, text='选择素材文件夹', command=self.choose_media_folder).pack(side='left')

            source_card = ttk_module.LabelFrame(frame, text='2  保存位置', style='Card.TLabelframe')
            source_card.grid(row=5, column=0, columnspan=3, sticky='ew', pady=8)
            source_card.columnconfigure(1, weight=1)
            ttk_module.Label(source_card, text='已选素材').grid(row=0, column=0, sticky='w')
            ttk_module.Entry(source_card, textvariable=self.input_var, state='readonly').grid(row=0, column=1, sticky='ew', padx=10)
            self.source_button = ttk_module.Label(source_card, text='尚未选择', foreground='#8D422E', background='white')
            self.source_button.grid(row=0, column=2, padx=10)
            ttk_module.Label(source_card, textvariable=self.source_help_var, foreground='#6D655D').grid(row=1, column=1, columnspan=2, sticky='w', padx=10, pady=(3, 8))
            ttk_module.Label(source_card, text='保存到').grid(row=2, column=0, sticky='w')
            ttk_module.Entry(source_card, textvariable=self.output_var).grid(row=2, column=1, sticky='ew', padx=10)
            ttk_module.Button(source_card, text='更改位置', command=self.choose_output).grid(row=2, column=2)
            self.recursive_check = ttk_module.Checkbutton(
                source_card, text='包含所选文件夹中的所有子文件夹（默认开启）',
                variable=self.recursive_var, command=self.refresh_source_summary,
            )
            self.recursive_check.grid(row=3, column=1, sticky='w', padx=10, pady=(6, 0))

            settings = ttk_module.LabelFrame(frame, text='3  转成文字', style='Card.TLabelframe')
            self.transcription_frame = settings
            settings.grid(row=6, column=0, columnspan=3, sticky='ew')
            settings.columnconfigure(1, weight=1)
            settings.columnconfigure(3, weight=1)
            ttk_module.Label(settings, text='输入语言').grid(row=0, column=0, sticky='w')
            ttk_module.Combobox(settings, textvariable=self.language_var, values=tuple(self.languages), state='readonly', width=17).grid(row=0, column=1, sticky='w', padx=(8, 25))
            ttk_module.Label(settings, text='输出语言').grid(row=0, column=2, sticky='w')
            ttk_module.Combobox(settings, textvariable=self.task_var, values=('保留原语言', '翻译成英文'), state='readonly', width=18).grid(row=0, column=3, sticky='w', padx=8)
            ttk_module.Label(
                settings,
                text='输入语言可自动识别；当前内置翻译只能转成英文，其他目标语言尚未接入。',
                foreground='#6D655D',
            ).grid(row=1, column=0, columnspan=4, sticky='w', pady=(7, 0))
            ttk_module.Label(settings, text='中文字形').grid(row=2, column=0, sticky='w', pady=(10, 0))
            ttk_module.Combobox(settings, textvariable=self.script_var, values=tuple(self.scripts), state='readonly', width=17).grid(row=2, column=1, sticky='w', padx=(8, 25), pady=(10, 0))
            ttk_module.Label(settings, text='简繁转换只统一字形，不改变语言。', foreground='#6D655D').grid(row=2, column=2, columnspan=2, sticky='w', pady=(10, 0))
            ttk_module.Separator(settings).grid(row=3, column=0, columnspan=4, sticky='ew', pady=10)
            ttk_module.Label(settings, text='输出格式').grid(row=4, column=0, sticky='w')
            self.output_format_box = ttk_module.Combobox(
                settings,
                textvariable=self.output_format_var,
                values=tuple(self.output_formats),
                state='readonly',
                width=27,
            )
            self.output_format_box.grid(row=4, column=1, sticky='w', padx=(8, 25))
            self.output_scope_label = ttk_module.Label(settings, text='输出范围')
            self.output_scope_label.grid(row=4, column=2, sticky='w')
            self.output_scope_box = ttk_module.Combobox(
                settings,
                textvariable=self.output_scope_var,
                values=('每个文件单独导出', '合并成一份（按文件分章节）', '每个文件＋合并稿'),
                state='readonly',
                width=24,
            )
            self.output_scope_box.grid(row=4, column=3, sticky='w', padx=8)
            ttk_module.Label(
                settings,
                textvariable=self.format_help_var,
                foreground='#6D655D',
            ).grid(row=5, column=0, columnspan=4, sticky='w', pady=(9, 0))
            ttk_module.Label(settings, textvariable=self.preview_var, foreground='#6D655D', justify='left', wraplength=850).grid(row=6, column=0, columnspan=4, sticky='w', pady=(10, 0))

            self.advanced_toggle_widget = ttk_module.Button(frame, textvariable=self.advanced_toggle_var, command=self.toggle_advanced)
            self.advanced_toggle_widget.grid(row=7, column=0, columnspan=3, sticky='w', pady=(8, 0))
            advanced = ttk_module.LabelFrame(frame, text='高级设置（一般不用改）', style='Card.TLabelframe')
            advanced.grid(row=8, column=0, columnspan=3, sticky='ew', pady=(4, 0))
            self.advanced_frame = advanced
            ttk_module.Label(advanced, text='识别模型').grid(row=0, column=0, sticky='w')
            ttk_module.Combobox(advanced, textvariable=self.model_var, values=('small', 'medium', 'large-v3', 'turbo'), state='readonly', width=14).grid(row=0, column=1, padx=(8, 22))
            ttk_module.Label(advanced, text='计算设备').grid(row=0, column=2, sticky='w')
            ttk_module.Combobox(advanced, textvariable=self.device_var, values=tuple(self.device_labels), state='readonly', width=20).grid(row=0, column=3, padx=8)
            ttk_module.Checkbutton(advanced, text='忽略缓存并重新识别', variable=self.overwrite_var).grid(row=1, column=2, columnspan=2, sticky='w', pady=(8, 0))
            device_help = ('自动选择会优先尝试 NVIDIA 显卡；CUDA 推理不可用时自动回退 CPU。手动选择 NVIDIA 需要本机有兼容的 CUDA 运行库。此项决定转文字由哪块硬件计算；只提取音频时不使用。' if sys.platform != 'darwin' else 'Mac 版用 CPU 进行转文字；只提取音频时不使用计算设备选项。')
            ttk_module.Label(advanced, text=device_help, foreground='#6D655D', wraplength=850).grid(row=2, column=0, columnspan=4, sticky='w', pady=(8, 0))
            ttk_module.Label(advanced, textvariable=self.model_help_var, foreground='#6D655D', wraplength=850).grid(row=3, column=0, columnspan=4, sticky='w', pady=(4, 0))
            ttk_module.Label(advanced, text='人名／专有词（可选）').grid(row=4, column=0, sticky='nw', pady=(10, 0))
            self.prompt_text = tk.Text(advanced, height=2, wrap='word', relief='solid', borderwidth=1, bg='#FBFCFF')
            self.prompt_text.grid(row=4, column=1, columnspan=3, sticky='ew', padx=(8, 0), pady=(10, 0))
            advanced.columnconfigure(3, weight=1)
            advanced.grid_remove()

            audio_card = ttk_module.LabelFrame(frame, text='3  提取音频（不识别文字）', style='Card.TLabelframe')
            self.audio_frame = audio_card
            audio_card.grid(row=9, column=0, columnspan=3, sticky='ew', pady=(10, 0))
            ttk_module.Label(audio_card, text='音频格式', background='white').grid(row=0, column=0, sticky='w')
            self.audio_format_box = ttk_module.Combobox(
                audio_card, textvariable=self.audio_format_var,
                values=tuple(self.audio_formats), state='readonly', width=27)
            self.audio_format_box.grid(row=0, column=1, padx=10, sticky='w')
            ttk_module.Label(audio_card, text='从视频中单独导出声音，不做语音识别。', background='white', foreground='#625B54').grid(row=0, column=2, sticky='w', padx=8)
            ttk_module.Label(audio_card, textvariable=self.audio_help_var,
                             background='white', foreground='#52617A', wraplength=760).grid(
                                 row=1, column=0, columnspan=3, sticky='w', pady=(6, 0))

            task_row = ttk_module.Frame(frame)
            task_row.grid(row=3, column=0, columnspan=3, sticky='ew', pady=(0, 8))
            self.transcribe_tab = ttk_module.Button(task_row, text='转成文字', command=lambda: self.set_task_page('转成文字'))
            self.transcribe_tab.pack(side='left', padx=(0, 8))
            self.audio_tab = ttk_module.Button(task_row, text='提取音频', command=lambda: self.set_task_page('提取音频'))
            self.audio_tab.pack(side='left')

            footer = ttk_module.Frame(outer)
            footer.grid(row=1, column=0, sticky='ew', pady=(8, 0))
            ttk_module.Separator(footer).pack(fill='x', pady=(0, 8))
            buttons = ttk_module.Frame(footer)
            buttons.pack(fill='x')
            self.start_button = ttk_module.Button(buttons, text='开始转成文字', style='Accent.TButton', command=self.start)
            self.start_button.pack(side='left')
            self.audio_button = self.start_button
            self.stop_button = ttk_module.Button(buttons, text='停止', command=self.stop, state='disabled')
            self.stop_button.pack(side='left', padx=6)
            ttk_module.Button(buttons, text='打开输出目录', command=self.open_output).pack(side='right')
            self.result_button = ttk_module.Button(buttons, text='打开主要结果', command=self.open_primary_result, state='disabled')
            self.result_button.pack(side='right', padx=(0, 8))
            self.progress = ttk_module.Progressbar(footer, mode='determinate')
            self.progress.pack(fill='x', pady=(5, 4))
            ttk_module.Label(footer, textvariable=self.phase_var, font=('Microsoft YaHei UI', 10, 'bold')).pack(anchor='w')
            ttk_module.Label(footer, textvariable=self.progress_summary_var, foreground='#8D422E').pack(anchor='w', pady=(2, 0))
            ttk_module.Label(footer, textvariable=self.status_var, wraplength=900).pack(anchor='w')
            ttk_module.Button(footer, textvariable=self.log_toggle_var, command=self.toggle_log).pack(anchor='w', pady=(3, 0))
            log_panel = ttk_module.Frame(footer)
            log_panel.pack(fill='x')
            self.log_panel = log_panel
            self.log = tk.Text(log_panel, height=4, state='disabled', wrap='word', bg='white', fg='#38445A', relief='solid', borderwidth=1, padx=10, pady=6)
            self.log.pack(fill='x')
            log_panel.pack_forget()
            self.audio_frame.grid_remove()
            self.task_row = task_row
            

        def refresh_mode_ui(self) -> None:
            series = self.mode_var.get() == '剧集转写'
            selected = self.input_var.get().strip()
            self.source_button.configure(text=('文件夹模式' if series else '单文件模式') if selected else '尚未选择')
            self.source_help_var.set('支持直播录像、课程、会议、采访和下载视频；选择文件夹时默认包含子文件夹。' if series else '支持直播录像、课程、会议、采访和下载视频，也可选择录音文件。')
            if series:
                self.recursive_check.grid()
            else:
                self.recursive_check.grid_remove()
            if series:
                self.output_scope_label.grid()
                self.output_scope_box.grid()
                self.output_scope_box.configure(
                    values=('每个文件单独导出', '合并成一份（按文件分章节）', '每个文件＋合并稿'),
                    state='readonly',
                )
                if self.output_scope_var.get() == '单个文件':
                    self.output_scope_var.set('合并成一份（按文件分章节）')
            else:
                self.output_scope_label.grid_remove()
                self.output_scope_box.grid_remove()
                self.output_scope_box.configure(values=('单个文件',), state='readonly')
                self.output_scope_var.set('单个文件')
            self.refresh_output_preview()

        def _refresh_task_tabs(self) -> None:
            selected = self.task_page_var.get()
            self.transcribe_tab.configure(style='Accent.TButton' if selected == '转成文字' else 'TButton')
            self.audio_tab.configure(style='Accent.TButton' if selected == '提取音频' else 'TButton')

        def set_task_page(self, task: str) -> None:
            if self.worker and self.worker.is_alive():
                return
            self.task_page_var.set(task)
            self._refresh_task_tabs()
            if task == '提取音频':
                self.transcription_frame.grid_remove()
                self.advanced_toggle_widget.grid_remove()
                self.advanced_frame.grid_remove()
                self.audio_frame.grid()
                self.start_button.configure(text='开始提取音频', command=self.start_audio_extraction)
            else:
                self.audio_frame.grid_remove()
                self.transcription_frame.grid()
                self.advanced_toggle_widget.grid()
                if self.advanced_open:
                    self.advanced_frame.grid()
                self.start_button.configure(text='开始转成文字', command=self.start)

        @staticmethod
        def readable_size(byte_count: int) -> str:
            value = float(max(0, byte_count))
            for unit in ('B', 'KB', 'MB', 'GB', 'TB'):
                if value < 1024 or unit == 'TB':
                    return f'{value:.0f} {unit}' if unit in {'B', 'KB'} else f'{value:.1f} {unit}'
                value /= 1024
            return f'{value:.1f} TB'

        def refresh_source_summary(self) -> None:
            selected = self.input_var.get().strip()
            series = self.mode_var.get() == '剧集转写'
            if not selected:
                self.source_help_var.set(
                    '支持直播录像、课程、会议、采访和下载视频；选择文件夹时默认包含子文件夹。'
                    if series else
                    '支持直播录像、课程、会议、采访、下载视频和录音文件。'
                )
                return
            if series:
                source = Path(selected)
                output_path = Path(self.output_var.get() or selected)
                recursive = self.recursive_var.get()
                self.source_summary_token += 1
                token = self.source_summary_token
                self.source_help_var.set('正在统计素材文件数量与大小……')

                def scan() -> None:
                    try:
                        files = discover_media(source, output_path, recursive)
                        total_size = sum(path.stat().st_size for path in files)
                        result = (len(files), total_size, None)
                    except (OSError, ValueError) as error:
                        result = (0, 0, str(error))
                    self.events.put(('source_summary', token, str(source), result))

                threading.Thread(target=scan, daemon=True).start()
                return
            try:
                source = Path(selected)
                self.source_help_var.set(
                    f'已选择：{source.name}｜大小约 {self.readable_size(source.stat().st_size)}'
                )
            except (OSError, ValueError) as error:
                self.source_help_var.set(f'暂时无法读取素材摘要：{error}')

        def refresh_model_help(self) -> None:
            model_name = self.model_var.get()
            local_source = resolve_model_source(model_name)
            availability = '已在本机找到，可直接使用。' if local_source != model_name else '本机未找到，首次使用需要联网下载。'
            if self.task_var.get() == '翻译成英文' and model_name == 'turbo':
                availability += ' turbo 不支持“翻译成英文”，请改用 small、medium 或 large-v3。'
            elif model_name == 'small':
                availability += ' 这是默认推荐：速度、准确率和资源占用较均衡。'
            elif model_name in {'medium', 'large-v3'}:
                availability += ' 准确率通常更高，但处理更慢、占用更多内存和磁盘。'
            else:
                availability += ' turbo 速度快，适合保留原语言的普通转写。'
            self.model_help_var.set(f'{model_name}：{availability}')

        def toggle_advanced(self) -> None:
            self.advanced_open = not self.advanced_open
            if self.advanced_open:
                self.advanced_frame.grid()
                self.advanced_toggle_var.set('高级设置（一般不用改）  ▾')
                self.root.after_idle(lambda: self.form_canvas.yview_moveto(1.0))
            else:
                self.advanced_frame.grid_remove()
                self.advanced_toggle_var.set('高级设置（一般不用改）  ▸')

        def toggle_log(self) -> None:
            self.log_open = not self.log_open
            if self.log_open:
                self.log_panel.pack(fill='x')
                self.log_toggle_var.set('收起详细日志  ▾')
            else:
                self.log_panel.pack_forget()
                self.log_toggle_var.set('查看详细日志  ▸')

        def scroll_form(self, event) -> None:
            if event.widget in (self.prompt_text, self.log):
                return
            pointer_x = self.root.winfo_pointerx() - self.form_canvas.winfo_rootx()
            pointer_y = self.root.winfo_pointery() - self.form_canvas.winfo_rooty()
            if not (0 <= pointer_x < self.form_canvas.winfo_width() and 0 <= pointer_y < self.form_canvas.winfo_height()):
                return
            if getattr(event, 'num', None) == 4:
                direction = -1
            elif getattr(event, 'num', None) == 5:
                direction = 1
            else:
                direction = -1 if event.delta > 0 else 1
            self.form_canvas.yview_scroll(direction, 'units')

        def refresh_audio_help(self) -> None:
            descriptions = {
                'mp3': 'MP3 兼容广、体积小，适合一般使用。',
                'm4a': 'M4A 体积小，常见设备可播放。',
                'wav': 'WAV 不压缩，文件较大，适合后期编辑。',
                'flac': 'FLAC 无损压缩，适合保存和后期编辑。',
            }
            selected = self.audio_formats[self.audio_format_var.get()]
            self.audio_help_var.set('从视频提取音频时才使用此格式，不影响转文字。' + descriptions[selected] + '转换格式不会提升原视频音质。')

        def refresh_output_preview(self) -> None:
            format_descriptions = {
                'md': 'Markdown：按文件分章并保留时间码，适合阅读，也适合交给其他工具分析。',
                'txt_timed': '带时间码 TXT：适合人工阅读并快速回到原视频定位。',
                'txt_plain': '纯文字 TXT：最简洁，适合通读和复制，不包含定位时间。',
                'srt': 'SRT：标准字幕文件，可导入常见剪辑软件；时间码对应各自原视频。',
                'json': 'JSON：供其他应用或自动化读取，不建议作为人工阅读稿。',
            }
            selected_format = self.output_formats[self.output_format_var.get()]
            self.format_help_var.set(format_descriptions[selected_format])
            source_text = self.input_var.get().strip()
            if not source_text:
                self.preview_var.set('选择素材后，这里会显示实际输出文件名。')
                return
            source = Path(source_text)
            series = self.mode_var.get() == '剧集转写'
            title = source.name if series else source.stem
            translated = self.task_var.get() == '翻译成英文'
            suffix = ('合并英文译稿' if translated else '合并文字稿') if series else ('英文译稿' if translated else '文字稿')
            output_format = self.output_formats[self.output_format_var.get()]
            output_scope = self.output_scopes[self.output_scope_var.get()]
            extension = export_extension(output_format)
            names: list[str] = []
            if series and output_scope in {'per_episode', 'both'}:
                names.append(f'逐个文件\\每个源文件名{extension}')
            if output_scope in {'combined', 'both'}:
                names.append(combined_export_path(Path('.'), title, suffix, output_format).name)
            self.preview_var.set('将生成：' + '；'.join(names))

        def choose_input(self) -> None:
            if self.worker and self.worker.is_alive():
                return
            if self.mode_var.get() == '普通音视频转写':
                chosen = filedialog.askopenfilename(title='选择直播录像、视频或录音', filetypes=[('音视频文件', ' '.join('*' + x for x in sorted(MEDIA_EXTENSIONS))), ('所有文件', '*.*')])
            else:
                chosen = filedialog.askdirectory(title="请选择素材文件夹（文件夹名将作为导出文件名前缀）")
            if chosen:
                self.input_var.set(chosen)
                self.output_var.set(str(Path(chosen).parent if self.mode_var.get() == '普通音视频转写' else Path(chosen)))
                self.refresh_source_summary()
                self.refresh_mode_ui()

        def choose_media_file(self) -> None:
            if self.worker and self.worker.is_alive():
                return
            chosen = filedialog.askopenfilename(
                title='选择要提取音轨的视频' if self.task_page_var.get() == '提取音频' else '选择视频或音频文件',
                filetypes=[
                    ('视频文件', ' '.join('*' + x for x in sorted(VIDEO_EXTENSIONS)))
                    if self.task_page_var.get() == '提取音频' else
                    ('音视频文件', ' '.join('*' + x for x in sorted(MEDIA_EXTENSIONS))),
                    ('所有文件', '*.*'),
                ],
            )
            if chosen:
                self.mode_var.set('普通音视频转写')
                self.input_var.set(chosen)
                self.output_var.set(str(Path(chosen).resolve().parent))
                self.refresh_mode_ui()
                self.refresh_source_summary()

        def choose_media_folder(self) -> None:
            if self.worker and self.worker.is_alive():
                return
            chosen = filedialog.askdirectory(title='选择要处理的素材文件夹')
            if chosen:
                self.mode_var.set('剧集转写')
                self.input_var.set(chosen)
                self.output_var.set(chosen)
                self.refresh_mode_ui()
                self.refresh_source_summary()

        def change_mode(self, event=None) -> None:
            if self.worker and self.worker.is_alive():
                return
            self.refresh_mode_ui()
            if self.input_var.get():
                self.refresh_source_summary()

        def choose_output(self) -> None:
            initial = self.output_var.get() or self.input_var.get()
            chosen = filedialog.askdirectory(title="选择转写结果保存位置", initialdir=initial or None)
            if chosen:
                self.output_var.set(chosen)

        def append_log(self, message: str) -> None:
            self.log.configure(state="normal")
            self.log.insert("end", message + "\n")
            self.log.see("end")
            self.log.configure(state="disabled")

        def start(self) -> None:
            if self.worker and self.worker.is_alive():
                return
            input_folder = self.input_var.get().strip()
            if not input_folder:
                messagebox.showwarning(APP_NAME, "请先选择一个音视频文件或素材文件夹。")
                return
            input_file = input_folder if self.mode_var.get() == '普通音视频转写' else ''
            if input_file:
                input_folder = str(Path(input_file).resolve().parent)
            output_folder = self.output_var.get().strip() or str(Path(input_folder))
            self.output_var.set(output_folder)
            options = Options(
                input_folder=input_folder,
                output_folder=output_folder,
                model=self.model_var.get(),
                language=self.languages[self.language_var.get()],
                input_file=input_file,
                task='translate' if self.task_var.get() == '翻译成英文' else 'transcribe',
                chinese_script=self.scripts[self.script_var.get()],
                device=self.device_labels[self.device_var.get()],
                recursive=self.recursive_var.get(),
                write_srt=False,
                write_txt=False,
                write_combined=False,
                write_timed=False,
                write_md=False,
                write_json=False,
                output_format=self.output_formats[self.output_format_var.get()],
                output_scope=self.output_scopes[self.output_scope_var.get()],
                overwrite=self.overwrite_var.get(),
                initial_prompt=self.prompt_text.get("1.0", "end").strip(),
            )
            self.cancel_event.clear()
            self.start_button.configure(state="disabled")
            self.audio_button.configure(state="disabled")
            self.audio_format_box.configure(state="disabled")
            self.stop_button.configure(state="normal")
            self.result_button.configure(state='disabled')
            self.last_result = None
            self.progress["value"] = 0
            self.progress_done = 0.0
            self.progress_total = 0
            self.started_at = time.monotonic()
            self.progress_summary_var.set('正在统计任务｜已用时 00:00')
            self.phase_var.set('正在扫描素材')
            self.status_var.set("正在查找可处理的音视频文件……")

            def work() -> None:
                try:
                    manifest = transcribe_folder(
                        options,
                        on_log=lambda msg: self.events.put(("log", msg)),
                        on_progress=lambda done, total, name: self.events.put(("progress", done, total, name)),
                        cancel_event=self.cancel_event,
                        on_segment_progress=lambda index, total, name, fraction: self.events.put(("detail", index, total, name, fraction)),
                    )
                    self.events.put(("done", manifest))
                except Exception as exc:
                    self.events.put(("error", f"{exc}\n\n{traceback.format_exc()}"))

            self.worker = threading.Thread(target=work, daemon=True)
            self.worker.start()

        def start_audio_extraction(self) -> None:
            if self.worker and self.worker.is_alive():
                return
            selected = self.input_var.get().strip()
            if not selected:
                messagebox.showwarning(APP_NAME, "请先选择一个视频文件或素材文件夹。")
                return
            input_file = selected if self.mode_var.get() == '普通音视频转写' else ''
            if input_file and Path(input_file).suffix.casefold() not in VIDEO_EXTENSIONS:
                messagebox.showwarning(APP_NAME, "当前选中的是音频文件。提取音频需要选择带有音轨的视频文件；如果只想转写，请切换到“转成文字”。")
                return
            input_folder = str(Path(selected).resolve().parent) if input_file else selected
            output_folder = self.output_var.get().strip() or input_folder
            self.output_var.set(output_folder)
            recursive = self.recursive_var.get()
            audio_format = self.audio_formats[self.audio_format_var.get()]
            overwrite = self.overwrite_var.get()
            self.cancel_event.clear()
            self.start_button.configure(state="disabled")
            self.audio_button.configure(state="disabled")
            self.audio_format_box.configure(state="disabled")
            self.stop_button.configure(state="normal")
            self.result_button.configure(state='disabled')
            self.last_result = None
            self.progress["value"] = 0
            self.progress_done = 0.0
            self.progress_total = 0
            self.started_at = time.monotonic()
            self.progress_summary_var.set('正在统计任务｜已用时 00:00')
            self.phase_var.set('正在扫描素材')
            self.status_var.set("正在查找可提取音频的视频文件……")

            def work() -> None:
                try:
                    manifest = extract_audio_batch(
                        input_folder,
                        output_folder,
                        input_file=input_file,
                        recursive=recursive,
                        audio_format=audio_format,
                        overwrite=overwrite,
                        on_log=lambda msg: self.events.put(("log", msg)),
                        on_progress=lambda done, total, name: self.events.put(("progress", done, total, name)),
                        on_file_progress=lambda index, total, name, fraction: self.events.put(("detail", index, total, name, fraction)),
                        cancel_event=self.cancel_event,
                    )
                    self.events.put(("audio_done", manifest))
                except Exception as exc:
                    self.events.put(("error", f"{exc}\n\n{traceback.format_exc()}"))

            self.worker = threading.Thread(target=work, daemon=True)
            self.worker.start()

        def stop(self) -> None:
            self.cancel_event.set()
            self.phase_var.set('正在停止')
            self.status_var.set("已请求停止，正在安全结束当前识别步骤……")
            self.stop_button.configure(state="disabled")

        def on_close(self) -> None:
            if self.worker and self.worker.is_alive():
                should_stop = messagebox.askyesno(
                    APP_NAME,
                    '任务仍在运行。现在请求停止并在当前处理步骤结束后退出吗？\n\n已经完成的文件会保留。',
                )
                if not should_stop:
                    return
                self.close_after_worker = True
                self.cancel_event.set()
                self.phase_var.set('正在安全停止')
                self.status_var.set('已请求停止；当前文件处理结束后将自动退出并保留已完成结果。')
                self.stop_button.configure(state='disabled')
                return
            self.root.destroy()

        def close_after_task_if_requested(self) -> bool:
            if self.close_after_worker:
                self.root.after_idle(self.root.destroy)
                return True
            return False

        def update_phase_from_log(self, message: str) -> None:
            if message.startswith('发现 ') and '个' in message:
                self.status_var.set(message)
            elif message.startswith('正在加载模型'):
                self.phase_var.set('正在加载语音模型')
                self.status_var.set('首次加载或下载模型可能需要一些时间；详细信息可展开日志查看。')
            elif message.startswith('模型加载完成'):
                self.phase_var.set('正在转写文字')
                self.status_var.set('模型已就绪，准备处理第一个文件……')
            elif '正在转写：' in message:
                self.phase_var.set('正在转写文字')
                self.status_var.set(message)
            elif '正在提取音频：' in message:
                self.phase_var.set('正在提取音频')
                self.status_var.set(message)
            elif message.startswith('本批次完成：'):
                self.phase_var.set('正在整理并保存结果')
                self.status_var.set(message)

        @staticmethod
        def format_elapsed(seconds: float) -> str:
            total = max(0, int(seconds))
            hours, remainder = divmod(total, 3600)
            minutes, seconds = divmod(remainder, 60)
            return f'{hours:02d}:{minutes:02d}:{seconds:02d}' if hours else f'{minutes:02d}:{seconds:02d}'

        def update_progress_summary(self) -> None:
            if self.started_at is None:
                return
            elapsed = self.format_elapsed(time.monotonic() - self.started_at)
            if self.progress_total > 0:
                fraction = min(max(self.progress_done / self.progress_total, 0.0), 1.0)
                self.progress_summary_var.set(
                    f'总体进度 {fraction:.0%}｜任务 {self.progress_done:.1f}/{self.progress_total:g}｜已用时 {elapsed}'
                )
            else:
                self.progress_summary_var.set(f'正在统计任务｜已用时 {elapsed}')

        def refresh_elapsed_clock(self) -> None:
            if self.started_at is not None:
                self.update_progress_summary()
            self.root.after(1000, self.refresh_elapsed_clock)

        def poll_events(self) -> None:
            try:
                while True:
                    event = self.events.get_nowait()
                    if event[0] == 'source_summary':
                        _, token, selected, result = event
                        if token != self.source_summary_token or selected != self.input_var.get().strip():
                            continue
                        count, total_size, error = result
                        if error:
                            self.source_help_var.set(f'暂时无法统计素材：{error}')
                        else:
                            self.source_help_var.set(
                                f'已找到 {count} 个音视频｜总大小约 {self.readable_size(total_size)}｜文件夹名将作为导出文件名称。'
                            )
                    elif event[0] == "log":
                        self.append_log(event[1])
                        self.update_phase_from_log(event[1])
                    elif event[0] == "progress":
                        _, done, total, name = event
                        self.progress["maximum"] = max(total, 1)
                        self.progress["value"] = done
                        self.progress_done = float(done)
                        self.progress_total = int(total)
                        self.update_progress_summary()
                        self.status_var.set(f"已完成 {done}/{total} 个文件｜{name}")
                    elif event[0] == "detail":
                        _, index, total, name, fraction = event
                        self.progress["maximum"] = max(total, 1)
                        self.progress["value"] = index - 1 + fraction
                        self.progress_done = index - 1 + fraction
                        self.progress_total = int(total)
                        self.update_progress_summary()
                        self.status_var.set(f"第 {index}/{total} 个文件｜当前约 {fraction:.0%}｜{name}")
                    elif event[0] == "done":
                        manifest = event[1]
                        self.start_button.configure(state="normal")
                        self.audio_button.configure(state="normal")
                        self.audio_format_box.configure(state="readonly")
                        self.stop_button.configure(state="disabled")
                        self.phase_var.set('已停止' if manifest['cancelled'] else '转写完成')
                        elapsed = self.format_elapsed(time.monotonic() - self.started_at) if self.started_at is not None else '--:--'
                        self.started_at = None
                        self.progress_done = float(manifest['completed'])
                        self.progress_total = int(manifest['total_discovered'])
                        self.progress_summary_var.set(
                            f"完成 {manifest['completed']}/{manifest['total_discovered']} 个文件｜总用时 {elapsed}"
                        )
                        self.status_var.set(f"结果已保存到：{manifest['options']['output_folder']}")
                        result_candidates = [
                            manifest.get('full_export'),
                            manifest.get('combined_txt'),
                            manifest.get('timed_txt'),
                            manifest.get('markdown'),
                            manifest.get('structured_json'),
                        ]
                        result_candidates.extend(
                            item.get('export') or item.get('srt') or item.get('txt')
                            for item in manifest.get('results', [])
                        )
                        self.last_result = next(
                            (str(path) for path in result_candidates if path and Path(path).exists()),
                            manifest['options']['output_folder'],
                        )
                        self.result_button.configure(state='normal')
                        if self.close_after_task_if_requested():
                            return
                        messagebox.showinfo(
                            APP_NAME,
                            f"{'已停止' if manifest['cancelled'] else '转写完成'}："
                            f"{manifest['completed']}/{manifest['total_discovered']} 个文件。\n"
                            f"总用时：{elapsed}\n\n输出位置：\n{manifest['options']['output_folder']}",
                        )
                    elif event[0] == "audio_done":
                        manifest = event[1]
                        self.start_button.configure(state="normal")
                        self.audio_button.configure(state="normal")
                        self.audio_format_box.configure(state="readonly")
                        self.stop_button.configure(state="disabled")
                        self.phase_var.set('已停止' if manifest['cancelled'] else '音频提取完成')
                        elapsed = self.format_elapsed(time.monotonic() - self.started_at) if self.started_at is not None else '--:--'
                        self.started_at = None
                        self.progress_done = float(manifest['available'])
                        self.progress_total = int(manifest['total_discovered'])
                        self.progress_summary_var.set(
                            f"音频可用 {manifest['available']}/{manifest['total_discovered']} 个｜失败 {manifest['failed']} 个｜总用时 {elapsed}"
                        )
                        self.last_result = manifest['output_folder']
                        self.result_button.configure(state='normal')
                        self.status_var.set(
                            f"结果已保存到：{manifest['output_folder']}"
                        )
                        if self.close_after_task_if_requested():
                            return
                        messagebox.showinfo(
                            APP_NAME,
                            f"{'已停止' if manifest['cancelled'] else '音频提取完成'}："
                            f"可用 {manifest['available']}/{manifest['total_discovered']} 个，失败 {manifest['failed']} 个。\n"
                            f"总用时：{elapsed}\n\n输出位置：\n{manifest['output_folder']}",
                        )
                    elif event[0] == "error":
                        self.start_button.configure(state="normal")
                        self.audio_button.configure(state="normal")
                        self.audio_format_box.configure(state="readonly")
                        self.stop_button.configure(state="disabled")
                        self.append_log(event[1])
                        if not self.log_open:
                            self.toggle_log()
                        self.phase_var.set('运行失败')
                        elapsed = self.format_elapsed(time.monotonic() - self.started_at) if self.started_at is not None else '--:--'
                        self.started_at = None
                        self.progress_summary_var.set(f'任务未完成｜已运行 {elapsed}｜错误详情已展开')
                        self.last_result = None
                        self.result_button.configure(state='disabled')
                        self.status_var.set("运行失败，请查看日志。")
                        if self.close_after_task_if_requested():
                            return
                        messagebox.showerror(APP_NAME, event[1].split("\n", 1)[0])
            except queue.Empty:
                pass
            self.root.after(120, self.poll_events)

        def open_output(self) -> None:
            path = Path(self.output_var.get().strip()) if self.output_var.get().strip() else None
            if not path or not path.exists():
                messagebox.showwarning(APP_NAME, "输出目录尚不存在。")
                return
            if sys.platform == "darwin":
                subprocess.run(["open", str(path)], check=False)
            elif os.name == "nt":
                os.startfile(path)
            else:
                subprocess.run(["xdg-open", str(path)], check=False)

        def open_primary_result(self) -> None:
            if not self.last_result:
                messagebox.showwarning(APP_NAME, '当前还没有可打开的结果。')
                return
            path = Path(self.last_result)
            if not path.exists():
                messagebox.showwarning(APP_NAME, f'主要结果已不存在：\n{path}')
                self.result_button.configure(state='disabled')
                return
            if sys.platform == 'darwin':
                subprocess.run(['open', str(path)], check=False)
            elif os.name == 'nt':
                os.startfile(path)
            else:
                subprocess.run(['xdg-open', str(path)], check=False)

        def open_help(self) -> None:
            roots = (APP_ROOT, Path(__file__).resolve().parent)
            candidates = tuple(root / name for name in ('声栖_新包使用说明.md', '使用说明.txt') for root in roots)
            help_path = next((path for path in candidates if path.exists()), None)
            if help_path is None:
                messagebox.showinfo(
                    APP_NAME,
                    '使用顺序：选择素材 → 保持默认或选择导出格式 → 开始转成文字 → 打开输出目录。',
                )
                return
            if sys.platform == 'darwin':
                subprocess.run(['open', str(help_path)], check=False)
            elif os.name == 'nt':
                # Markdown file associations are not guaranteed on a clean PC.
                subprocess.Popen(['notepad.exe', str(help_path)])
            else:
                subprocess.run(['xdg-open', str(help_path)], check=False)

    root = tk.Tk()
    App(root)
    if os.environ.get('LOCAL_TRANSCRIBER_GUI_SMOKE_TEST') == '1':
        root.update_idletasks()
        root.destroy()
        return
    root.mainloop()


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    if args.gui or len(sys.argv) == 1:
        launch_gui()
        return 0
    return run_cli(args)


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as error:
        print(json.dumps({"status": "error", "error": str(error)}, ensure_ascii=False), file=sys.stderr)
        raise
