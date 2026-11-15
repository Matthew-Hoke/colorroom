"""The design programs behind "Export to Photoshop / Illustrator / InDesign" (route A).

Made by Opus 5.5 (2026-11-14), for Fable 5.2 to review.

`program_links()` gives `ProjectPage.set_app_links` one link per file format. When the person
presses Export, the link:

1. asks the program for the file's exact colors (`Bridge.read`): fills, strokes, text runs,
   gradient and gradient map stops, effects, swatches;
2. works out what the preview does to each of those exact colors (`exact_mapping`: the same
   color moves as the preview, applied to the exact values), so the program changes exactly
   what the person saw change, and only by HEX values the file really holds;
3. lets the program write "name - Colorroom A.ext" beside the original (`Bridge.apply`), which
   reads the saved copy back and checks every color, and returns that report.

For InDesign files the link can also draw page 1 (`preview`), because Colorroom cannot draw
.indd or .idml itself. Nothing here imports pywin32 or window code until a link is used.
Qt-free.
"""

from __future__ import annotations

import dataclasses
import os
import tempfile
from pathlib import Path
from typing import Callable, Optional

import numpy as np

from .colorspace import hex_to_key, key_to_rgb, normalize_hex
from .file_export import Recipe, recolor_pixels

Progress = Optional[Callable[[float, str], None]]
Cancelled = Optional[Callable[[], bool]]

PROGRAMS = {"psd": "Photoshop", "psb": "Photoshop", "ai": "Illustrator", "indd": "InDesign", "idml": "InDesign"}
PREVIEW_PPI = 150.0  # InDesign page pictures: plenty for the preview, quick to make


def exact_mapping(exact_colors, recipe: Recipe) -> dict:
    """{exact HEX in the file: new HEX} for every exact color the preview's moves change.

    Each exact color is treated as one pixel of the preview: it moves by the same steps, so a
    flat color the person sees turn navy becomes that navy in the file. Areas left alone are
    about pictures, which the programs never change, so they play no part here.
    """
    colors = list(dict.fromkeys(normalize_hex(color) for color in exact_colors if color))
    if not colors:
        return {}
    strip = np.zeros((1, len(colors), 4), dtype=np.uint8)
    for position, color in enumerate(colors):
        strip[0, position, :3] = key_to_rgb(hex_to_key(color))
        strip[0, position, 3] = 255
    moved = recolor_pixels(strip, dataclasses.replace(recipe, boxes=[]))
    mapping = {}
    for position, color in enumerate(colors):
        new = "#%02X%02X%02X" % tuple(int(value) for value in moved[0, position, :3])
        if new != color:
            mapping[color] = new
    return mapping


class ProgramLink:
    """One design program, in the shape `ProjectPage.set_app_links` expects."""

    def __init__(self, program: str, factory: Callable, work_dir: Optional[Path] = None) -> None:
        self.program = program
        self._factory = factory
        self._bridge = None
        self.work_dir = Path(work_dir) if work_dir else Path(tempfile.gettempdir()) / "Colorroom"

    def bridge(self):
        if self._bridge is None:
            self._bridge = self._factory()
        return self._bridge

    def available(self) -> bool:
        """True when the program is installed and can be driven. Never starts it."""
        try:
            return bool(self.bridge().available())
        except Exception:
            return False

    def export(self, request, progress: Progress = None, cancelled: Cancelled = None):
        """Let the program recolor a copy of the file; returns its checked `ApplyReport`."""
        from .bridges import BridgeError
        from .index import AnalysisCancelled

        tell = progress or (lambda fraction, text: None)
        name = Path(request.source_path).name
        tell(0.05, f"Asking {self.program} for the exact colors in {name}…")
        self.work_dir.mkdir(parents=True, exist_ok=True)
        manifest = self.bridge().read(request.source_path, work_dir=self.work_dir, preview=False)
        if cancelled is not None and cancelled():
            raise AnalysisCancelled()
        mapping = exact_mapping([color.hex for color in manifest.colors()], request.recipe)
        if not mapping:
            raise BridgeError(
                f"None of the exact colors in {name} change with these choices, so {self.program} was not asked "
                "to save a copy. Give one of the file's colors a different role and try again."
            )
        tell(0.35, f"{self.program} is changing {len(mapping)} {'color' if len(mapping) == 1 else 'colors'} in a copy of {name}…")
        report = self.bridge().apply(request.source_path, mapping, option="A")
        tell(1.0, "Done")
        return report

class _InDesignLink(ProgramLink):
    """InDesign also draws the pages Colorroom cannot draw itself (.indd, .idml)."""

    def preview(self, path, progress: Progress = None, cancelled: Cancelled = None) -> np.ndarray:
        """Page 1 as an RGBA picture, drawn by InDesign."""
        from PIL import Image

        tell = progress or (lambda fraction, text: None)
        tell(0.1, f"Asking {self.program} for a picture of {Path(path).name}…")
        self.work_dir.mkdir(parents=True, exist_ok=True)
        handle, name = tempfile.mkstemp(suffix=".png", prefix="page-", dir=self.work_dir)
        os.close(handle)
        out = Path(name)
        try:
            self.bridge().preview(path, out, page=1, resolution=PREVIEW_PPI)
            with Image.open(out) as image:
                return np.array(image.convert("RGBA"), dtype=np.uint8)
        finally:
            try:
                out.unlink()
            except OSError:
                pass


def program_links(start: bool = True) -> dict:
    """{file format: link} for every design program Colorroom can drive on this computer.

    `start` lets a link start the program when it is not running. The window asks the person
    once before the first export (see `ProjectPage.confirm_app`).
    """
    from . import bridges

    photoshop = ProgramLink("Photoshop", lambda: bridges.photoshop(start=start))
    illustrator = ProgramLink("Illustrator", lambda: bridges.illustrator(start=start))
    indesign = _InDesignLink("InDesign", lambda: bridges.indesign(start=start))
    return {"psd": photoshop, "psb": photoshop, "ai": illustrator, "indd": indesign, "idml": indesign}
