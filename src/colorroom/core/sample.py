"""The built-in sample image, "Dusk at Mesa Valley", drawn with Pillow.

It reproduces the sample that Colorroom 1.7 painted on a browser canvas: a dusk sky
gradient, a sun, four mountain ridges and a few stars.
"""

from __future__ import annotations

import numpy as np
from PIL import Image, ImageDraw

SAMPLE_NAME = "Dusk at Mesa Valley · sample"
SAMPLE_FILE_NAME = "dusk-at-mesa-valley.png"
WIDTH, HEIGHT = 840, 390
_SCALE = 3  # shapes are drawn 3x larger and scaled down for smooth edges

_SKY = ((0.0, (0x38, 0x24, 0x57)), (0.51, (0xA3, 0x65, 0x92)), (1.0, (0xF7, 0xA4, 0x7E)))
_SKY_HEIGHT = 355
_SUN = ((582, 147), 62, "#ffd291")
_RIDGES = (
    ("#79537d", [(0, 235), (93, 188), (166, 217), (255, 159), (334, 211), (385, 185), (484, 245), (570, 217), (670, 185), (739, 225), (840, 206)]),
    ("#514169", [(0, 273), (133, 258), (202, 222), (254, 242), (364, 274), (475, 239), (552, 260), (668, 215), (710, 224), (840, 264)]),
    ("#30294f", [(0, 251), (95, 283), (191, 290), (253, 317), (379, 341), (533, 295), (617, 294), (740, 263), (840, 241)]),
    ("#211d38", [(0, 320), (102, 317), (156, 342), (275, 348), (397, 369), (529, 359), (622, 333), (728, 321), (840, 296)]),
)  # fmt: skip
_STARS = ((95, 54), (180, 91), (256, 45), (364, 74), (670, 74), (760, 49), (725, 110))
_STAR_COLOR = (0xF9, 0xC6, 0x9F)


def _sky(width: int, height: int, sky_height: float) -> np.ndarray:
    """Vertical three-stop gradient; rows below `sky_height` keep the last color."""
    position = np.clip((np.arange(height) + 0.5) / sky_height, 0.0, 1.0)
    stops = np.array([stop for stop, _ in _SKY])
    rows = np.stack(
        [np.interp(position, stops, [color[channel] for _, color in _SKY]) for channel in range(3)], axis=1
    )
    rows = np.rint(rows).astype(np.uint8)
    return np.repeat(rows[:, None, :], width, axis=1)


def sample_image() -> np.ndarray:
    """Return the sample as a (390, 840, 4) RGBA array."""
    big_width, big_height = WIDTH * _SCALE, HEIGHT * _SCALE
    canvas = Image.fromarray(_sky(big_width, big_height, _SKY_HEIGHT * _SCALE), "RGB")
    draw = ImageDraw.Draw(canvas)
    (center_x, center_y), radius, sun_color = _SUN
    draw.ellipse(
        [
            (center_x - radius) * _SCALE,
            (center_y - radius) * _SCALE,
            (center_x + radius) * _SCALE,
            (center_y + radius) * _SCALE,
        ],
        fill=sun_color,
    )
    for color, points in _RIDGES:
        outline = [(0, HEIGHT), *points, (WIDTH, HEIGHT)]
        draw.polygon([(x * _SCALE, y * _SCALE) for x, y in outline], fill=color)
    image = np.array(canvas.resize((WIDTH, HEIGHT), Image.Resampling.BOX), dtype=np.uint8)
    for x, y in _STARS:
        image[y : y + 2, x : x + 2] = _STAR_COLOR
    alpha = np.full((HEIGHT, WIDTH, 1), 255, dtype=np.uint8)
    return np.concatenate([image, alpha], axis=2)
