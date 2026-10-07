"""Opt-in packaged CLI integration test using generated media, never user footage."""
import argparse
import json
import os
from pathlib import Path
import subprocess
import tempfile

import av
import numpy as np


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--exe', required=True)
    args = parser.parse_args()
    exe = Path(args.exe).resolve(strict=True)
    with tempfile.TemporaryDirectory(prefix='shengqi-package-test-') as folder:
        root = Path(folder)
        source = root / 'synthetic.mkv'
        # A generated audio track in a supported video container exercises decoding
        # and transcoding without reading or uploading any real user material.
        with av.open(str(source), 'w') as container:
            stream = container.add_stream('pcm_s16le', rate=16000)
            stream.layout = 'mono'
            samples = (np.sin(np.arange(32000) * (2 * np.pi * 440 / 16000)) * 4000).astype(np.int16)
            frame = av.AudioFrame.from_ndarray(samples.reshape(1, -1), format='s16', layout='mono')
            frame.sample_rate = 16000
            for packet in stream.encode(frame):
                container.mux(packet)
            for packet in stream.encode(None):
                container.mux(packet)
        env = os.environ.copy()
        for key in ('PYTHONPATH', 'PYTHONHOME', 'LOCAL_TRANSCRIBER_BUILD_RUNTIME'):
            env.pop(key, None)
        env.update(HF_HUB_OFFLINE='1', TRANSFORMERS_OFFLINE='1')
        results = []
        for fmt in ('mp3', 'm4a', 'wav', 'flac'):
            output = root / fmt
            command = [str(exe), '--input-file', str(source), '--output-folder', str(output),
                       '--extract-audio-only', '--audio-format', fmt]
            run = subprocess.run(command, env=env, capture_output=True, timeout=90)
            if run.returncode:
                raise RuntimeError(f'{fmt}: {run.stdout!r} {run.stderr!r}')
            exported = output / '提取音频' / f'synthetic.{fmt}'
            with av.open(str(exported)) as media:
                frames = list(media.decode(audio=0))
                seconds = sum(f.samples / f.sample_rate for f in frames)
            assert 1.8 < seconds < 2.3, (fmt, seconds)
            before = exported.read_bytes()
            second = subprocess.run(command, env=env, capture_output=True, timeout=90)
            assert second.returncode == 0
            assert exported.read_bytes() == before, 'Existing output unexpectedly changed'
            results.append({'format': fmt, 'seconds': round(seconds, 3),
                            'bytes': len(before), 'repeat_preserved_output': True})
        print(json.dumps({'passed': True, 'exe': str(exe), 'synthetic_only': True,
                          'developer_pythonpath_removed': True, 'results': results}, indent=2))


if __name__ == '__main__':
    main()
