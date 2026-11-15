"""The rules of the "Your project" tab, without any window code.

Made by Opus 5.5 (2026-11-14), for Fable 5.2 to review.

"Your project" opens the person's own design file, tries palettes on it and exports the same
file type back. Everything here is plain Python so the tests and the command line can use it:

    source_format, export_label, accuracy_line, export_formats
        which file came in and what the one Export button says and does
    PaletteChoice, palette_choices, ideas_from_file, choose_starter, starting_variant
        the palette picker: My palettes, then the Library, then ideas made from the file,
        and the palette a file starts with, so the first result needs no settings
    leave_out, photo_line
        the photo areas that are left alone, and how that is said
    contrast_line
        the footer's "Text on background: easy to read (7.2:1)"
    FileFacts, read_facts
        the line under the tab title: "Poster.psd  1000 × 640 · 24 layers · RGB · 300 ppi"
    ExportRequest, run_app_link, report_parts
        the hook that lets Photoshop, Illustrator or InDesign write the file (see
        `run_app_link` for the exact contract)
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Optional, Sequence

from . import designer, file_export, palettes
from .colorspace import contrast_ratio, from_hsl, hex_to_key, key_to_oklab, normalize_hex, shift_on_wheel, to_hsl

# --------------------------------------------------------------------------------------
# Which file came in, and what Export does with it
# --------------------------------------------------------------------------------------

#: The program that writes each native format, when Colorroom can link to it.
APP_NAMES = {"psd": "Photoshop", "psb": "Photoshop", "ai": "Illustrator", "indd": "InDesign", "idml": "InDesign"}

#: File endings the open window offers (InDesign's .indd is listed so its message can be shown).
OPEN_EXTENSIONS = (".psd", ".psb", ".ai", ".pdf", ".png", ".jpg", ".jpeg", ".webp", ".gif", ".bmp", ".avif", ".tif", ".tiff")

INDD_WITH_IDML = (
    "Colorroom reads .indd files through InDesign. In InDesign choose File > Export > "
    "InDesign Markup (IDML), then open that file here."
)
INDD_WITHOUT_IDML = (
    "Colorroom reads .indd files through InDesign, and that link is not ready yet. For now, in "
    "InDesign choose File > Export > Adobe PDF (Print), then open the PDF here."
)
IDML_NOT_YET = (
    "Colorroom cannot show IDML files yet. In InDesign choose File > Export > Adobe PDF (Print), "
    "then open the PDF here."
)


def source_format(path) -> str:
    """The format key of a file by its ending: "psd", "png", "ai", "indd", "idml"... or ""."""
    if not path:
        return ""
    suffix = Path(str(path)).suffix.lower()
    if suffix in (".indd", ".idml"):
        return suffix[1:]
    return file_export.source_format(path)


def indd_message(idml_readable: bool) -> str:
    """What opening an .indd file without an InDesign link says.

    The designer's wording sends the person to IDML. That is only honest once Colorroom can
    open IDML, so until then the message points to a PDF instead.
    """
    return INDD_WITH_IDML if idml_readable else INDD_WITHOUT_IDML


def export_formats(source: str) -> list[str]:
    """The formats Colorroom itself can write for a file of format `source`; its own comes first."""
    return file_export.choices("png" if source in ("indd", "idml") else source)


def export_label(source_format: str, app_found: bool) -> str:
    """The text of the one primary Export button.

    | File in        | App found   | Button                 |
    | .psd           | Photoshop   | Export to Photoshop    |
    | .psd           | no          | Export as PSD…         |
    | .psb           | no          | Export as PSB…         |
    | .ai            | Illustrator | Export to Illustrator  |
    | .ai            | no          | Export as PDF…         |
    | .indd / .idml  | InDesign    | Export to InDesign     |
    | .idml          | no          | Export as IDML…        |
    | anything else  |             | Export as <format>…    |

    A .psb is labelled PSB rather than PSD because that is the file the button writes.
    An .indd cannot be opened without InDesign, so it always names InDesign.
    """
    source = source_format or ""
    app = APP_NAMES.get(source)
    if app is not None and (app_found or source == "indd"):
        return f"Export to {app}"
    if source == "idml":
        return "Export as IDML…"
    first = export_formats(source)[0]
    return f"Export as {file_export.SHORT_NAMES[first]}…"


def suggested_target(source_path) -> Path:
    """A new file beside the original, "name - Colorroom.ext", that does not exist yet."""
    source = Path(str(source_path))
    target, number = source.with_name(f"{source.stem} - Colorroom{source.suffix}"), 2
    while target.exists():
        target, number = source.with_name(f"{source.stem} - Colorroom {number}{source.suffix}"), number + 1
    return target


def accuracy_line(source_format: str, app_found: bool) -> str:
    """The footer line about how far the preview can be trusted."""
    if app_found and source_format in APP_NAMES:
        return "The preview is close. The exported file is exact."
    return "The exported file matches this preview."


# --------------------------------------------------------------------------------------
# Palettes to try
# --------------------------------------------------------------------------------------

MINE, LIBRARY, IDEAS = "My palettes", "Library", "Ideas from this file"
STARTER_LABEL = "Starter palette, made from your file"
#: Color-wheel styles of `palettes.py` offered as ideas, with how far (in twelfths of the
#: artist's wheel, as in `palettes.WHEEL_STYLES`) every colorful color of the file turns:
#: to its triadic partner, its complement, and so on. The first one is the starter.
IDEA_STEPS = {
    "triadic": (4,),
    "complementary": (6,),
    "split": (7,),
    "analogous": (1,),
    "tetradic": (2,),
}
_NEUTRAL = 0.04  # Oklab chroma below which a color counts as a gray: it keeps its color


@dataclass(frozen=True)
class PaletteChoice:
    """One entry of the palette picker."""

    key: str  # "mine:<id>", "library:<id>" or "idea:<style>": stable while the palette lives
    label: str
    colors: tuple
    names: tuple = ()
    group: str = ""  # MINE, LIBRARY or IDEAS
    favorite: bool = False
    roles: tuple = ()  # (role, HEX) pairs an idea comes with, so each file color gets its partner

    @property
    def from_file(self) -> bool:
        """True for palettes made from the open file (they change with the file)."""
        return self.key.startswith("idea:")


def _chroma(color: str) -> float:
    _, a, b = key_to_oklab(hex_to_key(color))
    return math.hypot(a, b)


def _lightness(color: str) -> float:
    return key_to_oklab(hex_to_key(color))[0]


def turn(color: str, steps: int) -> str:
    """`color` moved `steps` twelfths around the artist's wheel (as `palettes.py` does),
    with its saturation kept and its perceived lightness kept, so contrast survives."""
    hue, saturation, _ = to_hsl(color)
    hue = shift_on_wheel(hue, steps)
    wanted = _lightness(color)
    low, high = 0.0, 1.0
    for _ in range(16):
        middle = (low + high) / 2
        if _lightness(from_hsl(hue, saturation, middle)) < wanted:
            low = middle
        else:
            high = middle
    return from_hsl(hue, saturation, (low + high) / 2)


def ideas_from_file(file_colors: Sequence[str], jobs: Sequence[str] = (), styles: Sequence[str] = tuple(IDEA_STEPS)) -> list[PaletteChoice]:
    """A few palettes made from the file's own main colors with the color-wheel styles.

    Every colorful main color turns around the wheel by the same step (triadic: a third
    of the way round), so the colors keep their relationships; grays stay as they are. Lightness is kept, so the file's light and dark, and its
    contrast, stay where they were. `jobs` are the roles suggested for the file's colors;
    each idea comes with the roles that give every file color its own partner, so the
    first result needs no settings.
    """
    colors = [normalize_hex(color) for color in file_colors]
    if not colors:
        return []
    vivid = sorted((color for color in colors if _chroma(color) >= _NEUTRAL), key=_chroma, reverse=True)
    ideas = []
    for position, style in enumerate(styles):
        steps = IDEA_STEPS[style]
        moved = {color: turn(color, steps[rank % len(steps)]) for rank, color in enumerate(vivid)}
        if not vivid:  # a gray file: tint its middle grays, keep paper and ink
            light, dark = max(colors, key=_lightness), min(colors, key=_lightness)
            for rank, color in enumerate(color for color in colors if color not in (light, dark)):
                moved[color] = turn(from_hsl(220.0, 0.55, to_hsl(color)[2]), steps[rank % len(steps)])
        palette = tuple(dict.fromkeys(moved.get(color, color) for color in colors))
        roles = {}
        for color, job in zip(colors, jobs):
            if job and job not in roles:
                roles[job] = moved.get(color, color)
        label = STARTER_LABEL if position == 0 else palettes.MODE_LABELS.get(style, style.title())
        ideas.append(PaletteChoice(f"idea:{style}", label, palette, group=IDEAS, roles=tuple(roles.items())))
    return ideas


def palette_choices(project: Optional[designer.Project], library: Optional[designer.Library], ideas: Sequence[PaletteChoice] = ()) -> list[PaletteChoice]:
    """Every palette the picker offers: My palettes, then Library entries, then the ideas."""
    found: list[PaletteChoice] = []
    if project is not None:
        for palette in project.palettes:
            if palette.colors:
                found.append(PaletteChoice(f"mine:{id(palette)}", palette.name, tuple(palette.hexes()), tuple(palette.names()), MINE))
    if library is not None:
        entries = [entry for entry in library.entries if entry.favorite] + [entry for entry in library.entries if not entry.favorite]
        for entry in entries:
            if entry.palette.colors:
                label = f"★ {entry.palette.name}" if entry.favorite else entry.palette.name
                found.append(
                    PaletteChoice(
                        f"library:{id(entry)}", label, tuple(entry.palette.hexes()), tuple(entry.palette.names()), LIBRARY, entry.favorite
                    )
                )
    found.extend(ideas)
    return found


def mine_key(palette: designer.NamedPalette) -> str:
    return f"mine:{id(palette)}"


def library_key(entry: designer.LibraryEntry) -> str:
    return f"library:{id(entry)}"


def choose_starter(
    project: Optional[designer.Project], library: Optional[designer.Library], ideas: Sequence[PaletteChoice]
) -> str:
    """The key of the palette a newly opened file starts with.

    The palette open on My palette, when it has colors; otherwise the newest Library
    favorite; otherwise the starter palette made from the file. "" when there is nothing.
    """
    if project is not None and project.current.colors:
        return mine_key(project.current)
    if library is not None:
        favorites = [entry for entry in library.favorites() if entry.palette.colors]
        if favorites:
            return library_key(favorites[-1])  # the library adds new entries at the end
    return ideas[0].key if ideas else ""


def starting_variant(file_colors: Sequence[str]) -> int:
    """0 when the file's biggest color is light, 1 when it is dark (`designer.assign_roles`).

    A dark poster keeps a dark background and a light one a light background, so the first
    result looks like the file did.
    """
    if not file_colors:
        return 0
    return 1 if _lightness(normalize_hex(file_colors[0])) < 0.5 else 0


def small_details(shares: Sequence[float], limit: float = 0.03, always: int = 2) -> list[int]:
    """Positions of the main colors that cover so little they hide behind "+ N small details".

    The `always` biggest colors are never hidden.
    """
    return [position for position, share in enumerate(shares) if position >= always and share < limit]


# --------------------------------------------------------------------------------------
# Photos left alone
# --------------------------------------------------------------------------------------

WHOLE_PICTURE = 0.9  # photo areas covering this much of the picture mean "one picture layer"


def _area(box) -> float:
    left, top, right, bottom = box
    return max(0.0, right - left) * max(0.0, bottom - top)


def leave_out(shape, found: Sequence, manual: Sequence, change_photos: bool, known: Optional[Sequence] = None) -> tuple[list, bool]:
    """(boxes to leave alone, whole): the photo areas found plus the boxes drawn by hand.

    `whole` is True when the file is (or has) one picture layer over the whole page (a
    flattened file, a screenshot, a scan, a paper layer): a photo area found covers 90% of
    the picture or more, together or alone, or `known` (where the file says its pictures
    are) is a single picture over 90% of it. Leaving that out would leave nothing to
    recolor, so it is recolored; smaller photo areas are still left alone, and the person
    can draw boxes. Boxes drawn by hand are always kept.
    """
    automatic = [] if change_photos else [tuple(box) for box in found]
    whole = False
    if any(_area(box) >= WHOLE_PICTURE for box in automatic):
        automatic, whole = [box for box in automatic if _area(box) < WHOLE_PICTURE], True
    if automatic and float(designer.boxes_mask(tuple(shape[:2]), automatic).mean()) >= WHOLE_PICTURE:
        automatic, whole = [], True
    elif not change_photos and known is not None and len(known) == 1 and _area(known[0]) >= WHOLE_PICTURE:
        whole = True
    return [tuple(box) for box in manual] + automatic, whole


def photo_line(share: float, count: int, exact: bool, manual: bool, change_photos: bool, whole: bool) -> str:
    """The line about photo areas under "Colors in your file".

    `share` is how much of the picture is left alone (0..1), `count` how many areas,
    `exact` whether the file itself said where its pictures are, `manual` whether the
    person drew boxes, `whole` what `leave_out` returned.
    """
    if count and share >= 0.995:
        return "Your boxes cover the whole picture, so nothing is recolored. Choose More > Forget my boxes to start again."
    if change_photos and not manual:
        return "Photos are recolored too. Drag a box over any area to leave it alone."
    if not count and whole:
        return "Your file is one picture layer, so everything is recolored. Drag a box over any photo to leave it alone."
    if not count:
        return "No photos found, so everything is recolored. Drag a box over any photo to leave it alone."
    areas = "1 area" if count == 1 else f"{count} areas"
    how = "These are the file's own pictures." if exact and not manual else "A best guess: drag a box over anything it missed."
    return f"Photos: {areas} left alone ({share * 100:.0f}%). {how}"


# --------------------------------------------------------------------------------------
# Contrast
# --------------------------------------------------------------------------------------

_READABLE = {
    designer.AAA: "easy to read",
    designer.AA: "readable",
    designer.AA_LARGE: "readable only at large sizes",
    designer.FAIL: "hard to read",
}


def contrast_line(text: str, background: str) -> str:
    """ "Text on background: easy to read (7.2:1)", with the WCAG contrast of the two roles."""
    ratio = contrast_ratio(text, background)
    return f"Text on background: {_READABLE[designer.contrast_grade(ratio)]} ({ratio:.1f}:1)"


# --------------------------------------------------------------------------------------
# What the file is
# --------------------------------------------------------------------------------------

_PIL_MODES = {"RGB": "RGB", "RGBA": "RGB", "CMYK": "CMYK", "L": "Grayscale", "LA": "Grayscale", "P": "Indexed", "I;16": "Grayscale 16-bit"}


@dataclass(frozen=True)
class FileFacts:
    """What the header says about the open file."""

    name: str
    width: int = 0
    height: int = 0
    unit: str = ""  # "" for pixels, "pt" for PDF pages
    layers: Optional[int] = None
    mode: str = ""
    ppi: Optional[float] = None
    pages: int = 1

    def line(self) -> str:
        """ "1000 × 640 · 24 layers · RGB · 300 ppi" (only the parts that are known)."""
        parts = []
        if self.width and self.height:
            parts.append(f"{self.width:,} × {self.height:,}{' ' + self.unit if self.unit else ''}")
        if self.pages > 1:
            parts.append(f"{self.pages} pages")
        if self.layers is not None:
            parts.append(f"{self.layers} {'layer' if self.layers == 1 else 'layers'}")
        if self.mode:
            parts.append(self.mode)
        if self.ppi:
            parts.append(f"{self.ppi:g} ppi")
        return " · ".join(parts)


def read_facts(path, document=None, page: int = 0, rgba=None, name: str = "") -> FileFacts:
    """Facts about a file for the header. Never raises: unknown parts are left out."""
    title = name or (Path(str(path)).name if path else "")
    height, width = (int(rgba.shape[0]), int(rgba.shape[1])) if rgba is not None else (0, 0)
    psd = getattr(document, "_psd", None)
    if psd is not None:
        try:
            layers = sum(1 for layer in psd.descendants() if not layer.is_group())
        except Exception:
            layers = None
        try:
            if getattr(document, "_artboards", None):
                left, top, right, bottom = document._area(page)
                width, height = right - left, bottom - top
            else:
                width, height = psd.size
        except Exception:
            pass
        mode = getattr(getattr(psd, "color_mode", None), "name", "") or ""
        mode = {"GRAYSCALE": "Grayscale", "BITMAP": "Bitmap", "INDEXED": "Indexed", "DUOTONE": "Duotone"}.get(mode, mode)
        depth = getattr(psd, "depth", 8)
        if depth and depth != 8 and mode:
            mode = f"{mode} {depth}-bit"
        return FileFacts(title, int(width), int(height), "", layers, mode, file_export.psd_dpi(psd), getattr(document, "page_count", 1))
    if document is not None and hasattr(document, "page_size"):
        try:
            points = document.page_size(page)
            return FileFacts(title, round(points[0]), round(points[1]), "pt", None, "", None, document.page_count)
        except Exception:
            return FileFacts(title, width, height, pages=getattr(document, "page_count", 1))
    mode, ppi = "", None
    if path and not getattr(document, "page_size", None):
        try:
            from PIL import Image

            with Image.open(str(path)) as image:
                width, height = image.size
                mode = _PIL_MODES.get(image.mode, image.mode)
                found = image.info.get("dpi")
                ppi = file_export._tidy_dpi(float(found[0])) if found and float(found[0]) > 1 else None
        except Exception:
            pass
    elif rgba is not None:
        mode = "RGB"
    return FileFacts(title, int(width), int(height), "", None, mode, ppi, getattr(document, "page_count", 1) if document else 1)


# --------------------------------------------------------------------------------------
# The app links (route A): Photoshop, Illustrator or InDesign writes the file
# --------------------------------------------------------------------------------------


@dataclass
class ExportRequest:
    """Everything a link to a design program needs to write the recolored file."""

    source_path: str  # the file that was opened (never written to)
    source_format: str  # "psd", "ai", "indd"...
    page: int  # page or artboard shown, from 0
    recipe: file_export.Recipe  # main colors, what each becomes (None keeps it), boxes left alone
    target: Path  # a suggested new file beside the original that does not exist yet
    palette: tuple = ()  # the palette's colors, in order
    roles: dict = field(default_factory=dict)  # role name -> HEX, as shown in the preview

    @property
    def mapping(self) -> dict:
        """{original HEX: new HEX} for every main color that changes.

        These are the picture's main colors, found from its pixels. A link that reads the
        document's exact colors should map each exact color to the nearest of these.
        """
        moves = {}
        for color, target in zip(self.recipe.colors, self.recipe.targets):
            if target and normalize_hex(target) != normalize_hex(color):
                moves[normalize_hex(color)] = normalize_hex(target)
        return moves


def link_available(link: Any) -> bool:
    """Whether an app link can be used now: `available()` when it has one, else True."""
    if link is None:
        return False
    check = getattr(link, "available", None)
    if callable(check):
        try:
            return bool(check())
        except Exception:
            return False
    return True


def run_app_link(
    link: Any,
    request: ExportRequest,
    progress: Optional[Callable[[float, str], None]] = None,
    cancelled: Optional[Callable[[], bool]] = None,
):
    """Let a design program write the file. Runs on a worker thread; returns its report.

    The contract of `ProjectPage.set_app_links({format: link})`. A link is one of:

    1. A callable: `link(request, progress, cancelled) -> report`.
    2. An object with `export(request, progress, cancelled) -> report`, and optionally
       `available() -> bool` (checked when links are set and when a file opens) and
       `preview(path, progress, cancelled) -> RGBA numpy array` (page 1 of a file Colorroom
       cannot draw itself, such as .indd).
    3. A `core.bridges` Bridge: `apply(source_path, mapping, save_as=None) -> ApplyReport`;
       it is called as `apply(request.source_path, request.mapping)` and picks its own
       "name - Colorroom A.ext" beside the original.

    `progress(fraction, text)` and `cancelled()` behave as in `ui.tasks.Task`. The link must
    not touch any window. To fail, raise an exception whose text is written for the person
    (an `ImageError` or `BridgeError`). The report needs `summary() -> str`; `text() -> str`
    (the details), `path` or `saved` (the new file, for "Show in folder") and `ok` are used
    when present.
    """
    export = getattr(link, "export", None)
    if callable(export):
        return export(request, progress, cancelled)
    apply = getattr(link, "apply", None)
    if callable(apply):
        return apply(request.source_path, request.mapping)
    if callable(link):
        return link(request, progress, cancelled)
    raise TypeError("An app link must be callable or have export() or apply().")


def report_parts(report: Any) -> tuple[str, str, str, bool]:
    """(summary, details, saved file or "", ok) of an ExportReport or a bridge's ApplyReport."""
    summary = str(report.summary()) if hasattr(report, "summary") else str(report)
    details = ""
    if callable(getattr(report, "text", None)):
        details = str(report.text())
    path = getattr(report, "path", None) or getattr(report, "saved", None) or ""
    ok = getattr(report, "ok", True)
    return summary, details, str(path), bool(ok() if callable(ok) else ok)
