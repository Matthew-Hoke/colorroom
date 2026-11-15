"""Saving the recolored file from the Your project tab, in the format it came in.

What goes in comes back out: a PNG is saved as a PNG, a Photoshop file as a Photoshop file
with its layers. Other formats are offered when they make sense (see `choices`). The
original file is never written over.

Photoshop files (written without Photoshop; experimental, and checked on every save)
    Every visible pixel layer gets the same color moves as the preview, in place: names,
    order, groups, masks, opacity, blend modes and effects stay as they were. Then the
    file is drawn again and compared with the recolored picture.
    - They match: that file is saved. The new colors are on the real layers.
    - They do not (the colors come from text, shapes, fill or adjustment layers, or from
      effects, which cannot be changed safely from outside Photoshop): the recolored
      picture goes in as one new layer on top, and every original layer stays under it,
      unchanged.
    A flat file (only a Background) has its picture recolored directly.
    The saved file is read back and compared with the original, layer by layer, before
    Colorroom says it worked.
Illustrator files
    Illustrator's own format cannot be written from outside Illustrator. The default is a
    PDF, which Illustrator opens, holding the artwork as one picture.
Everything else
    A picture at the file's full size, tagged as sRGB.

No window code in here.
"""

from __future__ import annotations

import io
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Optional

import numpy as np

from .colorspace import hex_to_key, key_to_oklab, key_to_rgb
from .designer import _oklab_to_rgb, _rgb_to_oklab, boxes_mask, membership
from .index import AnalysisCancelled, ImageError

#: Formats a recolored file can be saved in: key -> (label, extension).
FORMATS = {
    "psd": ("Photoshop file", ".psd"),
    "psb": ("Photoshop large document", ".psb"),
    "png": ("PNG picture", ".png"),
    "jpg": ("JPEG picture", ".jpg"),
    "tiff": ("TIFF picture", ".tif"),
    "webp": ("WebP picture", ".webp"),
    "avif": ("AVIF picture", ".avif"),
    "gif": ("GIF picture", ".gif"),
    "bmp": ("BMP picture", ".bmp"),
    "pdf": ("PDF", ".pdf"),
}
#: The short name used on the button: "Export as PSD…".
SHORT_NAMES = {
    "psd": "PSD", "psb": "PSB", "png": "PNG", "jpg": "JPEG", "tiff": "TIFF",
    "webp": "WebP", "avif": "AVIF", "gif": "GIF", "bmp": "BMP", "pdf": "PDF",
}  # fmt: skip
_EXTENSIONS = {
    ".psd": "psd", ".psb": "psb", ".png": "png", ".jpg": "jpg", ".jpeg": "jpg", ".tif": "tiff",
    ".tiff": "tiff", ".webp": "webp", ".avif": "avif", ".gif": "gif", ".bmp": "bmp", ".pdf": "pdf", ".ai": "ai",
}  # fmt: skip
_COMMON = ("png", "jpg", "tiff", "webp", "psd", "pdf")  # offered for every upload, after its own format
_PIL_NAMES = {"png": "PNG", "jpg": "JPEG", "tiff": "TIFF", "webp": "WEBP", "avif": "AVIF", "gif": "GIF", "bmp": "BMP", "pdf": "PDF"}

PRINT_LONGEST = 4000  # longest side, in pixels, of a PDF or Illustrator page drawn for saving
LAYER_NAME = "Colorroom colors"  # the layer added when the real layers cannot be changed
_SAME = 24  # how far (0..255) a pixel may be from the recolored picture and still count as the same
_ALLOWED = 0.005  # share of the picture that may differ more than that

_WHY = {
    "type": "a text layer: its color can only be changed inside Photoshop",
    "shape": "a shape layer: its color can only be changed inside Photoshop",
    "solidcolorfill": "a color fill layer: its color can only be changed inside Photoshop",
    "gradientfill": "a gradient fill layer: its colors can only be changed inside Photoshop",
    "patternfill": "a pattern fill layer",
    "smartobject": "a smart object: its contents are a file of their own",
}


class ExportError(ImageError):
    """The file could not be saved. The message is written for the person using the app."""


# --------------------------------------------------------------------------------------
# Which formats, and where
# --------------------------------------------------------------------------------------


def source_format(path) -> str:
    """The format key of a file by its name ("psd", "png", "ai"...), or "" if unknown."""
    return _EXTENSIONS.get(Path(str(path)).suffix.lower(), "") if path else ""


def choices(source: str) -> list[str]:
    """The formats offered for an upload of format `source`. Its own format comes first."""
    first = {"ai": "pdf"}.get(source, source)
    if first not in FORMATS:
        first = "png"
    return list(dict.fromkeys([first, *(key for key in _COMMON if not (key == "psd" and first == "psb"))]))


def label(key: str, source: str) -> str:
    """What format `key` is called in the save dialog when the upload was a `source` file."""
    if key in ("psd", "psb"):
        return f"{FORMATS[key][0]}, {'layers kept' if source in ('psd', 'psb') else 'one layer'}"
    if key == "pdf":
        return "PDF for Illustrator, the artwork as one picture" if source == "ai" else "PDF, this page as one picture"
    return FORMATS[key][0]


def default_target(source_path, key: str, folder: str = "") -> Path:
    """Where to suggest saving: beside the original, as "name - Colorroom", never over a file."""
    source = Path(str(source_path)) if source_path else None
    where = source.parent if source else Path(folder or Path.home())
    base = f"{source.stem if source else 'image'} - Colorroom"
    extension = FORMATS[key][1]
    target, number = where / f"{base}{extension}", 2
    while target.exists():
        target, number = where / f"{base} {number}{extension}", number + 1
    return target


def _same_file(first, second) -> bool:
    try:
        return os.path.normcase(os.path.realpath(str(first))) == os.path.normcase(os.path.realpath(str(second)))
    except OSError:
        return False


# --------------------------------------------------------------------------------------
# The color moves
# --------------------------------------------------------------------------------------


@dataclass
class Recipe:
    """The preview's color moves, ready to apply to any picture of the same page."""

    colors: list  # the upload's main colors (HEX)
    targets: list  # what each becomes (HEX); None or "" keeps it
    boxes: list = field(default_factory=list)  # areas left alone, as fractions of the page
    blends: list = field(default_factory=list)  # designer.Blend: in-between shades (Layers.blends)
    flat: list = field(default_factory=list)  # which main colors are flat fills (Layers.flat)


def _lab(color: str) -> np.ndarray:
    return np.array(key_to_oklab(hex_to_key(color)), dtype=np.float64)


def left_out_mask(shape, boxes) -> Optional[np.ndarray]:
    """The areas left alone as a (height, width) mask, or None when there are none.

    Like the preview, a mask that covers everything counts as no mask at all.
    """
    if not boxes:
        return None
    mask = boxes_mask(tuple(shape[:2]), boxes)
    return mask if mask.any() and not mask.all() else None


def recolor_pixels(pixels: np.ndarray, recipe: Recipe, exclude: Optional[np.ndarray] = None, chunk: int = 1 << 19) -> np.ndarray:
    """`pixels` (height, width, 3 or 4) with the recipe's color moves. Works at any size.

    The same moves as `designer.recolor` makes on the preview: every pixel belongs softly
    to each main color and moves by the steps those colors take, so shading and smooth
    edges survive. Done in slices, so a large picture does not need much memory.
    `exclude` is a (height, width) mask of pixels that must not change.
    """
    out = np.ascontiguousarray(pixels, dtype=np.uint8).copy()
    count = min(len(recipe.colors), len(recipe.targets))
    if not count or not out.size:
        return out
    centers = np.array([_lab(color) for color in recipe.colors[:count]])
    steps = np.array([_lab(target) - centers[n] if target else np.zeros(3) for n, target in enumerate(recipe.targets[:count])])
    if not steps.any():
        return out
    blends = [blend for blend in recipe.blends if max(blend.index, blend.low, blend.high) < count]
    flat = out.reshape(-1, out.shape[-1])
    keep = None if exclude is None else ~np.asarray(exclude, dtype=bool).reshape(-1)
    for start in range(0, len(flat), chunk):
        part = flat[start : start + chunk, :3]
        lab = _rgb_to_oklab(part)
        distance = np.linalg.norm(lab[:, None, :] - centers[None, :, :], axis=2)
        weights = membership(lab, centers, blends, list(recipe.flat)[:count], distance)
        moved = _oklab_to_rgb(lab + weights.astype(np.float32).astype(np.float64) @ steps)
        if keep is None:
            part[:] = moved
        else:
            here = keep[start : start + chunk]
            part[here] = moved[here]
    return out


# --------------------------------------------------------------------------------------
# The report
# --------------------------------------------------------------------------------------


@dataclass
class ExportReport:
    """What was saved and how, in words for the person using the app."""

    path: str
    key: str
    how: str  # "picture", "layers" (changed in place), "flat" or "on top"
    width: int = 0
    height: int = 0
    changed: list = field(default_factory=list)  # names of the layers recolored in place
    kept: list = field(default_factory=list)  # (layer name, why it was left as it is)
    notes: list = field(default_factory=list)

    def summary(self) -> str:
        name = Path(self.path).name
        if self.how == "layers":
            count = len(self.changed)
            return f"Saved {name}: {count} {'layer' if count == 1 else 'layers'} recolored in place. Every layer is kept."
        if self.how == "flat":
            return f"Saved {name}: a flat Photoshop file like the original, with the new colors."
        if self.how == "on top":
            return f"Saved {name}: the new colors are on a new top layer, “{LAYER_NAME}”. Your own layers are under it, unchanged."
        return f"Saved {name} ({self.width} × {self.height} pixels)."

    def text(self) -> str:
        lines = [self.summary()]
        if self.changed:
            lines += ["", "Recolored in place:", *(f"  • {name}" for name in self.changed)]
        if self.kept:
            lines += ["", "Left as they are:", *(f"  • {name}: {why}" for name, why in self.kept)]
        if self.notes:
            lines += ["", *self.notes]
        return "\n".join(lines)


# --------------------------------------------------------------------------------------
# Pictures
# --------------------------------------------------------------------------------------


def _srgb_profile() -> bytes:
    from PIL import ImageCms

    return ImageCms.ImageCmsProfile(ImageCms.createProfile("sRGB")).tobytes()


def _partial(target: Path) -> Path:
    """A new, unique file beside `target` to write into first (never an existing file)."""
    import tempfile

    handle, name = tempfile.mkstemp(prefix=f".{target.stem}-", suffix=f".colorroom-part{target.suffix}", dir=target.parent)
    os.close(handle)
    return Path(name)


def _finish(partial: Path, target: Path) -> None:
    os.replace(partial, target)


def _discard(partial: Path) -> None:
    try:
        partial.unlink()
    except OSError:
        pass


def save_picture(rgba: np.ndarray, target, key: str, dpi: Optional[float] = None) -> None:
    """Write an RGBA picture as format `key`. Formats without transparency get white paper."""
    from PIL import Image

    target = Path(str(target))
    rgba = np.ascontiguousarray(rgba, dtype=np.uint8)
    image = Image.fromarray(rgba, "RGBA")
    opaque = bool((rgba[..., 3] == 255).all())
    if key in ("jpg", "pdf") and not opaque:
        paper = Image.new("RGBA", image.size, (255, 255, 255, 255))
        paper.alpha_composite(image)
        image, opaque = paper, True
    if opaque:
        image = image.convert("RGB")
    options: dict = {}
    if key in ("png", "jpg", "tiff", "webp", "avif"):
        options["icc_profile"] = _srgb_profile()
    if key == "jpg":
        options.update(quality=95, subsampling=0)
    elif key == "webp":
        options.update(lossless=True)
    elif key == "avif":
        options.update(quality=95)
    elif key == "tiff":
        options.update(compression="tiff_lzw")
    if dpi and key in ("png", "jpg", "tiff", "bmp"):
        options["dpi"] = (dpi, dpi)
    elif dpi and key == "pdf":
        options["resolution"] = dpi
    partial = _partial(target)
    try:
        image.save(str(partial), format=_PIL_NAMES[key], **options)
        _finish(partial, target)
    except (OSError, ValueError, KeyError) as error:
        _discard(partial)
        raise ExportError(f"The file could not be saved: {error}") from None


def _tidy_dpi(dpi: float) -> float:
    """300 stays 300: some formats store the resolution per metre, so it comes back as 299.999."""
    return float(round(dpi)) if abs(dpi - round(dpi)) < 0.01 else round(dpi, 3)


def psd_dpi(psd) -> Optional[float]:
    """The resolution (pixels per inch) a Photoshop file says it has, or None."""
    from psd_tools.constants import Resource

    try:
        info = psd.image_resources.get_data(Resource.RESOLUTION_INFO)
        dpi = info.horizontal / 65536.0 if info is not None else 0.0
        if info is not None and info.horizontal_unit == 2:  # stored as pixels per centimetre
            dpi *= 2.54
    except Exception:
        return None
    return _tidy_dpi(dpi) if dpi > 0 else None


def _set_psd_dpi(psd, dpi: Optional[float]) -> None:
    """Write the resolution into a Photoshop file (pixels per inch)."""
    from psd_tools.constants import Resource
    from psd_tools.psd.image_resources import ImageResource, ResoulutionInfo

    if not dpi or dpi <= 0:
        return
    fixed = int(round(dpi * 65536))
    psd.image_resources[Resource.RESOLUTION_INFO] = ImageResource(
        key=Resource.RESOLUTION_INFO, data=ResoulutionInfo(fixed, 1, 1, fixed, 1, 1)
    )


def _save_one_layer_psd(rgba: np.ndarray, target: Path, name: str, dpi: Optional[float] = None) -> None:
    from PIL import Image
    from psd_tools import PSDImage

    image = Image.fromarray(np.ascontiguousarray(rgba, dtype=np.uint8), "RGBA")
    psd = PSDImage.new("RGB", image.size, color=(255, 255, 255))
    _set_psd_dpi(psd, dpi)
    psd.append(psd.create_pixel_layer(image, name=name or LAYER_NAME))
    partial = _partial(target)
    try:
        psd.save(str(partial))
        _finish(partial, target)
    except (OSError, ValueError) as error:
        _discard(partial)
        raise ExportError(f"The file could not be saved: {error}") from None


def export_picture(rgba: np.ndarray, recipe: Recipe, target, key: str, dpi: Optional[float] = None, name: str = "") -> ExportReport:
    """Recolor a full-size picture and save it as format `key`."""
    target = Path(str(target))
    recolored = recolor_pixels(rgba, recipe, left_out_mask(rgba.shape, recipe.boxes))
    if key in ("psd", "psb"):
        _save_one_layer_psd(recolored, target, name, dpi)
    else:
        save_picture(recolored, target, key, dpi)
    return ExportReport(str(target), key, "picture", int(rgba.shape[1]), int(rgba.shape[0]))


def full_picture(path, page: int = 0) -> tuple:
    """The upload at full size: (RGBA picture, dots per inch or None, notes)."""
    from . import documents
    from .loader import load_image

    if not documents.is_document(path):
        from PIL import Image

        loaded = load_image(str(path))
        notes = ["This file has several frames; the first one was saved."] if loaded.frames > 1 else []
        dpi = None
        try:
            with Image.open(str(path)) as image:
                found = image.info.get("dpi")
            dpi = _tidy_dpi(float(found[0])) if found and float(found[0]) > 1 else None
        except Exception:
            dpi = None
        return loaded.rgba, dpi, notes
    document = documents.open_document(path)
    try:
        if isinstance(document, documents.PdfDocument):
            rgba = document.render(page, PRINT_LONGEST)
            points = max(document.page_size(page))
            return rgba, max(rgba.shape[:2]) / (points / 72.0), ["Shapes and text were saved as one picture, not as editable artwork."]
        dpi = psd_dpi(document._psd) if isinstance(document, documents.PsdDocument) else None
        return document.render(page, 1 << 30), dpi, ([document.note] if document.note else [])
    finally:
        document.close()


# --------------------------------------------------------------------------------------
# Photoshop files
# --------------------------------------------------------------------------------------


def _rgba(image) -> np.ndarray:
    return np.array(image.convert("RGBA"), dtype=np.uint8)


def _draw(scope, is_board: bool) -> np.ndarray:
    """The page as psd-tools draws it from the layers, in the file's own color values."""
    image = scope.composite(apply_icc=False) if is_board else scope.composite(ignore_preview=True, apply_icc=False)
    if image is None:
        raise ValueError("nothing to draw")
    return _rgba(image)


def _same(first: np.ndarray, second: np.ndarray) -> bool:
    """True when two pictures of the page look the same (a few stray pixels aside)."""
    if first.shape != second.shape:
        return False
    step = max(1, int((first.shape[0] * first.shape[1] / 4_000_000) ** 0.5))
    one, two = first[::step, ::step].astype(np.int16), second[::step, ::step].astype(np.int16)
    visible = (one[..., 3] > 0) | (two[..., 3] > 0)
    if not visible.any():
        return True
    off = np.abs(one - two).max(axis=2) > _SAME
    return float(off[visible].mean()) <= _ALLOWED


def _document_recipe(recipe: Recipe, psd) -> tuple:
    """The recipe in the file's own color values (they differ when its profile is not sRGB)."""
    from PIL import Image, ImageCms
    from psd_tools.constants import Resource

    try:
        data = psd.image_resources.get_data(Resource.ICC_PROFILE)
    except Exception:
        data = None
    if not data:
        return recipe, ""
    try:
        profile = ImageCms.ImageCmsProfile(io.BytesIO(bytes(data)))
        description = (ImageCms.getProfileDescription(profile) or "").strip()
        if "srgb" in description.lower():
            return recipe, ""
        wanted = list(recipe.colors) + [target for target in recipe.targets if target]
        strip = Image.new("RGB", (len(wanted), 1))
        strip.putdata([key_to_rgb(hex_to_key(color)) for color in wanted])
        moved = ImageCms.profileToProfile(strip, ImageCms.createProfile("sRGB"), profile, outputMode="RGB")
        table = {color: "#%02X%02X%02X" % tuple(value[:3]) for color, value in zip(wanted, moved.getdata())}
        converted = Recipe(
            [table[c] for c in recipe.colors], [table[t] if t else t for t in recipe.targets], list(recipe.boxes), list(recipe.blends), list(recipe.flat)
        )
        return converted, f"Colors were converted into your file's color profile ({description or 'not named'})."
    except Exception:
        return recipe, "Your file's color profile could not be read, so the colors were written as sRGB values."


def _structure(psd) -> list:
    return [(layer.name, layer.kind, tuple(layer.bbox), layer.opacity, str(layer.blend_mode)) for layer in psd.descendants()]


def _channel_bytes(layer) -> list:
    return [bytes(data.data) for data in layer._channels]


def _recolor_layer(layer, recipe: Recipe, mask: Optional[np.ndarray], area, version: int) -> bool:
    """Recolor one pixel layer's color channels in place. Transparency and masks are not touched."""
    record = layer._record
    width, height = record.right - record.left, record.bottom - record.top
    color = {int(info.id): (info, data) for info, data in zip(record.channel_info, layer._channels) if int(info.id) in (0, 1, 2)}
    if width <= 0 or height <= 0 or len(color) != 3:
        return False
    planes = [np.frombuffer(color[n][1].get_data(width, height, 8, version), dtype=np.uint8).reshape(height, width) for n in range(3)]
    rgb = np.stack(planes, axis=2)
    exclude = None
    if mask is not None:
        exclude = np.zeros((height, width), dtype=bool)
        left, top = max(record.left, area[0]), max(record.top, area[1])
        right, bottom = min(record.right, area[2]), min(record.bottom, area[3])
        if left < right and top < bottom:
            exclude[top - record.top : bottom - record.top, left - record.left : right - record.left] = mask[
                top - area[1] : bottom - area[1], left - area[0] : right - area[0]
            ]
    new = recolor_pixels(rgb, recipe, exclude)
    if np.array_equal(new, rgb):
        return False
    for n in range(3):
        info, data = color[n]
        data.set_data(np.ascontiguousarray(new[..., n]).tobytes(), width, height, 8, version)
        info.length = data._length
    return True


def _store_preview(psd, recolored: np.ndarray, area, numpy_io) -> None:
    """Write the file's stored picture (what thumbnails, file previews and Colorroom show).

    The new top layer covers the page, so the stored picture is the recolored one. Writing it
    directly means it is right even when psd-tools cannot draw some layers (gradient fills
    need extra libraries), where it would otherwise keep the old colors.
    """
    if not psd.has_preview():
        return
    try:
        full = _rgba(psd.topil(apply_icc=False))
        left, top, right, bottom = area
        full[top:bottom, left:right] = recolored[: bottom - top, : right - left]
        planes = numpy_io.encode_image_data(psd, full[..., :3].astype(np.float32) / 255.0, full[..., 3:4].astype(np.float32) / 255.0)
        psd._record.image_data.set_data(planes, psd._record.header)
        psd._updated = False  # the stored picture is already right: do not let save() redraw it
    except Exception:
        pass  # psd-tools redraws it on save when it can


def _save_psd(psd, target: Path) -> Path:
    partial = _partial(target)
    try:
        psd.save(str(partial))
    except Exception as error:
        _discard(partial)
        raise ExportError(f"The Photoshop file could not be saved: {error}") from None
    return partial


def _refuse(partial: Path, why: str) -> ExportError:
    _discard(partial)
    return ExportError(f"The saved file did not read back correctly ({why}), so it was removed. Nothing was changed. Choose PNG or TIFF instead.")


def export_psd(
    source,
    recipe: Recipe,
    target,
    page: int = 0,
    progress: Optional[Callable[[float, str], None]] = None,
    cancelled: Optional[Callable[[], bool]] = None,
) -> ExportReport:
    """Save a recolored copy of the Photoshop file `source` as `target`. See the module notes."""
    from psd_tools import PSDImage
    from psd_tools.api import numpy_io
    from psd_tools.constants import ColorMode

    tell = progress or (lambda fraction, text: None)

    def check() -> None:
        if cancelled is not None and cancelled():
            raise AnalysisCancelled()

    target = Path(str(target))
    key = "psb" if target.suffix.lower() == ".psb" else "psd"
    if _same_file(source, target):
        raise ExportError("Choose another name: Colorroom never writes over your original file.")

    def open_original():
        try:
            return PSDImage.open(str(source))
        except Exception:
            raise ExportError("The original Photoshop file could not be read again. Was it moved or renamed?") from None

    tell(0.02, "Reading your Photoshop file…")
    psd = open_original()
    if psd.color_mode != ColorMode.RGB or psd.depth != 8:
        what = f"{psd.depth}-bit" if psd.color_mode == ColorMode.RGB else str(psd.color_mode).split(".")[-1].title()
        raise ExportError(
            f"This Photoshop file is {what}. Colorroom writes 8-bit RGB Photoshop files so far. "
            "Choose PNG or TIFF in the save window instead."
        )
    version = psd._record.header.version
    if (key == "psb") != (version == 2):
        raise ExportError("Keep the file's own ending (.psd stays .psd, .psb stays .psb).")
    boards = [layer for layer in psd if layer.kind == "artboard" and layer.is_visible()]
    is_board = bool(boards)
    if is_board and not 0 <= page < len(boards):
        raise ExportError("That artboard could not be found in the file.")
    scope = boards[page] if is_board else psd
    area = tuple(scope.bbox) if is_board else (0, 0, psd.width, psd.height)
    width, height = area[2] - area[0], area[3] - area[1]
    recipe, profile_note = _document_recipe(recipe, psd)
    mask = left_out_mask((height, width), recipe.boxes)
    original = _structure(psd)
    stored = None
    if psd.has_preview():
        try:
            stored = _rgba(psd.topil(apply_icc=False).crop(area))
        except Exception:
            stored = None
    report = ExportReport(str(target), key, "layers", width, height, notes=[profile_note] if profile_note else [])

    # A flat file: the picture itself is the document.
    if not original:
        if stored is None:
            raise ExportError("This Photoshop file has no layers and no picture in it.")
        tell(0.4, "Recoloring the picture…")
        recolored = recolor_pixels(stored, recipe, mask)
        planes = numpy_io.encode_image_data(
            psd, recolored[..., :3].astype(np.float32) / 255.0, recolored[..., 3:4].astype(np.float32) / 255.0
        )
        psd._record.image_data.set_data(planes, psd._record.header)
        check()
        partial = _save_psd(psd, target)
        saved = PSDImage.open(str(partial))
        if _structure(saved) or not np.array_equal(_rgba(saved.topil(apply_icc=False))[..., :3], recolored[..., :3]):
            raise _refuse(partial, "the picture came back different")
        _finish(partial, target)
        report.how = "flat"
        return report

    tell(0.1, "Drawing the layers…")
    try:
        before = _draw(scope, is_board)
    except Exception:
        before = None
    check()
    faithful = before is not None and before.shape[:2] == (height, width) and (stored is None or _same(before, stored))
    layers = [layer for layer in scope.descendants() if not layer.is_group()]
    kept = []
    for layer in layers:
        if not layer.is_visible():
            kept.append((layer.name, "hidden"))
        elif layer.kind != "pixel":
            kept.append((layer.name, _WHY.get(layer.kind, "an adjustment layer")))
        elif layer.has_effects():
            kept.append((f"Effects on {layer.name}", "layer effects keep their own colors"))

    if faithful:
        changed = []
        for number, layer in enumerate(layers):
            check()
            tell(0.2 + 0.4 * number / max(1, len(layers)), f"Recoloring {layer.name}…")
            if layer.kind == "pixel" and layer.is_visible() and _recolor_layer(layer, recipe, mask, area, version):
                changed.append(layer)
        psd.mark_updated()
        tell(0.65, "Checking the result…")
        wanted = recolor_pixels(before, recipe, mask)
        try:
            matches = _same(_draw(scope, is_board), wanted)
        except Exception:
            matches = False
        check()
        if matches:
            tell(0.8, "Saving…")
            partial = _save_psd(psd, target)
            saved, untouched = PSDImage.open(str(partial)), open_original()
            if _structure(saved) != original:
                raise _refuse(partial, "the layers came back different")
            names = {id(layer) for layer in changed}
            for mine, theirs, was in zip(psd.descendants(), saved.descendants(), untouched.descendants()):
                if id(mine) not in names and not mine.is_group() and _channel_bytes(theirs) != _channel_bytes(was):
                    raise _refuse(partial, f"the layer {was.name} changed")
            _finish(partial, target)
            report.changed, report.kept = [layer.name for layer in changed], kept
            return report

    # The real layers cannot carry the change: add the recolored picture as one layer on top.
    from PIL import Image

    tell(0.7, "Adding the new colors as a layer…")
    psd = open_original()
    boards = [layer for layer in psd if layer.kind == "artboard" and layer.is_visible()]
    scope = boards[page] if is_board else psd
    picture = stored if stored is not None else before
    if picture is None:
        raise ExportError("This Photoshop file could not be drawn. In Photoshop, save it again with Maximize Compatibility on.")
    recolored = recolor_pixels(picture, recipe, mask)
    scope.append(scope.create_pixel_layer(Image.fromarray(recolored, "RGBA"), name=LAYER_NAME, top=area[1], left=area[0]))
    _store_preview(psd, recolored, area, numpy_io)
    check()
    partial = _save_psd(psd, target)
    saved = PSDImage.open(str(partial))
    after = _structure(saved)
    added = [entry for entry in after if entry[0] == LAYER_NAME and entry not in original]
    without = list(after)
    if added:
        without.remove(added[-1])
    if not added or without != original:
        raise _refuse(partial, "the layers came back different")
    _finish(partial, target)
    report.how = "on top"
    report.kept = [entry for entry in kept if entry[1] != "hidden"]
    report.notes.append(
        "Why a layer on top: some colors in this file come from text, shapes, fill or adjustment layers, effects or "
        "blending, which cannot be changed safely from outside Photoshop. Hide the new layer to see your original."
        if faithful
        else "Why a layer on top: Colorroom cannot draw this file exactly as Photoshop does, so it did not risk changing "
        "your layers. Hide the new layer to see your original."
    )
    return report


# --------------------------------------------------------------------------------------
# One call for the window
# --------------------------------------------------------------------------------------


def export_upload(
    source_path,
    recipe: Recipe,
    target,
    key: str,
    page: int = 0,
    fallback: Optional[np.ndarray] = None,
    progress: Optional[Callable[[float, str], None]] = None,
    cancelled: Optional[Callable[[], bool]] = None,
) -> ExportReport:
    """Save the recolored upload as format `key`.

    `source_path` is the uploaded file ("" when the picture did not come from a file; then
    `fallback`, an RGBA picture, is used).
    """
    if key not in FORMATS:
        raise ExportError("Colorroom cannot save that kind of file.")
    source = source_format(source_path)
    if source_path and _same_file(source_path, target):
        raise ExportError("Choose another name: Colorroom never writes over your original file.")
    if key in ("psd", "psb") and source in ("psd", "psb"):
        return export_psd(source_path, recipe, target, page, progress, cancelled)
    if progress is not None:
        progress(0.1, "Reading the picture at full size…")
    notes: list = []
    dpi = None
    if source_path:
        rgba, dpi, notes = full_picture(source_path, page)
    elif fallback is not None:
        rgba = fallback
    else:
        raise ExportError("There is no picture to save yet.")
    if cancelled is not None and cancelled():
        raise AnalysisCancelled()
    if progress is not None:
        progress(0.5, "Recoloring and saving…")
    report = export_picture(rgba, recipe, target, key, dpi, Path(str(source_path)).stem if source_path else "")
    report.notes += notes
    if source == "ai":
        report.notes.append("Illustrator's own format can only be written by Illustrator. This PDF opens in Illustrator.")
    return report
