import tempfile
import unittest
from pathlib import Path

from PIL import Image, ImageDraw

from prepare_logo_assets import prepare_logo_assets


class LogoAssetTests(unittest.TestCase):
    def test_creates_flat_transparent_png_small_preview_and_multisize_ico(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            source = root / 'source.png'
            image = Image.new('RGBA', (300, 300), (0, 0, 0, 0))
            ImageDraw.Draw(image).ellipse((40, 40, 260, 260), fill=(100, 120, 150, 255))
            image.save(source)
            out = prepare_logo_assets(source, root / 'generated')
            saved = Image.open(out['png']).convert('RGBA')
            self.assertEqual(saved.size, (300, 300))
            self.assertEqual(saved.getpixel((150, 150)), (199, 119, 79, 255))
            self.assertEqual(saved.getpixel((0, 0))[3], 0)
            with Image.open(out['small_png']) as small:
                self.assertEqual(small.size, (64, 64))
            with Image.open(out['ico']) as icon:
                self.assertEqual(icon.size, (256, 256))
                self.assertIn((32, 32), icon.info['sizes'])
            with self.assertRaises(FileExistsError):
                prepare_logo_assets(source, root / 'generated')

    def test_rejects_source_without_true_transparency(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            source = root / 'opaque.png'
            Image.new('RGBA', (300, 300), (1, 2, 3, 255)).save(source)
            with self.assertRaises(ValueError):
                prepare_logo_assets(source, root / 'generated')
            self.assertFalse((root / 'generated').exists())

    def test_preserves_approved_source_colors(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            source = root / 'source.png'
            image = Image.new('RGBA', (300, 300), (0, 0, 0, 0))
            ImageDraw.Draw(image).ellipse((40, 40, 260, 260), fill=(180, 95, 60, 255))
            image.save(source)
            out = prepare_logo_assets(source, root / 'preserved', preserve_color=True)
            saved = Image.open(out['png']).convert('RGBA')
            self.assertEqual(saved.getpixel((150, 150)), (180, 95, 60, 255))
            self.assertEqual(saved.getpixel((0, 0))[3], 0)


if __name__ == '__main__':
    unittest.main()
