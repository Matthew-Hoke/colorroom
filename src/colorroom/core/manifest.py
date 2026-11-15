"""The two small files Colorroom and its companion plugins pass to each other.

    name.colorroom-manifest.json   written by a plugin ("Send to Colorroom"): every layer of
                                   the open document, the exact colors each one uses, and
                                   where the pictures are. A preview PNG sits beside it.
    name.colorroom-mapping.json    written by Colorroom ("Save for Photoshop"): which
                                   original color becomes which new color. The plugin reads
                                   it ("Apply from Colorroom") and changes the real layers.

Colorroom never writes a Photoshop, Illustrator or InDesign file. The plugin changes colors
inside the program itself, so text stays text, shapes stay shapes, and every hex is exact.
No window code in here.

Route A (Colorroom drives Photoshop through COM, `core/bridges/photoshop_win.py`) produces the
same manifest, with a few extra fields: every color part of a layer with a stable key (`parts`),
the document's resolution, bit depth and layer comps, and a signature of everything except
color per layer, so a read-back can prove that only colors changed. Those fields are optional:
a manifest without them reads exactly as before. (Extension made by Opus 5.5, 2026-11-14.)
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional, Sequence

from .colorspace import normalize_hex
from .index import ImageError

MANIFEST_VERSION = 1
MAPPING_VERSION = 1
MANIFEST_SUFFIX = ".colorroom-manifest.json"
MAPPING_SUFFIX = ".colorroom-mapping.json"
PREVIEW_SUFFIX = ".colorroom-preview.png"

#: Layer kinds whose colors can be changed exactly.
COLOR_KINDS = ("text", "shape", "fill", "adjustment")
#: Layer kinds that hold pictures.
IMAGE_KINDS = ("image", "smart-object")
#: Every kind a manifest may name. Anything else is read as "other".
KINDS = (*COLOR_KINDS, *IMAGE_KINDS, "group", "other")
#: Where a color sits in a layer. "gradient" is a stop of a gradient fill or gradient map,
#: "effect" a layer effect (Color Overlay, Stroke, shadows...), "adjustment" the color of any
#: other adjustment layer (Photo Filter).
WHERE = ("fill", "stroke", "text", "gradient", "effect", "adjustment")
_MAX_FILE = 20_000_000
_MAX_LAYERS = 20_000


class ManifestError(ImageError):
    """A manifest could not be read. The message is written for the person using the app."""


def is_manifest(path) -> bool:
    return str(path).lower().endswith(MANIFEST_SUFFIX)


@dataclass
class ColorPart:
    """One exact color inside a layer, such as one gradient stop or one text run."""

    key: str  # stable inside the layer, e.g. "adj/0/gradientMapClass/gradient/colors/1/color"
    where: str  # one of WHERE
    hex: str
    label: str = ""  # in plain words: "gradient map stop 2 (100%)"
    off: bool = False  # part of an effect or stroke that is switched off: listed, not counted


@dataclass
class ManifestLayer:
    id: int
    name: str
    path: str  # "Header / Logo / Wordmark"
    kind: str  # text, shape, fill, image, smart-object, other
    visible: bool
    locked: bool
    bounds: Optional[tuple[float, float, float, float]]  # fractions of the document, or None
    colors: list[tuple[str, str]]  # (where, hex), each pair once; where is one of WHERE
    parts: list[ColorPart] = field(default_factory=list)  # every color one by one (route A)
    detail: str = ""  # what it is in Photoshop's words: "gradient map", "solid color fill"
    notes: list[str] = field(default_factory=list)  # e.g. a font that is not installed
    signature: str = ""  # everything except color; a read-back compares it
    lock: str = ""  # "all", "pixels", "position"... empty when nothing is locked


@dataclass
class ManifestColor:
    """One exact color of the document and everything that uses it."""

    hex: str
    uses: int = 0  # how many places use it (a layer's fill and its stroke count as two)
    where: set = field(default_factory=set)  # {"fill", "stroke", "text"}
    layers: list[str] = field(default_factory=list)  # paths of the layers that use it

    def describe(self) -> str:
        kinds = " and ".join(sorted(self.where)) or "color"
        names = ", ".join(self.layers[:3]) + (f" and {len(self.layers) - 3} more" if len(self.layers) > 3 else "")
        return f"{self.hex}: {kinds} of {len(self.layers)} {'layer' if len(self.layers) == 1 else 'layers'} ({names})"


@dataclass
class Manifest:
    path: Path
    program: str
    program_version: str
    document: str
    color_mode: str
    width: int
    height: int
    preview: Optional[Path]
    layers: list[ManifestLayer]
    notes: list[str]  # things the plugin could not read, in plain words
    source: Optional[Path] = None  # the document file itself, when it has been saved
    resolution: float = 0.0  # pixels per inch
    bit_depth: int = 8
    layer_comps: int = 0
    color_profile: str = ""

    @property
    def stem(self) -> str:
        """The shared start of the manifest, preview and mapping file names."""
        return self.path.name[: -len(MANIFEST_SUFFIX)]

    def colors(self, include_hidden: bool = False) -> list[ManifestColor]:
        """Every exact color that can be changed, most used first.

        Picture layers list no colors of their own, but their layer effects do, so every
        layer that lists a color counts.
        """
        found: dict[str, ManifestColor] = {}
        for layer in self.layers:
            if not layer.colors or (not layer.visible and not include_hidden):
                continue
            for where, color in layer.colors:
                entry = found.setdefault(color, ManifestColor(color))
                entry.uses += 1
                entry.where.add(where)
                if layer.path not in entry.layers:
                    entry.layers.append(layer.path)
        return sorted(found.values(), key=lambda item: (-item.uses, item.hex))

    def image_boxes(self) -> list[tuple[float, float, float, float]]:
        """Where the document's pictures are: raster layers and smart objects."""
        return [layer.bounds for layer in self.layers if layer.kind in IMAGE_KINDS and layer.visible and layer.bounds is not None]

    def mapping_path(self) -> Path:
        return self.path.with_name(self.stem + MAPPING_SUFFIX)

    def layer(self, identity: int) -> Optional[ManifestLayer]:
        for layer in self.layers:
            if layer.id == identity:
                return layer
        return None


def _fraction_box(raw, width: int, height: int) -> Optional[tuple[float, float, float, float]]:
    try:
        left, top, right, bottom = (float(value) for value in raw)
    except (TypeError, ValueError):
        return None
    if width <= 0 or height <= 0:
        return None
    box = (left / width, top / height, right / width, bottom / height)
    box = tuple(min(1.0, max(0.0, value)) for value in box)
    return box if box[2] - box[0] > 0.001 and box[3] - box[1] > 0.001 else None


def parse_manifest(text: str, path) -> Manifest:
    """Read a manifest from its JSON text. Anything malformed inside it is skipped, not fatal."""
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        raise ManifestError("This is not a Colorroom manifest file. Use Send to Colorroom in the plugin again.") from None
    if not isinstance(data, dict) or data.get("format") != "colorroom-manifest":
        raise ManifestError("This is not a Colorroom manifest file. Use Send to Colorroom in the plugin again.")
    version = data.get("manifest_version")
    if not isinstance(version, int) or version > MANIFEST_VERSION:
        raise ManifestError("This manifest was made by a newer plugin than this Colorroom understands. Update Colorroom.")
    path = Path(path)
    width = int(data.get("width") or 0)
    height = int(data.get("height") or 0)
    layers: list[ManifestLayer] = []
    for item in (data.get("layers") or [])[:_MAX_LAYERS]:
        if not isinstance(item, dict):
            continue
        colors = []
        for entry in item.get("colors") or []:
            if not isinstance(entry, dict) or entry.get("where") not in WHERE:
                continue
            try:
                pair = (entry["where"], normalize_hex(str(entry.get("hex", ""))))
            except ValueError:
                continue
            if pair not in colors:
                colors.append(pair)
        parts = []
        for entry in (item.get("parts") or [])[:5000]:
            if not isinstance(entry, dict) or entry.get("where") not in WHERE or not isinstance(entry.get("key"), str):
                continue
            try:
                part = ColorPart(
                    entry["key"][:300],
                    entry["where"],
                    normalize_hex(str(entry.get("hex", ""))),
                    str(entry.get("label") or "")[:200],
                    bool(entry.get("off", False)),
                )
            except ValueError:
                continue
            parts.append(part)
            if not part.off and (part.where, part.hex) not in colors:
                colors.append((part.where, part.hex))
        kind = str(item.get("kind") or "other")
        try:
            identity = int(item.get("id"))
        except (TypeError, ValueError):
            continue
        name = str(item.get("name") or "Layer")[:200]
        layers.append(
            ManifestLayer(
                identity,
                name,
                str(item.get("path") or name)[:600],
                kind if kind in KINDS else "other",
                bool(item.get("visible", True)),
                bool(item.get("locked", False)),
                _fraction_box(item.get("bounds"), width, height),
                colors,
                parts,
                str(item.get("detail") or "")[:80],
                [str(note)[:300] for note in (item.get("notes") or []) if isinstance(note, str)][:20],
                str(item.get("signature") or "")[:20_000],
                str(item.get("lock") or "")[:80],
            )
        )
    preview = None
    name = data.get("preview")
    if isinstance(name, str) and name and "/" not in name and "\\" not in name:  # a file beside the manifest, nothing else
        candidate = path.with_name(name)
        if candidate.is_file():
            preview = candidate
    notes = [str(note)[:300] for note in (data.get("notes") or []) if isinstance(note, str)][:50]
    source = data.get("source")

    def number(key, kind, default):
        try:
            return kind(data.get(key) or default)
        except (TypeError, ValueError):
            return default

    return Manifest(
        path,
        str(data.get("program") or "the design program")[:60],
        str(data.get("program_version") or "")[:40],
        str(data.get("document") or path.name)[:200],
        str(data.get("color_mode") or "RGB")[:20],
        width,
        height,
        preview,
        layers,
        notes,
        Path(source) if isinstance(source, str) and source else None,
        number("resolution", float, 0.0),
        number("bit_depth", int, 8),
        number("layer_comps", int, 0),
        str(data.get("color_profile") or "")[:200],
    )


def load_manifest(path) -> Manifest:
    target = Path(path)
    try:
        if target.stat().st_size > _MAX_FILE:
            raise ManifestError("This manifest file is too large to be a Colorroom manifest.")
        text = target.read_text(encoding="utf-8", errors="replace")
    except OSError:
        raise ManifestError("That manifest file could not be found or read.") from None
    manifest = parse_manifest(text, target)
    if manifest.preview is None:
        raise ManifestError(
            "The preview picture that belongs to this manifest is missing. Use Send to Colorroom in the plugin again."
        )
    return manifest


def build_mapping(manifest: Manifest, changes: Sequence[tuple[str, str, str]], palette: str = "") -> dict:
    """The mapping to send back: `changes` is (original hex, new hex, role name) per color.

    Colors that would not change are left out, so the plugin touches nothing it need not.
    """
    known = {color.hex for color in manifest.colors(include_hidden=True)}
    rows = []
    for original, new, role in changes:
        original, new = normalize_hex(original), normalize_hex(new)
        if original in known and original != new and not any(row["from"] == original for row in rows):
            rows.append({"from": original, "to": new, "role": role})
    return {
        "format": "colorroom-mapping",
        "mapping_version": MAPPING_VERSION,
        "document": manifest.document,
        "program": manifest.program,
        "palette": palette,
        "created": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "colors": rows,
    }


def save_mapping(manifest: Manifest, changes: Sequence[tuple[str, str, str]], palette: str = "", path=None) -> tuple[Path, int]:
    """Write the mapping file beside the manifest. Returns (where it went, how many colors change)."""
    mapping = build_mapping(manifest, changes, palette)
    target = Path(path) if path is not None else manifest.mapping_path()
    target.write_text(json.dumps(mapping, indent=2, ensure_ascii=False) + "\n", encoding="utf-8", newline="\n")
    return target, len(mapping["colors"])
