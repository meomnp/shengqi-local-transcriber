"""Create deterministic app-ready logo files from an approved transparent PNG."""
import argparse
from pathlib import Path

from PIL import Image


def prepare_logo_assets(source: Path, output_dir: Path, color: str = '#C7774F', preserve_color: bool = False):
    source = Path(source).resolve(strict=True)
    output_dir = Path(output_dir).absolute()
    png_path = output_dir / 'shengqi-logo.png'
    small_path = output_dir / 'shengqi-logo-64.png'
    ico_path = output_dir / 'app.ico'
    if any(path.exists() for path in (png_path, small_path, ico_path)):
        raise FileExistsError('Logo output exists; choose a new output directory')
    rgba = Image.open(source).convert('RGBA')
    if min(rgba.size) < 256:
        raise ValueError('Source logo must be at least 256×256')
    alpha = rgba.getchannel('A')
    if alpha.getextrema() != (0, 255):
        raise ValueError('Source must contain both fully transparent and opaque pixels')
    if not color.startswith('#') or len(color) != 7:
        raise ValueError('Color must be a six-digit hex value such as #C7774F')
    rgb = tuple(int(color[index:index + 2], 16) for index in (1, 3, 5))
    if preserve_color:
        flat = rgba.copy()
    else:
        flat = Image.new('RGBA', rgba.size, rgb + (255,))
        flat.putalpha(alpha)

    output_dir.mkdir(parents=True, exist_ok=False)
    flat.save(png_path, format='PNG', optimize=True)
    flat.resize((64, 64), Image.Resampling.LANCZOS).save(small_path, format='PNG', optimize=True)
    flat.save(ico_path, format='ICO', sizes=[(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)])

    check = Image.open(png_path).convert('RGBA')
    visible = check.getchannel('A').getbbox()
    if visible is None or visible == (0, 0, *check.size):
        raise ValueError('Logo alpha mask is empty or has no transparent margin')
    if check.getpixel((0, 0))[3] != 0:
        raise ValueError('Logo must have transparent outer corner pixels')
    pixels = check.load()
    if not preserve_color:
        for y in range(check.height):
            for x in range(check.width):
                if pixels[x, y][3] and pixels[x, y][:3] != rgb:
                    raise ValueError('Logo recolor verification failed')
    return {'png': png_path, 'small_png': small_path, 'ico': ico_path, 'source': source, 'color': color}


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source', required=True, type=Path)
    parser.add_argument('--output-dir', required=True, type=Path)
    parser.add_argument('--color', default='#C7774F')
    parser.add_argument('--preserve-color', action='store_true',
                        help='Keep the approved source logo colors instead of applying a flat recolor')
    args = parser.parse_args()
    for key, value in prepare_logo_assets(args.source, args.output_dir, args.color, args.preserve_color).items():
        print(f'{key}: {value}')
