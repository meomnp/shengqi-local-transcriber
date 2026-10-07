import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from types import SimpleNamespace
import sys
from local_transcriber import transcribe_folder
from local_transcriber import discover_media, discover_video_files, extract_audio_batch, extract_audio_track, episode_label, episode_timeline, write_combined, write_duration_report, write_markdown, write_transcript_json, Options, convert_chinese_script, resolve_export_plan, write_full_export


class OutputRules(unittest.TestCase):
    def test_auto_device_retries_current_file_on_cpu_after_cuda_inference_failure(self):
        created_devices = []

        class Model:
            def __init__(self, _source, device, **_kwargs):
                created_devices.append(device)

            def transcribe(self, _source, **_kwargs):
                if created_devices[-1] == 'cuda':
                    def fail_during_lazy_inference():
                        raise RuntimeError('Library cublas64_12.dll is not found')
                        yield None
                    return fail_during_lazy_inference(), SimpleNamespace(
                        language='en', language_probability=1, duration=2
                    )
                return iter([SimpleNamespace(start=0, end=1, text='CPU retry passed')]), SimpleNamespace(
                    language='en', language_probability=1, duration=2
                )

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / 'synthetic.mp4'
            source.write_bytes(b'synthetic placeholder; fake model does not decode it')
            output = root / 'results'
            options = Options(
                str(root), str(output), input_file=str(source), language='en',
                device='auto', output_format='md', output_scope='combined',
            )
            messages = []
            devices = [
                ('cuda', 'int8_float16', {'cuda_device_count': 1}),
                ('cpu', 'int8', {'cuda_device_count': 1}),
            ]
            with patch('local_transcriber.choose_device', side_effect=devices), \
                    patch.dict(sys.modules, {'faster_whisper': SimpleNamespace(WhisperModel=Model)}):
                result = transcribe_folder(options, on_log=messages.append)

            self.assertEqual(created_devices, ['cuda', 'cpu'])
            self.assertEqual(result['device'], 'cpu')
            self.assertIn('cublas64_12.dll', result['device_details']['auto_fallback_reason'])
            self.assertIn('自动模式改用 CPU/int8', '\n'.join(messages))
            exported = Path(result['full_export'])
            self.assertIn('CPU retry passed', exported.read_text(encoding='utf-8-sig'))

    def test_explicit_cuda_failure_is_not_silently_switched_to_cpu(self):
        created_devices = []

        class Model:
            def __init__(self, _source, device, **_kwargs):
                created_devices.append(device)

            def transcribe(self, _source, **_kwargs):
                def fail_during_lazy_inference():
                    raise RuntimeError('Library cublas64_12.dll is not found')
                    yield None
                return fail_during_lazy_inference(), SimpleNamespace(
                    language='en', language_probability=1, duration=2
                )

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / 'synthetic.mp4'
            source.write_bytes(b'synthetic placeholder; fake model does not decode it')
            options = Options(
                str(root), str(root / 'results'), input_file=str(source), language='en',
                device='cuda', output_format='md', output_scope='combined',
            )
            with patch('local_transcriber.choose_device', return_value=('cuda', 'int8_float16', {})), \
                    patch.dict(sys.modules, {'faster_whisper': SimpleNamespace(WhisperModel=Model)}):
                with self.assertRaisesRegex(RuntimeError, 'cublas64_12.dll'):
                    transcribe_folder(options, on_log=lambda _: None)
            self.assertEqual(created_devices, ['cuda'])

    def test_structured_json_is_application_facing(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            output = root / '全剧.json'
            write_transcript_json(output, root, [{'order': 2, 'relative_source': '第2集.mp4', 'segments': [{'start': 3.125, 'end': 4.5, 'text': '下一句'}]}], title='测试剧')
            import json
            payload = json.loads(output.read_text(encoding='utf-8'))
            self.assertEqual(payload['schema_version'], '1.0')
            self.assertEqual(payload['title'], '测试剧')
            self.assertEqual(payload['episodes'][0]['label'], '第2集')
            self.assertEqual(payload['episodes'][0]['segments'][0]['start_seconds'], 3.125)
            self.assertEqual(payload['episodes'][0]['segments'][0]['start'], '00:00:03.125')
            self.assertEqual(payload['episodes'][0]['segments'][0]['text'], '下一句')

    def test_markdown_has_drama_episode_source_and_timestamps(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            output = root / '全剧.md'
            write_markdown(output, root, [{'order': 2, 'relative_source': '第2集.mp4', 'segments': [{'start': 3, 'end': 4, 'text': '下一句'}]}], title='测试剧')
            text = output.read_text(encoding='utf-8-sig')
            self.assertIn('# 《测试剧》台词稿', text)
            self.assertIn('## 第2集', text)
            self.assertIn('源文件：`第2集.mp4`', text)
            self.assertIn('**00:00:03.000–00:00:04.000** 下一句', text)

    def test_chinese_script_is_explicit(self):
        source = [{'start': 0, 'end': 1, 'text': '軟體裡的影片'}]
        self.assertEqual(convert_chinese_script(source, 'simplified')[0]['text'], '软体里的影片')
        self.assertEqual(convert_chinese_script([{'start': 0, 'end': 1, 'text': '软件里的视频'}], 'traditional')[0]['text'], '軟件裏的視頻')
        self.assertEqual(convert_chinese_script(source, 'preserve')[0]['text'], '軟體裡的影片')

    def test_single_file_language_and_exports(self):
        calls = []
        segment_progress = []
        class Model:
            def __init__(self, *args, **kwargs):
                pass
            def transcribe(self, source, **kwargs):
                calls.append((source, kwargs))
                return iter([SimpleNamespace(start=1, end=2, text='hello')]), SimpleNamespace(language='en', language_probability=1, duration=2)
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / '直播.mp4'
            source.touch()
            (root / '其他.mp4').touch()
            options = Options(str(root), str(root), input_file=str(source), language='', write_timed=True, write_srt=True)
            with patch('local_transcriber.choose_device', return_value=('cpu', 'int8', {})), patch.dict(sys.modules, {'faster_whisper': SimpleNamespace(WhisperModel=Model)}):
                result = transcribe_folder(
                    options,
                    on_log=lambda _: None,
                    on_segment_progress=lambda index, total, name, fraction: segment_progress.append((index, total, name, fraction)),
                )
            self.assertEqual(len(calls), 1)
            self.assertEqual(segment_progress[0][:3], (1, 1, '直播.mp4'))
            self.assertAlmostEqual(segment_progress[0][3], 0.99)
            self.assertIsNone(calls[0][1]['language'])
            self.assertEqual(calls[0][1]['task'], 'transcribe')
            self.assertNotIn('[00:00:01.000', Path(result['combined_txt']).read_text(encoding='utf-8-sig'))
            self.assertIn('[00:00:01.000', Path(result['timed_txt']).read_text(encoding='utf-8-sig'))
            self.assertIn('00:00:01,000 --> 00:00:02,000', Path(result['results'][0]['srt']).read_text(encoding='utf-8-sig'))

    def test_same_folder_and_numeric_order(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            for name in ('10.mp4', '2.mp4', '1.mp4', '剧_全集台词.txt'):
                (root / name).touch()
            self.assertEqual([p.name for p in discover_media(root, root, False)], ['1.mp4', '2.mp4', '10.mp4'])

    def test_recursive_discovery_ignores_own_outputs(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / '第1集.mp4').touch()
            (root / '子目录').mkdir()
            (root / '子目录' / '第2集.mp4').touch()
            (root / '提取音频').mkdir()
            (root / '提取音频' / '第1集.m4a').touch()
            (root / '切片工程').mkdir()
            (root / '切片工程' / '成片.mp4').touch()
            self.assertEqual(len(discover_media(root, root, True)), 2)
            self.assertEqual(len(discover_video_files(root, True)), 2)

    def test_audio_extraction_batch_uses_video_files_and_audio_subfolder(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            for name in ('2.mp4', '1.mkv', '已有.mp3', '说明.txt'):
                (root / name).touch()
            self.assertEqual([path.name for path in discover_video_files(root, False)], ['1.mkv', '2.mp4'])

            def fake_extract(source, destination, audio_format, cancel_event):
                destination.parent.mkdir(parents=True, exist_ok=True)
                destination.write_bytes(b'audio')

            with patch('local_transcriber.extract_audio_track', side_effect=fake_extract):
                manifest = extract_audio_batch(str(root), str(root), audio_format='mp3', on_log=lambda _: None)
            self.assertEqual(manifest['completed'], 2)
            self.assertEqual(manifest['failed'], 0)
            self.assertTrue((root / '提取音频' / '1.mp3').is_file())
            self.assertTrue((root / '提取音频' / '2.mp3').is_file())

    def test_audio_extraction_encodes_all_supported_formats(self):
        import av
        import numpy as np

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / '测试视频.mp4'
            with av.open(str(source), mode='w', format='mp4') as container:
                stream = container.add_stream('aac', rate=48_000)
                stream.layout = 'stereo'
                pts = 0
                for _ in range(4):
                    samples = np.zeros((2, 1024), dtype=np.float32)
                    frame = av.AudioFrame.from_ndarray(samples, format='fltp', layout='stereo')
                    frame.sample_rate = 48_000
                    frame.pts = pts
                    pts += 1024
                    for packet in stream.encode(frame):
                        container.mux(packet)
                for packet in stream.encode(None):
                    container.mux(packet)

            for audio_format in ('mp3', 'm4a', 'wav', 'flac'):
                output = root / f'测试音频.{audio_format}'
                fractions = []
                extract_audio_track(source, output, audio_format, on_frame_progress=fractions.append)
                self.assertGreater(output.stat().st_size, 0)
                self.assertTrue(fractions)
                self.assertTrue(all(0 <= fraction <= 0.99 for fraction in fractions))
                with av.open(str(output), mode='r') as extracted:
                    self.assertTrue(any(stream.type == 'audio' for stream in extracted.streams))

    def test_labels(self):
        for source, expected in [('第02集.mp4', '第02集'), ('EP03.mp4', '第3集'), ('1.mp4', '第1集'), ('预告.mp4', '第1个文件')]:
            self.assertEqual(episode_label(source, 1), expected)

    def test_combined_retains_source_and_timestamp(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            output = root / '总稿.txt'
            write_combined(output, root, [{'order': 1, 'relative_source': '第1集.mp4', 'segments': [{'start': 1, 'end': 2, 'text': '测试台词'}]}])
            text = output.read_text(encoding='utf-8-sig')
            self.assertIn('第1集｜第1集.mp4', text)
            self.assertIn('00:00:01.000', text)
            self.assertIn('测试台词', text)
            self.assertFalse(Options(str(root), str(root)).write_txt)
            self.assertFalse(Options(str(root), str(root)).write_srt)
            write_combined(output, root, [{'order': 1, 'relative_source': '直播.mp4', 'segments': [{'start': 1, 'end': 2, 'text': '测试台词'}]}], title='直播', single=True, timed=False)
            text = output.read_text(encoding='utf-8-sig')
            self.assertNotIn('[00:00:01.000', text)
            self.assertIn('来源文件｜直播.mp4', text)

    def test_episode_duration_report_and_master_offsets(self):
        episodes = [
            {'order': 1, 'relative_source': '第1集.mp4', 'duration_seconds': 120, 'duration_source': 'media_container', 'segments': []},
            {'order': 2, 'relative_source': '第2集.mp4', 'duration_seconds': 110, 'duration_source': 'media_container', 'segments': []},
        ]
        rows, total = episode_timeline(episodes)
        self.assertEqual(rows[1]['master_start'], '00:02:00.000')
        self.assertEqual(rows[1]['master_end'], '00:03:50.000')
        self.assertEqual(total, 230)
        with tempfile.TemporaryDirectory() as tmp:
            output = Path(tmp) / '时长表.md'
            write_duration_report(output, episodes, '测试剧')
            text = output.read_text(encoding='utf-8-sig')
            self.assertIn('第1集.mp4', text)
            self.assertIn('00:02:00.000', text)
            self.assertIn('未删减自然顺序母版总时长：**00:03:50.000**', text)

    def test_two_dropdown_plan(self):
        plan = resolve_export_plan(
            Options('in', 'out', output_format='md', output_scope='both')
        )
        self.assertTrue(plan['modern'])
        self.assertTrue(plan['per_episode'])
        self.assertTrue(plan['combined'])
        self.assertEqual(plan['format'], 'md')

    def test_full_transcript_keeps_episode_local_time_and_reports_real_duration(self):
        episodes = [
            {
                'order': 1,
                'relative_source': '第1集.mp4',
                'duration_seconds': 130,
                'duration_source': 'media_container',
                'segments': [{'start': 10, 'end': 12, 'text': '第一句'}],
            },
            {
                'order': 2,
                'relative_source': '第2集.mp4',
                'duration_seconds': 110,
                'duration_source': 'media_container',
                'segments': [{'start': 5, 'end': 7, 'text': '第二句'}],
            },
        ]
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            output = root / '测试剧_全集台词.md'
            write_full_export('md', output, root, episodes, '测试剧')
            text = output.read_text(encoding='utf-8-sig')
            self.assertIn('音视频数量：**2**', text)
            self.assertIn('全部音视频总时长：**00:04:00.000**', text)
            self.assertIn('完整时长：`00:02:10.000`', text)
            self.assertIn('**00:00:05.000–00:00:07.000** 第二句', text)
            self.assertNotIn('00:02:15.000', text)


if __name__ == '__main__':
    unittest.main()
