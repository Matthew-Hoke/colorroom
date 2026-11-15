"""Documents with pages: PDFs, Illustrator files and images with several frames.

A `Document` gives each page as a picture (for everything Colorroom already does with
images) and, for PDF-based files, the exact fill and stroke colors written in the file.
No window code in here.

Formats:
    PDF            rendered with pypdfium2 (PDFium; Apache-2.0 / BSD-3-Clause)
    Illustrator    .ai files saved with "Create PDF Compatible File" (Illustrator's default)
                   contain a PDF, and that part is what gets opened. Illustrator's private
                   data is never parsed.
    Photoshop      .psd and .psb files are read with psd-tools (MIT). Artboards become pages.
                   Reading only: recolored copies are written by `file_export`, which
                   checks each one and never touches the original.
    Images         GIF frames and multi-page TIFFs become pages.
"""

from __future__ import annotations

import ctypes
import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Optional, Sequence

import numpy as np

from .colorspace import hex_to_key, key_to_oklab, oklab_distance
from .index import MAX_PIXELS, ImageError
from .loader import load_image

#: File extensions opened as documents (besides images with several frames).
DOCUMENT_EXTENSIONS = (".pdf", ".ai", ".psd", ".psb")
PAGE_LONGEST = 2000  # longest side, in pixels, of a page rendered for analysis
THUMBNAIL_LONGEST = 240
MAX_PAGES = 500

_PDFIUM_LOCK = threading.Lock()  # PDFium is not thread-safe: one call at a time

NO_PDF_SUPPORT = (
    "Opening PDF and Illustrator files needs the pypdfium2 package. "
    'Install it with:  .venv\\Scripts\\python -m pip install -e ".[pdf]"'
)
NO_PSD_SUPPORT = (
    "Opening Photoshop files needs the psd-tools package. "
    'Install it with:  .venv\\Scripts\\python -m pip install -e ".[psd]"'
)
AI_WITHOUT_PDF = (
    "This Illustrator file was saved without PDF compatibility. In Illustrator choose "
    "File > Save As and tick Create PDF Compatible File, or export a PDF."
)


class DocumentError(ImageError):
    """A document could not be opened. The message is written for the person using the app."""


def is_document(path) -> bool:
    return Path(str(path)).suffix.lower() in DOCUMENT_EXTENSIONS


def _to_rgba(rgb: np.ndarray) -> np.ndarray:
    height, width = rgb.shape[:2]
    rgba = np.empty((height, width, 4), dtype=np.uint8)
    rgba[..., :3] = rgb[..., :3]
    rgba[..., 3] = rgb[..., 3] if rgb.shape[2] == 4 else 255
    return rgba


class Document:
    """Base class: a named file with one or more pages."""

    kind = "Image"

    def __init__(self, path: str, page_names: Sequence[str]) -> None:
        self.path = str(path)
        self.name = Path(self.path).name
        self.page_names = list(page_names)

    @property
    def page_count(self) -> int:
        return len(self.page_names)

    def render(self, page: int, longest: int = PAGE_LONGEST) -> np.ndarray:
        """The page as an RGBA picture, its longest side at most `longest` pixels."""
        raise NotImplementedError

    def thumbnail(self, page: int) -> np.ndarray:
        return self.render(page, THUMBNAIL_LONGEST)

    def vector_colors(self, page: int) -> list[str]:
        """Exact fill and stroke colors written in the file for this page (may be empty)."""
        return []

    def image_boxes(self, page: int) -> list[tuple[float, float, float, float]]:
        """Where the page's placed pictures are: (left, top, right, bottom) as fractions 0..1.

        These are the file's own image objects or raster layers, so they are exact, unlike
        a guess made from pixels. Empty when the format cannot tell.
        """
        return []

    #: A sentence for the status line when the page is not shown exactly as its program draws it.
    note = ""

    def close(self) -> None:
        pass


# --------------------------------------------------------------------------------------
# PDF and Illustrator
# --------------------------------------------------------------------------------------


class PdfDocument(Document):
    kind = "PDF"

    def __init__(self, path: str, kind: str = "PDF") -> None:
        try:
            import pypdfium2 as pdfium
        except ImportError:
            raise DocumentError(NO_PDF_SUPPORT) from None
        self._pdfium = pdfium
        try:
            with _PDFIUM_LOCK:
                self._pdf = pdfium.PdfDocument(str(path))
                count = len(self._pdf)
        except pdfium.PdfiumError as error:
            if "password" in str(error).lower():
                raise DocumentError("This PDF is password-protected. Save a copy without the password, then open that.") from None
            raise DocumentError("This file could not be opened as a PDF. It may be damaged.") from None
        except (OSError, ValueError):
            raise DocumentError("This file could not be read.") from None
        if count == 0:
            raise DocumentError("This PDF has no pages.")
        if count > MAX_PAGES:
            raise DocumentError(f"This document has {count:,} pages. Colorroom opens up to {MAX_PAGES}.")
        self.kind = kind
        word = "Artboard" if kind == "Illustrator" else "Page"
        names = []
        for number in range(count):
            label = ""
            try:
                with _PDFIUM_LOCK:
                    label = (self._pdf.get_page_label(number) or "").strip()
            except Exception:
                label = ""
            plain = f"{word} {number + 1}"
            names.append(plain if not label or label == str(number + 1) else f"{plain} · {label}")
        super().__init__(path, names)

    def _scale_for(self, page, longest: int) -> float:
        width, height = page.get_size()
        width, height = max(1.0, width), max(1.0, height)
        scale = longest / max(width, height)
        # Never go over the app's pixel limit, however the page is shaped.
        return min(scale, (MAX_PIXELS * 0.98 / (width * height)) ** 0.5)

    def page_size(self, page: int) -> tuple[float, float]:
        """(width, height) of the page in points (1/72 inch)."""
        try:
            with _PDFIUM_LOCK:
                width, height = self._pdf[page].get_size()
        except (self._pdfium.PdfiumError, IndexError, ValueError):
            raise DocumentError("That page could not be measured.") from None
        return max(1.0, float(width)), max(1.0, float(height))

    def render(self, page: int, longest: int = PAGE_LONGEST) -> np.ndarray:
        try:
            with _PDFIUM_LOCK:
                sheet = self._pdf[page]
                bitmap = sheet.render(scale=self._scale_for(sheet, longest), rev_byteorder=True)
                pixels = bitmap.to_numpy().copy()
        except (self._pdfium.PdfiumError, IndexError, ValueError, MemoryError):
            raise DocumentError(f"{self.page_names[page] if 0 <= page < self.page_count else 'That page'} could not be drawn.") from None
        return _to_rgba(pixels)

    def vector_colors(self, page: int) -> list[str]:
        """Fill and stroke colors of the shapes and text on the page, most used first.

        PDFium reports them as RGB; CMYK and spot colors arrive already converted, so
        treat those as close matches rather than exact print values.
        """
        raw = self._pdfium.raw
        counts: dict[str, int] = {}

        def read(function, handle) -> Optional[str]:
            red, green, blue, alpha = (ctypes.c_uint() for _ in range(4))
            if function(handle, ctypes.byref(red), ctypes.byref(green), ctypes.byref(blue), ctypes.byref(alpha)) and alpha.value:
                return f"#{red.value:02X}{green.value:02X}{blue.value:02X}"
            return None

        try:
            with _PDFIUM_LOCK:
                sheet = self._pdf[page]
                for item in sheet.get_objects(max_depth=8):
                    if item.type == raw.FPDF_PAGEOBJ_PATH:
                        mode, stroked = ctypes.c_int(), ctypes.c_int()
                        raw.FPDFPath_GetDrawMode(item.raw, ctypes.byref(mode), ctypes.byref(stroked))
                        wanted = ([raw.FPDFPageObj_GetFillColor] if mode.value else []) + (
                            [raw.FPDFPageObj_GetStrokeColor] if stroked.value else []
                        )
                    elif item.type == raw.FPDF_PAGEOBJ_TEXT:
                        wanted = [raw.FPDFPageObj_GetFillColor]
                    else:
                        continue
                    for function in wanted:
                        color = read(function, item.raw)
                        if color:
                            counts[color] = counts.get(color, 0) + 1
        except Exception:
            return []  # the picture of the page still works; exact colors are a bonus
        return sorted(counts, key=lambda color: -counts[color])

    def image_boxes(self, page: int) -> list[tuple[float, float, float, float]]:
        raw = self._pdfium.raw
        boxes = []
        try:
            with _PDFIUM_LOCK:
                sheet = self._pdf[page]
                width, height = sheet.get_size()
                for item in sheet.get_objects(max_depth=8):
                    if item.type != raw.FPDF_PAGEOBJ_IMAGE:
                        continue
                    left, bottom, right, top = item.get_bounds()
                    box = (left / width, 1 - top / height, right / width, 1 - bottom / height)
                    box = tuple(min(1.0, max(0.0, value)) for value in box)
                    if box[2] - box[0] > 0.002 and box[3] - box[1] > 0.002:
                        boxes.append(box)
        except Exception:
            return []
        return boxes

    def close(self) -> None:
        try:
            with _PDFIUM_LOCK:
                self._pdf.close()
        except Exception:
            pass


def _open_illustrator(path: str) -> Document:
    try:
        with open(path, "rb") as handle:
            head = handle.read(1024)
    except OSError:
        raise DocumentError("That file could not be found or read.") from None
    if b"%PDF-" not in head:
        raise DocumentError(AI_WITHOUT_PDF)
    return PdfDocument(path, "Illustrator")


# --------------------------------------------------------------------------------------
# Photoshop
# --------------------------------------------------------------------------------------

#: Layer kinds that hold pictures (pixels), as opposed to shapes, text and fills.
_RASTER_KINDS = ("pixel", "smartobject")
_JUDGE_LONGEST = 800  # a layer's own pixels are judged at most this size (it is about texture, not detail)
_PHOTO_SHARE = 0.25  # share of a layer's covered squares that must look photographic


def _looks_like_photo(layer) -> bool:
    """True when a raster layer's own pixels look like a photograph.

    Judged on the layer alone, and only where it has pixels, so a flat paper or background
    layer that spans the whole page is not mistaken for the photo lying on top of it.
    """
    from .designer import busy_grid

    try:
        image = layer.topil()
    except Exception:
        return False
    if image is None:
        return False
    image = image.convert("RGBA")
    if max(image.size) > _JUDGE_LONGEST:
        image.thumbnail((_JUDGE_LONGEST, _JUDGE_LONGEST))
    rgba = np.asarray(image, dtype=np.uint8)
    block = 12
    grid = busy_grid(rgba, block)
    rows, columns = grid.shape
    if rgba.shape[0] < block or rgba.shape[1] < block:
        return False
    covered = rgba[: rows * block, : columns * block, 3].reshape(rows, block, columns, block).mean(axis=(1, 3)) > 128
    return bool(covered.any()) and float(grid[covered].mean()) >= _PHOTO_SHARE


class PsdDocument(Document):
    """A Photoshop file, for looking at and for palette work.

    The picture comes from psd-tools' own rendering of the layers. Some effects and
    adjustment layers are approximated; if rendering fails altogether, the preview that
    Photoshop saved inside the file is shown instead and `note` says so.
    """

    kind = "Photoshop"

    def __init__(self, path: str) -> None:
        try:
            from psd_tools import PSDImage
        except ImportError:
            raise DocumentError(NO_PSD_SUPPORT) from None
        try:
            self._psd = PSDImage.open(str(path))
        except FileNotFoundError:
            raise DocumentError("That file could not be found.") from None
        except Exception:
            raise DocumentError("This file could not be opened as a Photoshop file. It may be damaged.") from None
        width, height = self._psd.size
        if width < 1 or height < 1:
            raise DocumentError("This Photoshop file has no picture in it.")
        self._artboards = [layer for layer in self._psd if layer.kind == "artboard" and layer.is_visible()][:MAX_PAGES]
        names = [f"Artboard {n + 1} · {board.name}" for n, board in enumerate(self._artboards)] or ["Whole image"]
        super().__init__(path, names)
        self.note = ""
        self._cache: dict[int, np.ndarray] = {}
        self._boxes: dict[int, list] = {}

    def _area(self, page: int) -> tuple[int, int, int, int]:
        """(left, top, right, bottom) of the page in the file's own pixels."""
        if self._artboards:
            return tuple(self._artboards[page].bbox)
        return (0, 0, *self._psd.size)

    def _full(self, page: int) -> np.ndarray:
        if page in self._cache:
            return self._cache[page]
        source = self._artboards[page] if self._artboards else self._psd
        image = None
        try:
            image = source.composite()
        except Exception:
            image = None
        if image is None:
            self.note = "Shown from the preview Photoshop saved in the file, so some layers may look slightly different."
            try:
                image = self._psd.topil()
                if image is not None and self._artboards:
                    image = image.crop(self._area(page))
            except Exception:
                image = None
        if image is None:
            raise DocumentError(
                "This Photoshop file could not be drawn. In Photoshop, save it again with Maximize Compatibility on."
            )
        if image.width * image.height > MAX_PIXELS:
            scale = (MAX_PIXELS * 0.98 / (image.width * image.height)) ** 0.5
            image = image.resize((max(1, int(image.width * scale)), max(1, int(image.height * scale))))
        rgba = np.array(image.convert("RGBA"), dtype=np.uint8)
        self._cache = {page: rgba}  # keep one page: these can be large
        return rgba

    def render(self, page: int, longest: int = PAGE_LONGEST) -> np.ndarray:
        if not 0 <= page < self.page_count:
            raise DocumentError("That page could not be drawn.")
        rgba = self._full(page)
        height, width = rgba.shape[:2]
        if max(width, height) <= longest:
            return rgba
        from PIL import Image

        scale = longest / max(width, height)
        small = Image.fromarray(rgba, "RGBA").resize((max(1, round(width * scale)), max(1, round(height * scale))), Image.Resampling.LANCZOS)
        return np.array(small, dtype=np.uint8)

    def image_boxes(self, page: int) -> list[tuple[float, float, float, float]]:
        """Raster layers and smart objects whose own pixels look like photographs.

        Flat raster layers (a paper or background color, a rasterized shape) are not
        pictures: they are recolored like any flat artwork.
        """
        if page in self._boxes:
            return list(self._boxes[page])
        left, top, right, bottom = self._area(page)
        width, height = max(1, right - left), max(1, bottom - top)
        source = self._artboards[page] if self._artboards else self._psd
        boxes = []
        try:
            for layer in source.descendants():
                if layer.kind not in _RASTER_KINDS or not layer.is_visible() or not _looks_like_photo(layer):
                    continue
                l, t, r, b = layer.bbox
                box = ((l - left) / width, (t - top) / height, (r - left) / width, (b - top) / height)
                box = tuple(min(1.0, max(0.0, value)) for value in box)
                if box[2] - box[0] > 0.002 and box[3] - box[1] > 0.002:
                    boxes.append(box)
        except Exception:
            return []
        self._boxes[page] = boxes
        return list(boxes)

    def layer_names(self, page: int = 0) -> list[tuple[str, str]]:
        """(name, kind) of every visible layer, top first: text, shape, pixel, smartobject..."""
        source = self._artboards[page] if self._artboards else self._psd
        return [(layer.name, layer.kind) for layer in reversed(list(source.descendants())) if layer.is_visible()]


# --------------------------------------------------------------------------------------
# Images with several frames
# --------------------------------------------------------------------------------------


class FramesDocument(Document):
    """An animated GIF or a multi-page TIFF: every frame is a page."""

    kind = "Image"

    def __init__(self, path: str, frames: int) -> None:
        word = "Page" if Path(str(path)).suffix.lower() in (".tif", ".tiff") else "Frame"
        super().__init__(path, [f"{word} {number + 1}" for number in range(min(frames, MAX_PAGES))])

    def render(self, page: int, longest: int = PAGE_LONGEST) -> np.ndarray:
        rgba = load_image(self.path, frame=page).rgba
        step = max(1, -(-max(rgba.shape[:2]) // max(1, longest))) if longest < PAGE_LONGEST else 1
        return np.ascontiguousarray(rgba[::step, ::step])


def open_document(path) -> Document:
    """Open a PDF or Illustrator file. Raises DocumentError with a message for the user."""
    path = str(path)
    if not Path(path).is_file():
        raise DocumentError("That file could not be found.")
    extension = Path(path).suffix.lower()
    if extension == ".ai":
        return _open_illustrator(path)
    if extension == ".pdf":
        return PdfDocument(path)
    if extension in (".psd", ".psb"):
        return PsdDocument(path)
    raise DocumentError("Colorroom opens PDF, Illustrator and Photoshop documents, and images.")


# --------------------------------------------------------------------------------------
# What a page looks like, and the whole document at once
# --------------------------------------------------------------------------------------


@dataclass
class PageSummary:
    """The quick look at one page shown in the page strip."""

    thumbnail: np.ndarray  # small RGBA picture
    colors: list[str]  # its main colors, biggest area first
    shares: list[float]  # how much of the page each covers (0..1)
    defined: list[str] = field(default_factory=list)  # exact colors written in the file


def summarize_page(document: Document, page: int, count: int = 6) -> PageSummary:
    from .designer import find_layers

    thumbnail = document.thumbnail(page)
    layers = find_layers(thumbnail, count)
    return PageSummary(thumbnail, layers.colors, layers.shares, document.vector_colors(page))


def summarize(
    document: Document,
    progress: Optional[Callable[[float, str], None]] = None,
    cancelled: Optional[Callable[[], bool]] = None,
    each: Optional[Callable[[int, PageSummary], None]] = None,
) -> list[PageSummary]:
    """Summaries of every page, in order. `each` is called as soon as a page is ready."""
    from .index import AnalysisCancelled

    summaries = []
    for page in range(document.page_count):
        if cancelled is not None and cancelled():
            raise AnalysisCancelled()
        summary = summarize_page(document, page)
        summaries.append(summary)
        if each is not None:
            each(page, summary)
        if progress is not None:
            progress((page + 1) / document.page_count, f"Looking at {document.page_names[page]}…")
    return summaries


@dataclass
class DocumentColor:
    """One color of the whole document, after merging near-identical colors across pages."""

    color: str  # the most common variant
    pages: list[int]  # pages it appears on (0-based, ascending)
    weight: float  # how much of the document it covers (sum of page shares)
    variants: list[str]  # every slightly different version that was merged into it
    defined: bool = False  # True if the file itself defines this color (not only sampled pixels)


def _lab(color: str):
    return key_to_oklab(hex_to_key(color))


def merge_document_colors(
    summaries: Sequence[PageSummary], threshold: float = 0.03, minimum_share: float = 0.004
) -> list[DocumentColor]:
    """One palette for the whole document.

    Colors closer than `threshold` (Oklab distance; about 0.02 is barely visible, 0.05 is
    clearly different) are treated as the same color. Tiny specks under `minimum_share`
    of a page are ignored. Most used first.
    """
    merged: list[DocumentColor] = []
    weights: list[dict[str, float]] = []
    for page, summary in enumerate(summaries):
        entries = [(color, share, False) for color, share in zip(summary.colors, summary.shares) if share >= minimum_share]
        entries += [(color, 0.0, True) for color in summary.defined]
        for color, share, defined in entries:
            home = next((i for i, known in enumerate(merged) if oklab_distance(known.color, color) <= threshold), None)
            if home is None:
                merged.append(DocumentColor(color, [page], share, [color], defined))
                weights.append({color: share})
                continue
            known = merged[home]
            if page not in known.pages:
                known.pages.append(page)
            known.weight += share
            known.defined = known.defined or defined
            if color not in known.variants:
                known.variants.append(color)
            weights[home][color] = weights[home].get(color, 0.0) + share
    for known, tally in zip(merged, weights):
        defined_here = [c for c in known.variants if any(c in s.defined for s in summaries)]
        # Prefer a color the file defines exactly; otherwise the most common sampled variant.
        known.color = defined_here[0] if defined_here else max(tally, key=tally.get)
        known.pages.sort()
    return sorted(merged, key=lambda item: (-len(item.pages), -item.weight))


def near_duplicates(colors: Sequence[DocumentColor], closest: float, farthest: float = 0.07) -> list[tuple[str, str, float]]:
    """Pairs of document colors that are different but suspiciously close.

    These are usually the same brand color typed slightly differently on different pages.
    """
    pairs = []
    for first in range(len(colors)):
        for second in range(first + 1, len(colors)):
            distance = oklab_distance(colors[first].color, colors[second].color)
            if closest < distance <= farthest:
                pairs.append((colors[first].color, colors[second].color, distance))
    return sorted(pairs, key=lambda pair: pair[2])
