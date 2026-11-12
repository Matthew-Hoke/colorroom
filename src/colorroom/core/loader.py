"""Image loading: decode a file with Pillow and hand back plain 8-bit sRGB RGBA pixels.

Improvements over the browser version: embedded color profiles (Display P3, Adobe RGB,
CMYK...) are converted to sRGB so HEX values are right, and the camera's EXIF rotation
is applied. Animated images use one frame (the first unless asked otherwise).
"""

from __future__ import annotations

import io
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Union

import numpy as np
from PIL import Image, ImageCms, ImageOps, UnidentifiedImageError

from .index import MAX_PIXELS, ImageError

#: File extensions offered in the Open dialog.
IMAGE_EXTENSIONS = (".png", ".jpg", ".jpeg", ".webp", ".gif", ".bmp", ".avif", ".tif", ".tiff")

#: The only Pillow decoders the app uses. Pillow can read about 35 more formats (EPS, PSD,
#: PCX, TGA...). Refusing those keeps a file with a misleading name away from decoders the
#: app never needs. Camera JPEGs with several pictures (MPO) open through the JPEG decoder.
ALLOWED_FORMATS = ("PNG", "JPEG", "WEBP", "GIF", "BMP", "AVIF", "TIFF")

Source = Union[str, os.PathLike, bytes, Image.Image]


@dataclass
class LoadedImage:
    rgba: np.ndarray  # (height, width, 4) uint8, sRGB with straight alpha
    name: str  # file name, or a description for images that did not come from a file
    path: str  # full path, or "" if not from a file
    frames: int  # number of frames in the file (1 unless animated)
    frame: int  # which frame was read
    color_managed: bool  # True if an embedded color profile was converted to sRGB

    @property
    def width(self) -> int:
        return int(self.rgba.shape[1])

    @property
    def height(self) -> int:
        return int(self.rgba.shape[0])


def _is_srgb(profile: ImageCms.ImageCmsProfile) -> bool:
    try:
        description = ImageCms.getProfileDescription(profile) or ""
    except Exception:
        return False
    return "srgb" in description.lower()


def _to_srgb(image: Image.Image) -> tuple[Image.Image, bool]:
    """Convert from the embedded ICC profile to sRGB. Returns (image, was_converted)."""
    data = image.info.get("icc_profile")
    if not data:
        return image, False
    try:
        source = ImageCms.ImageCmsProfile(io.BytesIO(data))
        if _is_srgb(source):
            return image, False  # already sRGB: keep the exact pixel values
        if image.mode == "CMYK":
            working, output = image, "RGB"
        elif image.mode in ("RGB", "RGBA"):
            working, output = image, image.mode
        elif image.mode in ("L", "LA", "P", "PA", "1"):
            output = "RGBA" if _has_alpha(image) else "RGB"
            working = image.convert(output)
        else:
            return image, False
        converted = ImageCms.profileToProfile(
            working,
            source,
            ImageCms.createProfile("sRGB"),
            renderingIntent=ImageCms.Intent.RELATIVE_COLORIMETRIC,
            outputMode=output,
        )
        return (converted, True) if converted is not None else (image, False)
    except Exception:
        # A broken or unusual profile must never stop the image from opening.
        return image, False


def _has_alpha(image: Image.Image) -> bool:
    return image.mode in ("RGBA", "LA", "PA") or "transparency" in image.info


def _to_rgba_array(image: Image.Image) -> np.ndarray:
    if image.mode in ("I;16", "I;16L", "I;16B", "I;16N", "I", "F"):
        # High-bit-depth grayscale: scale to 8 bits instead of clipping.
        values = np.asarray(image, dtype=np.float64)
        top = 65535.0 if values.max(initial=0) > 255 else 255.0
        gray = np.clip(np.rint(values / top * 255), 0, 255).astype(np.uint8)
        image = Image.fromarray(gray, "L")
    return np.array(image.convert("RGBA"), dtype=np.uint8)


def _open(source) -> Image.Image:
    """Open a file with the allowed decoders only, whatever its name says it is."""
    Image.init()  # registers every decoder Pillow has, so the allowed names can be looked up
    return Image.open(source, formats=[name for name in ALLOWED_FORMATS if name in Image.OPEN])


def load_image(source: Source, frame: int = 0, name: str = "") -> LoadedImage:
    """Open an image from a path, raw file bytes, or an already opened Pillow image."""
    path = ""
    try:
        if isinstance(source, Image.Image):
            image = source
        elif isinstance(source, (bytes, bytearray, memoryview)):
            image = _open(io.BytesIO(bytes(source)))
        else:
            path = os.fspath(source)
            image = _open(path)
            name = name or Path(path).name
    except FileNotFoundError:
        raise ImageError("That file could not be found.") from None
    except Image.DecompressionBombError:
        # Pillow refuses files that claim an enormous size before reading any pixels.
        raise ImageError("Choose an image with 24 megapixels or fewer.") from None
    except (UnidentifiedImageError, OSError, ValueError):
        raise ImageError("This file could not be decoded. Try a different image.") from None

    try:
        width, height = image.size
        if width * height > MAX_PIXELS:
            raise ImageError("Choose an image with 24 megapixels or fewer.")
        frames = int(getattr(image, "n_frames", 1) or 1)
        frame = max(0, min(int(frame), frames - 1))
        if frames > 1:
            image.seek(frame)
        image.load()
        image = ImageOps.exif_transpose(image) or image
        image, managed = _to_srgb(image)
        rgba = _to_rgba_array(image)
    except ImageError:
        raise
    except (OSError, ValueError, EOFError, SyntaxError, Image.DecompressionBombError):
        raise ImageError("This file could not be decoded. Try a different image.") from None
    return LoadedImage(rgba, name or "image", path, frames, frame, managed)
