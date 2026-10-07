import json
import tempfile
import unittest
from pathlib import Path

from local_transcriber import clear_recent_tasks, load_recent_tasks, record_recent_task


class RecentTaskHistoryTests(unittest.TestCase):
    def test_keeps_latest_100_and_only_metadata(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / 'recent_tasks.json'
            for number in range(105):
                record_recent_task({
                    'created_at': f'2026-10-08T00:{number:03d}:00+08:00',
                    'task': '转成文字',
                    'source_name': f'第{number}.mp4',
                    'source_path': f'C:/input/{number}.mp4',
                    'output_path': f'C:/output/{number}',
                    'status': '完成',
                    'completed': 1,
                    'total': 1,
                    'transcript': 'this must not be persisted',
                }, path)

            records = load_recent_tasks(path)
            saved = json.loads(path.read_text(encoding='utf-8'))
            self.assertEqual(len(records), 100)
            self.assertEqual(records[0]['source_name'], '第104.mp4')
            self.assertEqual(records[-1]['source_name'], '第5.mp4')
            self.assertNotIn('transcript', saved['tasks'][0])

    def test_clear_removes_only_history_index(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            history = root / 'recent_tasks.json'
            export = root / 'transcript.md'
            export.write_text('keep this result', encoding='utf-8')
            record_recent_task({'created_at': 'now', 'output_path': str(root)}, history)

            clear_recent_tasks(history)

            self.assertFalse(history.exists())
            self.assertEqual(export.read_text(encoding='utf-8'), 'keep this result')

    def test_invalid_history_is_ignored(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / 'recent_tasks.json'
            path.write_text('{broken', encoding='utf-8')
            self.assertEqual(load_recent_tasks(path), [])


if __name__ == '__main__':
    unittest.main()
