import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import local_transcriber as app


class DistributionPaths(unittest.TestCase):
    def test_bundle_precedes_user_cache(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            bundled = root / 'app' / 'models' / 'faster-whisper-small'
            cached = root / 'cache' / 'small'
            for model in (bundled, cached):
                model.mkdir(parents=True)
                (model / 'model.bin').touch()
                (model / 'config.json').write_text('{}')
            with patch.object(app, 'APP_ROOT', root / 'app'), patch.object(app, 'MODEL_ROOT', root / 'cache'):
                self.assertEqual(app.resolve_model_source('small'), str(bundled))
                self.assertEqual(app.resolve_model_source('medium'), 'medium')

    def test_partial_bundle_falls_back_to_cache(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            bundled = root / 'app' / 'models' / 'faster-whisper-small'
            cached = root / 'cache' / 'small'
            bundled.mkdir(parents=True)
            (bundled / 'model.bin').touch()
            cached.mkdir(parents=True)
            (cached / 'model.bin').touch()
            (cached / 'config.json').write_text('{}')
            with patch.object(app, 'APP_ROOT', root / 'app'), patch.object(app, 'MODEL_ROOT', root / 'cache'):
                self.assertEqual(app.resolve_model_source('small'), str(cached))

    def test_pyinstaller_internal_bundle_precedes_user_cache(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            bundled = root / 'app' / '_internal' / 'models' / 'faster-whisper-small'
            cached = root / 'cache' / 'small'
            for model in (bundled, cached):
                model.mkdir(parents=True)
                (model / 'model.bin').touch()
                (model / 'config.json').write_text('{}')
            with patch.object(app, 'APP_ROOT', root / 'app'), patch.object(app, 'MODEL_ROOT', root / 'cache'):
                self.assertEqual(app.resolve_model_source('small'), str(bundled))

    def test_pyinstaller_meipass_bundle_precedes_user_cache(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            meipass = root / 'runtime'
            bundled = meipass / 'models' / 'faster-whisper-small'
            cached = root / 'cache' / 'small'
            for model in (bundled, cached):
                model.mkdir(parents=True)
                (model / 'model.bin').touch()
                (model / 'config.json').write_text('{}')
            with patch.object(app, 'APP_ROOT', root / 'app'), patch.object(app, 'MODEL_ROOT', root / 'cache'), patch.object(app.sys, '_MEIPASS', str(meipass), create=True):
                self.assertEqual(app.resolve_model_source('small'), str(bundled))


if __name__ == '__main__':
    unittest.main()
