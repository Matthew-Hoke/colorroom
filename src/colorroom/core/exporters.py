"""Exports: the full color inventory as CSV, and palettes in formats other tools read."""

from __future__ import annotations

import json
import struct
from pathlib import Path
from typing import Callable, Iterator, Optional, Sequence

from .colorspace import hex_to_key, key_to_rgb, normalize_hex, readable_text_color
from .index import AnalysisCancelled, ColorIndex

CSV_HEADER = "hex,pixels,percent\r\n"
_CSV_BLOCK = 8192

#: Palette file formats: key -> (menu label, file extension).
PALETTE_FORMATS = {
    "css": ("CSS variables", ".css"),
    "json": ("JSON", ".json"),
    "ase": ("Adobe Swatch Exchange (Photoshop, Illustrator, InDesign)", ".ase"),
    "gpl": ("GIMP / Inkscape / Krita palette", ".gpl"),
    "png": ("PNG swatch sheet", ".png"),
    "txt": ("Plain text list", ".txt"),
}


# --------------------------------------------------------------------------------------
# Inventory
# --------------------------------------------------------------------------------------


def inventory_csv_blocks(index: ColorIndex) -> Iterator[str]:
    """The inventory CSV as text blocks: every distinct color in HEX order.

    Columns are hex, pixels and percent (of visible pixels, nine decimals), with
    Windows line endings, exactly as Colorroom 1.7 wrote it.
    """
    yield CSV_HEADER
    visible = index.visible_pixels
    for start in range(0, index.distinct, _CSV_BLOCK):
        keys = index.keys[start : start + _CSV_BLOCK].tolist()
        counts = index.counts[start : start + _CSV_BLOCK].tolist()
        yield "".join(
            f"#{key:06X},{count},{count / visible * 100:.9f}\r\n" for key, count in zip(keys, counts)
        )


def write_inventory_csv(
    index: ColorIndex,
    path,
    progress: Optional[Callable[[float, str], None]] = None,
    cancelled: Optional[Callable[[], bool]] = None,
) -> int:
    """Write the inventory CSV to `path`. Returns the number of colors written."""
    blocks = max(1, -(-index.distinct // _CSV_BLOCK))
    target = Path(path)
    try:
        with open(target, "w", encoding="utf-8", newline="") as handle:
            for number, block in enumerate(inventory_csv_blocks(index)):
                if cancelled is not None and cancelled():
                    raise AnalysisCancelled()
                handle.write(block)
                if progress is not None and number % 16 == 0:
                    progress(min(1.0, number / blocks), "Writing CSV…")
    except AnalysisCancelled:
        target.unlink(missing_ok=True)
        raise
    return index.distinct


# --------------------------------------------------------------------------------------
# Palettes
# --------------------------------------------------------------------------------------


def _clean(colors: Sequence[str]) -> list[str]:
    return [normalize_hex(color) for color in colors]


def palette_text(colors: Sequence[str]) -> str:
    """'#AAAAAA, #BBBBBB': what "Copy palette" puts on the clipboard."""
    return ", ".join(_clean(colors))


def palette_css(colors: Sequence[str]) -> str:
    """CSS custom properties, --palette-1 onward."""
    variables = "\n".join(f"  --palette-{number}: {color};" for number, color in enumerate(_clean(colors), 1))
    return f":root {{\n{variables}\n}}\n"


def palette_json(colors: Sequence[str], name: str = "Colorroom palette", roles: Sequence[str] = ()) -> str:
    swatches = []
    for position, color in enumerate(_clean(colors)):
        red, green, blue = key_to_rgb(hex_to_key(color))
        swatch = {"hex": color, "rgb": [red, green, blue]}
        if position < len(roles) and roles[position]:
            swatch["role"] = roles[position]
        swatches.append(swatch)
    return json.dumps({"name": name, "colors": swatches}, indent=2, ensure_ascii=False) + "\n"


def palette_gpl(colors: Sequence[str], name: str = "Colorroom palette") -> str:
    """GIMP palette text format (also read by Inkscape and Krita)."""
    colors = _clean(colors)
    lines = ["GIMP Palette", f"Name: {name}", f"Columns: {min(len(colors), 16) or 1}", "#"]
    for color in colors:
        red, green, blue = key_to_rgb(hex_to_key(color))
        lines.append(f"{red:3d} {green:3d} {blue:3d}\t{color}")
    return "\n".join(lines) + "\n"


def palette_ase(colors: Sequence[str], name: str = "Colorroom palette") -> bytes:
    """Adobe Swatch Exchange file: load it from the Swatches panel of an Adobe app.

    The swatches are written as RGB colors inside one named group.
    """

    def text(value: str) -> bytes:
        encoded = (value + "\0").encode("utf-16-be")
        return struct.pack(">H", len(encoded) // 2) + encoded

    def block(kind: int, body: bytes) -> bytes:
        return struct.pack(">HI", kind, len(body)) + body

    normal_swatch = 2  # color type: 0 = global, 1 = spot, 2 = normal
    colors = _clean(colors)
    blocks = [block(0xC001, text(name))]  # group start
    for color in colors:
        red, green, blue = key_to_rgb(hex_to_key(color))
        values = struct.pack(">fff", red / 255, green / 255, blue / 255)
        body = text(color) + b"RGB " + values + struct.pack(">H", normal_swatch)
        blocks.append(block(0x0001, body))
    blocks.append(block(0xC002, b""))  # group end
    return b"ASEF" + struct.pack(">HHI", 1, 0, len(blocks)) + b"".join(blocks)


def palette_png(colors: Sequence[str], path, roles: Sequence[str] = ()) -> None:
    """Save a swatch sheet image: one labelled block per color."""
    from PIL import Image, ImageDraw, ImageFont

    colors = _clean(colors) or ["#FFFFFF"]
    block_width, block_height, label_height, margin = 220, 220, 64, 24
    width = margin * 2 + block_width * len(colors)
    height = margin * 2 + block_height + label_height
    sheet = Image.new("RGB", (width, height), "#FFFEFA")
    draw = ImageDraw.Draw(sheet)
    try:
        font = ImageFont.load_default(size=22)
        small = ImageFont.load_default(size=15)
    except TypeError:  # very old Pillow: fixed-size default font
        font = small = ImageFont.load_default()
    for position, color in enumerate(colors):
        left = margin + position * block_width
        draw.rectangle([left, margin, left + block_width - 1, margin + block_height - 1], fill=color)
        draw.text((left + 12, margin + block_height + 10), color, fill="#211D26", font=font)
        if position < len(roles) and roles[position]:
            draw.text((left + 12, margin + block_height + 38), roles[position], fill="#635C69", font=small)
        draw.text((left + 12, margin + 10), str(position + 1), fill=readable_text_color(color), font=small)
    sheet.save(path)


def save_palette(colors: Sequence[str], path, name: str = "Colorroom palette", roles: Sequence[str] = ()) -> str:
    """Save a palette to `path`; the format follows the file extension. Returns the format key."""
    target = Path(path)
    extension = target.suffix.lower()
    if extension == ".css":
        target.write_text(palette_css(colors), encoding="utf-8", newline="\n")
        return "css"
    if extension == ".json":
        target.write_text(palette_json(colors, name, roles), encoding="utf-8", newline="\n")
        return "json"
    if extension == ".gpl":
        target.write_text(palette_gpl(colors, name), encoding="utf-8", newline="\n")
        return "gpl"
    if extension == ".ase":
        target.write_bytes(palette_ase(colors, name))
        return "ase"
    if extension == ".png":
        palette_png(colors, target, roles)
        return "png"
    if extension == ".txt":
        target.write_text("\n".join(_clean(colors)) + "\n", encoding="utf-8", newline="\n")
        return "txt"
    raise ValueError(f"Unsupported palette format: {extension or '(no extension)'}")
