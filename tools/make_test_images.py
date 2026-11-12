"""Create the small PNG images that the parity tests analyze.

Run from the project folder:   .venv\\Scripts\\python tools\\make_test_images.py

The images are saved as 8-bit RGBA PNGs so the JavaScript original (run under Node by
tools/make_fixtures.mjs) and the Python port read exactly the same pixels.
"""

from __future__ import annotations

import colorsys
import sys
from pathlib import Path

import numpy as np
from PIL import Image

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from colorroom.core.sample import sample_image  # noqa: E402

OUT = ROOT / "tests" / "fixtures" / "images"


def opaque(rgb: np.ndarray) -> np.ndarray:
    alpha = np.full((*rgb.shape[:2], 1), 255, dtype=np.uint8)
    return np.concatenate([rgb.astype(np.uint8), alpha], axis=2)


def tiny() -> np.ndarray:
    """4 x 3 pixels: repeats, a half-transparent pixel and two invisible ones."""
    pixels = [
        [(255, 0, 0, 255), (255, 0, 0, 255), (0, 255, 0, 255), (0, 0, 255, 0)],
        [(0, 0, 255, 255), (1, 2, 3, 128), (255, 0, 0, 255), (0, 255, 0, 255)],
        [(9, 9, 9, 0), (255, 255, 255, 255), (0, 0, 0, 255), (127, 127, 127, 255)],
    ]
    return np.array(pixels, dtype=np.uint8)


def blocks() -> np.ndarray:
    """Flat color blocks of different sizes plus a transparent strip."""
    image = np.zeros((64, 96, 4), dtype=np.uint8)
    image[..., 3] = 255
    image[:, :] = (0xF7, 0xF6, 0xF2, 255)
    image[0:32, 0:40] = (0x5A, 0x3C, 0xB0, 255)
    image[32:64, 0:24] = (0xEE, 0xC5, 0x86, 255)
    image[8:24, 48:90] = (0x33, 0x24, 0x4D, 255)
    image[30:60, 50:70] = (0xA5, 0x8B, 0xD8, 255)
    image[40:44, 72:96] = (0x64, 0x48, 0xAE, 255)
    image[60:64, 0:96] = (0x12, 0x34, 0x56, 0)  # invisible
    image[50, 80] = (0x5A, 0x3C, 0xB1, 255)  # a near-duplicate, one pixel
    return image


def noise() -> np.ndarray:
    """Random colors: nearly every pixel is its own color."""
    rng = np.random.default_rng(7)
    return opaque(rng.integers(0, 256, (192, 256, 3), dtype=np.uint8))


def gradient() -> np.ndarray:
    """Hue across, lightness down: every color family at every tone."""
    width, height = 300, 200
    image = np.zeros((height, width, 3), dtype=np.uint8)
    for y in range(height):
        lightness = 0.04 + 0.92 * y / (height - 1)
        for x in range(width):
            red, green, blue = colorsys.hls_to_rgb(x / width, lightness, 0.85)
            image[y, x] = (round(red * 255), round(green * 255), round(blue * 255))
    return opaque(image)


def gray() -> np.ndarray:
    """A gray ramp with unequal steps, to exercise neutral colors and gray bases."""
    ramp = np.repeat(np.arange(256, dtype=np.uint8)[None, :], 12, axis=0)
    ramp[:, 200:] = 200  # make one gray clearly the most frequent
    return opaque(np.stack([ramp, ramp, ramp], axis=2))


def single() -> np.ndarray:
    image = np.zeros((5, 5, 4), dtype=np.uint8)
    image[:, :] = (0x5A, 0x3C, 0xB0, 255)
    return image


def blobs() -> np.ndarray:
    """Soft overlapping color blobs: smooth, photo-like transitions with a soft-edged hole."""
    width, height = 220, 160
    rng = np.random.default_rng(42)
    ys, xs = np.mgrid[0:height, 0:width]
    image = np.full((height, width, 3), (30.0, 26.0, 48.0))
    for _ in range(9):
        cx, cy = rng.uniform(0, width), rng.uniform(0, height)
        radius = rng.uniform(25, 70)
        color = rng.uniform(40, 255, 3)
        strength = np.exp(-((xs - cx) ** 2 + (ys - cy) ** 2) / (2 * radius**2))[..., None]
        image = image * (1 - strength) + color * strength
    rgba = opaque(np.rint(image))
    hole = (xs - 40) ** 2 + (ys - 120) ** 2
    rgba[..., 3] = np.where(hole < 15**2, 0, np.where(hole < 22**2, 128, 255)).astype(np.uint8)
    return rgba


IMAGES = {
    "tiny": tiny,
    "blocks": blocks,
    "noise": noise,
    "gradient": gradient,
    "gray": gray,
    "single": single,
    "blobs": blobs,
    "sample": sample_image,
}


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    for name, make in IMAGES.items():
        rgba = make()
        Image.fromarray(rgba, "RGBA").save(OUT / f"{name}.png")
        print(f"{name}.png  {rgba.shape[1]} x {rgba.shape[0]}")


if __name__ == "__main__":
    main()
