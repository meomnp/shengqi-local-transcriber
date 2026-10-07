import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import export_source as exporter


class SourceExportTests(unittest.TestCase):
    def test_only_explicit_files_and_refuses_overwrite(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / 'source'
            root.mkdir()
            (root / 'app.py').write_text('print(1)', encoding='utf-8')
            (root / '.env').write_text('PRIVATE_TEST_DATA', encoding='utf-8')
            (root / 'models').mkdir()
            (root / 'models' / 'private.txt').write_text('not for export', encoding='utf-8')
            dest = Path(tmp) / 'review'
            with patch.object(exporter, 'SOURCE_FILES', ('app.py',)):
                result = exporter.export_source(root, dest)
                self.assertEqual([f['path'] for f in result], ['app.py'])
                self.assertEqual(sorted(p.name for p in dest.iterdir()),
                                 ['SOURCE_REVIEW_MANIFEST.json', 'app.py'])
                with self.assertRaises(FileExistsError):
                    exporter.export_source(root, dest)

    def test_missing_input_does_not_create_output(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            dest = root / 'review'
            with patch.object(exporter, 'SOURCE_FILES', ('missing.py',)):
                with self.assertRaises(FileNotFoundError):
                    exporter.export_source(root, dest)
            self.assertFalse(dest.exists())

    def test_escape_is_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / 'source'
            root.mkdir()
            (root.parent / 'outside.txt').write_text('private', encoding='utf-8')
            with patch.object(exporter, 'SOURCE_FILES', ('../outside.txt',)):
                with self.assertRaises(ValueError):
                    exporter.export_source(root, root / 'review')


if __name__ == '__main__':
    unittest.main()
