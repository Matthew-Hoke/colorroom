"""Command-line access to the Colorroom engine. No window, no Qt.

Examples:
    python -m colorroom analyze photo.jpg
    python -m colorroom analyze photo.jpg --palette triadic --pick "#B47A3C"
    python -m colorroom analyze photo.jpg -n 8 --export palette.ase --csv all-colors.csv
    python -m colorroom analyze photo.jpg --json
"""

from __future__ import annotations

import argparse
import json
import sys
from typing import Optional, Sequence

from . import APP_NAME, __version__
from .core import palettes
from .core.exporters import save_palette, write_inventory_csv
from .core.index import ColorIndex, ImageError
from .core.loader import load_image
from .core.sample import SAMPLE_NAME, sample_image


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="colorroom", description=f"{APP_NAME}: find the colors in any image.")
    parser.add_argument("--version", action="version", version=f"{APP_NAME} {__version__}")
    commands = parser.add_subparsers(dest="command", required=True)
    analyze = commands.add_parser("analyze", help="Count an image's colors and build a palette.")
    analyze.add_argument("image", help='Image file, or "sample" for the built-in sample image.')
    analyze.add_argument("--palette", default="balanced", choices=palettes.MODES, help="Palette style (default: balanced).")
    analyze.add_argument("-n", "--colors", type=int, default=palettes.DEFAULT_SIZE, help="Number of palette colors, 3 to 12.")
    analyze.add_argument("--theme", default="Spring", choices=list(palettes.THEMES), help="Theme for the seasonal style.")
    analyze.add_argument("--pick", action="append", default=[], metavar="HEX", help="A picked color; repeat for more. The first is the base.")
    analyze.add_argument("--top", type=int, default=10, help="How many of the most frequent colors to list.")
    analyze.add_argument("--frame", type=int, default=0, help="Frame to read from an animated image (0 = first).")
    analyze.add_argument("--csv", metavar="FILE", help="Write every distinct color to a CSV file.")
    analyze.add_argument("--export", metavar="FILE", help="Save the palette (.css, .json, .ase, .gpl, .png or .txt).")
    analyze.add_argument("--json", action="store_true", help="Print the result as JSON.")
    return parser


def analyze(arguments: argparse.Namespace) -> int:
    if arguments.image == "sample":
        name, rgba = SAMPLE_NAME, sample_image()
    else:
        loaded = load_image(arguments.image, frame=arguments.frame)
        name, rgba = loaded.name, loaded.rgba
    index = ColorIndex.build(rgba)
    size = max(palettes.SIZES[0], min(palettes.SIZES[-1], arguments.colors))
    palette = palettes.build_palette(index, arguments.palette, size, arguments.theme, arguments.pick)
    top = [int(position) for position in index.frequency_order[: max(0, arguments.top)]]

    if arguments.csv:
        write_inventory_csv(index, arguments.csv)
    if arguments.export:
        save_palette(palette.colors, arguments.export, f"{name} palette", [entry.role for entry in palette.entries])

    if arguments.json:
        result = {
            "image": name,
            "width": index.width,
            "height": index.height,
            "visiblePixels": index.visible_pixels,
            "distinctColors": index.distinct,
            "mostFrequent": [
                {"hex": index.hex(position), "pixels": index.count(position), "share": index.share(position)}
                for position in top
            ],
            "palette": {
                "style": palette.mode,
                "note": palette.note,
                "colors": [
                    {"hex": entry.color, "imageColor": entry.source_color, "role": entry.role,
                     "x": entry.point.x, "y": entry.point.y}
                    for entry in palette.entries
                ],
            },
        }  # fmt: skip
        print(json.dumps(result, indent=2, ensure_ascii=False))
        return 0

    print(f"{name}: {index.width:,} x {index.height:,}, {index.visible_pixels:,} visible pixels")
    print(f"{index.distinct:,} distinct colors")
    if top:
        print(f"\nMost frequent ({len(top)}):")
        for position in top:
            print(f"  {index.hex(position)}  {index.count(position):>12,} px  {index.share(position) * 100:8.3f}%")
    print(f"\n{palettes.MODE_LABELS[palette.mode]} palette ({len(palette.entries)} colors). {palette.note}")
    for entry in palette.entries:
        note = "" if entry.exact else f"  (closest image color {entry.source_color})"
        role = f"  {entry.role}" if entry.role else ""
        print(f"  {entry.color}{role}{note}")
    if arguments.csv:
        print(f"\nAll colors written to {arguments.csv}")
    if arguments.export:
        print(f"Palette saved to {arguments.export}")
    return 0


def main(argv: Optional[Sequence[str]] = None) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")  # safe output on Windows consoles
    arguments = build_parser().parse_args(argv)
    try:
        return analyze(arguments)
    except ImageError as error:
        print(f"Error: {error}", file=sys.stderr)
        return 2
    except (OSError, ValueError) as error:
        print(f"Error: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
