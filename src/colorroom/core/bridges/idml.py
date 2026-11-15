"""Route D: read and rewrite the swatches of an InDesign IDML file, without InDesign.

Made by Fable 5.2 (2026-11-13), finished by Opus 5.5 (2026-11-14: listed / in-use swatches,
hidden inks, size-limit fix, live checks). Both parts for the other model to review.

IDML is InDesign's open exchange format: a zip archive of XML files. The swatches live in
``Resources/Graphic.xml`` (``Color``, ``Tint``, ``Gradient`` and ``GradientStop`` elements).
Everything on the pages points at a swatch by its id, so changing one ``ColorValue`` recolors
every frame, stroke, text range, tint and gradient stop that uses it, the same way editing a
swatch does inside InDesign.

    read_swatches(path)                -> IdmlDocument
    rewrite(path, mapping, save_as)    -> ColorReport

What ``rewrite`` promises, and checks before it reports success:

* the original file is never opened for writing;
* only the swatch file is changed, and inside it only the ``ColorValue`` (and, when a swatch
  changes color space, ``Space``) attributes of the swatches that were asked for;
* every other entry of the archive is copied byte for byte, as stored (same compressed bytes,
  same headers, same order);
* ``mimetype`` stays the first entry and stays stored without compression;
* the new file is read back and every changed swatch is compared with what was asked.

Safety: the archive is parsed in memory with size limits, nothing is ever extracted to disk,
entry names are never used as file paths, and XML with a DOCTYPE or entity declarations is
refused. Standard library only; no window code.
"""

from __future__ import annotations

import os
import re
import struct
import tempfile
import zipfile
import zlib
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Mapping, Optional, Sequence, Union
from xml.etree import ElementTree
from xml.parsers import expat

__all__ = [
    "IdmlError",
    "IdmlSwatch",
    "IdmlDocument",
    "SwatchChange",
    "ColorReport",
    "read_swatches",
    "rewrite",
    "normalize_hex",
    "color_to_hex",
    "hex_to_values",
]

MIMETYPE = b"application/vnd.adobe.indesign-idml-package"
DEFAULT_GRAPHIC = "Resources/Graphic.xml"

MAX_ARCHIVE_BYTES = 512 * 1024 * 1024  # the whole .idml file
MAX_ENTRIES = 20_000
MAX_XML_BYTES = 64 * 1024 * 1024  # one XML entry, unpacked
MAX_SCAN_BYTES = 256 * 1024 * 1024  # everything unpacked while counting uses
MAX_NAME_BYTES = 1024

#: ColorOverride values of InDesign's own colors: hidden process inks, Black, Paper, Registration.
_RESERVED = ("Hiddenreserved", "Specialblack", "Specialpaper", "Specialregistration")
#: Swatches InDesign builds in. Their values are fixed, or (Paper) only change the on-screen
#: preview, so they are never rewritten.
BUILTIN_NAMES = ("Black", "Paper", "Registration", "None")

_LOCAL_SIG = b"PK\x03\x04"
_CENTRAL_SIG = b"PK\x01\x02"
_END_SIG = b"PK\x05\x06"
_LOCAL = struct.Struct("<4s5H3L2H")  # 30 bytes
_CENTRAL = struct.Struct("<4s6H3L5H2L")  # 46 bytes
_END = struct.Struct("<4s4H2LH")  # 22 bytes


class IdmlError(Exception):
    """An IDML file could not be read or written. The message is written for the person using the app."""


# --------------------------------------------------------------------------------------------
# Color values
# --------------------------------------------------------------------------------------------


def normalize_hex(color: str) -> str:
    """'#1d4ed8', '1D4ED8' or '#abc' -> '#1D4ED8' / '#AABBCC'."""
    text = str(color).strip().lstrip("#")
    if len(text) == 3:
        text = "".join(ch * 2 for ch in text)
    if len(text) != 6 or any(ch not in "0123456789abcdefABCDEF" for ch in text):
        raise IdmlError(f"'{color}' is not a color like #1D4ED8.")
    return "#" + text.upper()


def _hex_to_rgb(color: str) -> tuple[int, int, int]:
    text = normalize_hex(color)
    return int(text[1:3], 16), int(text[3:5], 16), int(text[5:7], 16)


def _rgb_to_hex(red: float, green: float, blue: float) -> str:
    return "#" + "".join(f"{min(255, max(0, int(round(value)))):02X}" for value in (red, green, blue))


_D50 = (0.96422, 1.0, 0.82521)
_XYZ_TO_RGB = ((3.1338561, -1.6168667, -0.4906146), (-0.9787684, 1.9161415, 0.0334540), (0.0719453, -0.2289914, 1.4052427))
_RGB_TO_XYZ = ((0.4360747, 0.3850649, 0.1430804), (0.2225045, 0.7168786, 0.0606169), (0.0139322, 0.0971045, 0.7141733))


def _lab_to_rgb(lightness: float, a: float, b: float) -> tuple[float, float, float]:
    fy = (lightness + 16.0) / 116.0
    fx, fz = fy + a / 500.0, fy - b / 200.0

    def inverse(value: float) -> float:
        return value**3 if value**3 > 216.0 / 24389.0 else (116.0 * value - 16.0) / (24389.0 / 27.0)

    xyz = [inverse(f) * white for f, white in zip((fx, fy, fz), _D50)]
    out = []
    for row in _XYZ_TO_RGB:
        linear = sum(m * v for m, v in zip(row, xyz))
        linear = min(1.0, max(0.0, linear))
        out.append(255.0 * (12.92 * linear if linear <= 0.0031308 else 1.055 * linear ** (1 / 2.4) - 0.055))
    return out[0], out[1], out[2]


def _rgb_to_lab(red: int, green: int, blue: int) -> tuple[float, float, float]:
    linear = []
    for value in (red, green, blue):
        c = value / 255.0
        linear.append(c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4)
    xyz = [sum(m * v for m, v in zip(row, linear)) for row in _RGB_TO_XYZ]

    def forward(value: float) -> float:
        return value ** (1 / 3) if value > 216.0 / 24389.0 else ((24389.0 / 27.0) * value + 16.0) / 116.0

    fx, fy, fz = (forward(v / white) for v, white in zip(xyz, _D50))
    return 116.0 * fy - 16.0, 500.0 * (fx - fy), 200.0 * (fy - fz)


def color_to_hex(space: str, values: Sequence[float]) -> tuple[Optional[str], bool]:
    """The sRGB hex of a swatch value, and whether it is exact.

    RGB swatches are exact. CMYK is the plain formula with no color profile, so it is only
    close to what InDesign shows; Lab uses the standard D50 math. Anything else gives None.
    """
    space = space.upper()
    try:
        if space == "RGB" and len(values) == 3:
            return _rgb_to_hex(*values), True
        if space == "CMYK" and len(values) == 4:
            c, m, y, k = (min(100.0, max(0.0, float(v))) / 100.0 for v in values)
            return _rgb_to_hex(255 * (1 - c) * (1 - k), 255 * (1 - m) * (1 - k), 255 * (1 - y) * (1 - k)), False
        if space == "LAB" and len(values) == 3:
            return _rgb_to_hex(*_lab_to_rgb(*(float(v) for v in values))), False
    except (TypeError, ValueError):
        pass
    return None, False


def hex_to_values(color: str, space: str) -> list[float]:
    """The swatch values of a hex color in the given space ('RGB', 'CMYK' or 'Lab').

    CMYK uses the plain formula with no color profile (see `color_to_hex`).
    """
    red, green, blue = _hex_to_rgb(color)
    space = space.upper()
    if space == "RGB":
        return [red, green, blue]
    if space == "CMYK":
        r, g, b = red / 255.0, green / 255.0, blue / 255.0
        k = 1.0 - max(r, g, b)
        if k >= 1.0:
            return [0, 0, 0, 100]
        return [round(100 * (1 - channel - k) / (1 - k), 2) for channel in (r, g, b)] + [round(100 * k, 2)]
    if space == "LAB":
        return [round(v, 2) for v in _rgb_to_lab(red, green, blue)]
    raise IdmlError(f"Colorroom cannot write colors in the {space} color space.")


def _format_values(values: Sequence[float]) -> str:
    parts = []
    for value in values:
        number = float(value)
        if number != number or number in (float("inf"), float("-inf")):
            raise IdmlError("A color value is not a number.")
        text = f"{number:.4f}".rstrip("0").rstrip(".")
        parts.append("0" if text in ("-0", "") else text)
    return " ".join(parts)


def _parse_values(text: str) -> list[float]:
    out = []
    for part in (text or "").split():
        try:
            out.append(float(part))
        except ValueError:
            return []
    return out


def _space_label(space: str) -> str:
    return {"RGB": "RGB", "CMYK": "CMYK", "LAB": "Lab", "MIXEDINK": "MixedInk", "HSB": "HSB"}.get(space.upper(), space)


# --------------------------------------------------------------------------------------------
# Results
# --------------------------------------------------------------------------------------------


@dataclass
class IdmlSwatch:
    """One swatch of the document: a color, a tint of a color, or a gradient."""

    id: str  # the IDML id, like "Color/Brand Blue"
    name: str  # "" for an unnamed color
    kind: str  # "color", "tint" or "gradient"
    named: bool = True  # False: an unnamed color (frames and text use it, the Swatches panel does not list it)
    listed: bool = True  # shown in the Swatches panel
    builtin: bool = False  # Black, Paper, Registration, and InDesign's hidden process inks
    editable: bool = True
    model: str = ""  # "process", "spot", "registration", "mixed"
    space: str = ""  # "RGB", "CMYK", "Lab", "MixedInk"
    values: list = field(default_factory=list)
    hex: Optional[str] = None
    hex_exact: bool = False  # False: worked out without a color profile, close but not exact
    in_use: bool = False  # something on a page, in text or in a style shows it (directly or through a tint or gradient)
    base: str = ""  # tints: the id of the color it is a tint of
    tint: Optional[float] = None  # tints: percent
    gradient_type: str = ""  # gradients: "linear" or "radial"
    stops: list = field(default_factory=list)  # gradients: {"location", "midpoint", "color", "hex"}
    uses: int = 0  # places on pages and in text that point at it
    style_uses: int = 0  # paragraph, character, object, cell and table styles that point at it
    stop_uses: int = 0  # gradient stops that point at it
    tint_uses: int = 0  # tints built on it

    @property
    def used(self) -> bool:
        return bool(self.uses or self.style_uses or self.stop_uses or self.tint_uses)

    def to_dict(self) -> dict:
        data = asdict(self)
        data["used"] = self.used
        return data


@dataclass
class IdmlDocument:
    path: Path
    graphic_entry: str
    dom_version: str
    swatches: list[IdmlSwatch]
    spreads: int
    master_spreads: int
    stories: int
    notes: list[str] = field(default_factory=list)

    def swatch(self, key: str) -> Optional[IdmlSwatch]:
        """Find a swatch by id or by name."""
        for item in self.swatches:
            if item.id == key:
                return item
        for item in self.swatches:
            if item.name and item.name == key:
                return item
        return None

    def colors(self) -> list[IdmlSwatch]:
        return [item for item in self.swatches if item.kind == "color"]

    def to_dict(self) -> dict:
        return {
            "path": str(self.path),
            "graphic_entry": self.graphic_entry,
            "dom_version": self.dom_version,
            "spreads": self.spreads,
            "master_spreads": self.master_spreads,
            "stories": self.stories,
            "notes": list(self.notes),
            "swatches": [item.to_dict() for item in self.swatches],
        }


@dataclass
class SwatchChange:
    """One swatch that was changed, and what the read-back found."""

    id: str
    name: str
    model: str
    old_hex: Optional[str]
    wanted_hex: Optional[str]
    got_hex: Optional[str] = None
    space_before: str = ""
    space_after: str = ""
    values_before: list = field(default_factory=list)
    values_after: list = field(default_factory=list)
    verified: bool = False  # read back from the saved file and found as written
    exact: bool = False  # the read-back hex equals the wanted hex
    note: str = ""


@dataclass
class ColorReport:
    """What an apply or rewrite did. `ok` is only True when the read-back passed."""

    route: str  # "idml" or "indesign"
    source: str
    saved: str = ""
    changed: list[SwatchChange] = field(default_factory=list)
    skipped: list[dict] = field(default_factory=list)  # {"what": ..., "why": ...}
    follows: list[dict] = field(default_factory=list)  # tints and gradients that changed with their color
    notes: list[str] = field(default_factory=list)
    checks: dict = field(default_factory=dict)  # name -> True/False for each check that ran
    extra_files: dict = field(default_factory=dict)  # for example {"idml": path}
    ok: bool = False

    @property
    def verified(self) -> int:
        return sum(1 for change in self.changed if change.verified)

    @property
    def exact(self) -> int:
        return sum(1 for change in self.changed if change.exact)

    def summary(self) -> str:
        """A short report in plain words, for the person using the app."""
        count = len(self.changed)
        lines = [f"{count} {'swatch' if count == 1 else 'swatches'} changed."]
        if count:
            if self.exact == count:
                lines.append("Checked: every color is exact.")
            else:
                lines.append(f"Checked: {self.exact} of {count} are exact.")
                for change in self.changed:
                    if not change.exact:
                        lines.append(f"  {change.name or change.id}: wanted {change.wanted_hex}, got {change.got_hex}. {change.note}".rstrip())
        if self.follows:
            lines.append(f"{len(self.follows)} tints and gradients changed with their colors.")
        for item in self.skipped:
            lines.append(f"Skipped {item['what']}: {item['why']}")
        lines.extend(self.notes)
        if not self.ok:
            lines.append("The check did not pass. Do not rely on this file.")
        return "\n".join(lines)

    def to_dict(self) -> dict:
        data = asdict(self)
        data["verified"] = self.verified
        data["exact"] = self.exact
        return data


# --------------------------------------------------------------------------------------------
# The zip archive, parsed by hand so entries can be copied exactly as stored
# --------------------------------------------------------------------------------------------


@dataclass
class _Entry:
    name: str
    raw_name: bytes
    flags: int
    method: int
    crc: int
    packed_size: int
    size: int
    header_offset: int
    central: tuple  # the unpacked central directory record
    central_tail: bytes  # name + extra + comment, as stored
    data_offset: int = 0
    span_end: int = 0  # where this entry's bytes end in the file (the next entry or the directory)


@dataclass
class _Archive:
    data: bytes
    entries: list[_Entry]  # in central directory order
    comment: bytes
    directory_offset: int

    def by_name(self, name: str) -> Optional[_Entry]:
        for entry in self.entries:
            if entry.name == name:
                return entry
        return None

    def in_file_order(self) -> list[_Entry]:
        return sorted(self.entries, key=lambda entry: entry.header_offset)

    def span(self, entry: _Entry) -> bytes:
        return self.data[entry.header_offset : entry.span_end]

    def read(self, entry: _Entry, limit: Optional[int] = None) -> bytes:
        """Unpack one entry in memory, never past `limit` bytes (default MAX_XML_BYTES)."""
        limit = MAX_XML_BYTES if limit is None else limit
        if entry.size > limit:
            raise IdmlError(f"'{entry.name}' inside the file is too large to read safely.")
        packed = self.data[entry.data_offset : entry.data_offset + entry.packed_size]
        if entry.method == 0:
            out = packed
        elif entry.method == 8:
            unpacker = zlib.decompressobj(-15)
            try:
                out = unpacker.decompress(packed, limit + 1)
            except zlib.error:
                raise IdmlError(f"'{entry.name}' inside the file is damaged.") from None
            if len(out) > limit or unpacker.unconsumed_tail:
                raise IdmlError(f"'{entry.name}' inside the file is too large to read safely.")
            if not unpacker.eof:
                raise IdmlError(f"'{entry.name}' inside the file is damaged.")
        else:
            raise IdmlError(f"'{entry.name}' uses a kind of compression Colorroom does not read.")
        if len(out) != entry.size or (zlib.crc32(out) & 0xFFFFFFFF) != entry.crc:
            raise IdmlError(f"'{entry.name}' inside the file is damaged.")
        return out


def _safe_name(name: str) -> bool:
    if not name or name.startswith(("/", "\\")) or "\\" in name or "\x00" in name or ":" in name:
        return False
    return all(part not in ("", ".", "..") for part in name.rstrip("/").split("/"))


def _open_archive(path: Path) -> _Archive:
    try:
        size = path.stat().st_size
    except OSError:
        raise IdmlError("The file could not be found.") from None
    if size > MAX_ARCHIVE_BYTES:
        raise IdmlError("This IDML file is too large for Colorroom to open safely.")
    try:
        with open(path, "rb") as handle:
            data = handle.read(MAX_ARCHIVE_BYTES + 1)
    except OSError:
        raise IdmlError("The file could not be read.") from None
    if len(data) > MAX_ARCHIVE_BYTES:
        raise IdmlError("This IDML file is too large for Colorroom to open safely.")
    return _parse_archive(data)


def _parse_archive(data: bytes) -> _Archive:
    not_idml = "This is not an IDML file. In InDesign use File > Export and choose InDesign Markup (IDML)."
    end_at = data.rfind(_END_SIG, max(0, len(data) - 22 - 65535))
    if end_at < 0 or end_at + _END.size > len(data):
        raise IdmlError(not_idml)
    _, disk, start_disk, here, total, directory_size, directory_offset, comment_size = _END.unpack_from(data, end_at)
    if disk or start_disk or here != total:
        raise IdmlError("This IDML file is split over several parts, which Colorroom does not read.")
    if total == 0xFFFF or directory_size == 0xFFFFFFFF or directory_offset == 0xFFFFFFFF:
        raise IdmlError("This IDML file uses the large-file zip format, which Colorroom does not read.")
    if total > MAX_ENTRIES:
        raise IdmlError("This IDML file holds too many parts for Colorroom to open safely.")
    if end_at + _END.size + comment_size != len(data) or directory_offset + directory_size != end_at:
        raise IdmlError("This IDML file has extra data in it that Colorroom does not understand, so it was left alone.")
    comment = data[end_at + _END.size :]

    entries: list[_Entry] = []
    seen: set[str] = set()
    at = directory_offset
    for _ in range(total):
        if at + _CENTRAL.size > end_at:
            raise IdmlError("This IDML file is damaged.")
        record = _CENTRAL.unpack_from(data, at)
        (sig, _made, _need, flags, method, _time, _date, crc, packed, size, name_size, extra_size, note_size, disk_no, _ia, _ea, offset) = record
        if sig != _CENTRAL_SIG:
            raise IdmlError("This IDML file is damaged.")
        tail_end = at + _CENTRAL.size + name_size + extra_size + note_size
        if tail_end > end_at or name_size > MAX_NAME_BYTES:
            raise IdmlError("This IDML file is damaged.")
        raw_name = data[at + _CENTRAL.size : at + _CENTRAL.size + name_size]
        try:
            name = raw_name.decode("utf-8" if flags & 0x800 else "cp437")
        except UnicodeDecodeError:
            raise IdmlError("This IDML file is damaged.") from None
        if not _safe_name(name) or name in seen:
            raise IdmlError("This IDML file holds a part with an unsafe or repeated name, so it was left alone.")
        if flags & 0x1 or flags & 0x40:
            raise IdmlError("This IDML file is password protected, which Colorroom does not read.")
        if disk_no or 0xFFFFFFFF in (packed, size, offset):
            raise IdmlError("This IDML file uses the large-file zip format, which Colorroom does not read.")
        seen.add(name)
        entries.append(_Entry(name, raw_name, flags, method, crc, packed, size, offset, record, data[at + _CENTRAL.size : tail_end]))
        at = tail_end
    if at != end_at:
        raise IdmlError("This IDML file is damaged.")

    ordered = sorted(entries, key=lambda entry: entry.header_offset)
    for index, entry in enumerate(ordered):
        start = entry.header_offset
        if start + _LOCAL.size > directory_offset:
            raise IdmlError("This IDML file is damaged.")
        sig, _need, _flags, method, _time, _date, _crc, _packed, _size, name_size, extra_size = _LOCAL.unpack_from(data, start)
        if sig != _LOCAL_SIG or method != entry.method:
            raise IdmlError("This IDML file is damaged.")
        if data[start + _LOCAL.size : start + _LOCAL.size + name_size] != entry.raw_name:
            raise IdmlError("This IDML file holds a part whose two names differ, so it was left alone.")
        entry.data_offset = start + _LOCAL.size + name_size + extra_size
        entry.span_end = ordered[index + 1].header_offset if index + 1 < len(ordered) else directory_offset
        if entry.data_offset + entry.packed_size > entry.span_end:
            raise IdmlError("This IDML file holds parts that overlap, so it was left alone.")
    if ordered and ordered[0].header_offset != 0:
        raise IdmlError("This IDML file has extra data in it that Colorroom does not understand, so it was left alone.")

    first = ordered[0] if ordered else None
    if first is None or first.name != "mimetype" or first.method != 0:
        raise IdmlError(not_idml)
    archive = _Archive(data, entries, comment, directory_offset)
    if archive.read(first, 1024).strip() != MIMETYPE:
        raise IdmlError(not_idml)
    return archive


def _build_archive(archive: _Archive, replaced: Mapping[str, bytes]) -> bytes:
    """The archive again, with some entries given new content and all others copied as stored."""
    out = bytearray()
    new_offsets: dict[str, int] = {}
    new_records: dict[str, tuple] = {}
    for entry in archive.in_file_order():
        new_offsets[entry.name] = len(out)
        if entry.name not in replaced:
            out += archive.span(entry)
            continue
        content = replaced[entry.name]
        if entry.method == 0:
            packed = content
        else:
            packer = zlib.compressobj(6, zlib.DEFLATED, -15)
            packed = packer.compress(content) + packer.flush()
        crc = zlib.crc32(content) & 0xFFFFFFFF
        flags = entry.flags & 0x800  # keep only "names are UTF-8"; sizes go in the header, not after the data
        record = entry.central
        local_extra_size = _LOCAL.unpack_from(archive.data, entry.header_offset)[10]
        local_extra = archive.data[entry.data_offset - local_extra_size : entry.data_offset]
        out += _LOCAL.pack(_LOCAL_SIG, record[2], flags, entry.method, record[5], record[6], crc, len(packed), len(content), len(entry.raw_name), len(local_extra))
        out += entry.raw_name + local_extra + packed
        new_records[entry.name] = (flags, crc, len(packed), len(content))

    directory_offset = len(out)
    for entry in archive.entries:
        record = list(entry.central)
        if entry.name in new_records:
            record[3], record[7], record[8], record[9] = new_records[entry.name]
        record[16] = new_offsets[entry.name]
        out += _CENTRAL.pack(*record) + entry.central_tail
    directory_size = len(out) - directory_offset
    if len(out) + _END.size + len(archive.comment) > 0xFFFFFFFE:
        raise IdmlError("The new file would be too large.")
    out += _END.pack(_END_SIG, 0, 0, len(archive.entries), len(archive.entries), directory_size, directory_offset, len(archive.comment))
    out += archive.comment
    return bytes(out)


# --------------------------------------------------------------------------------------------
# The swatch XML
# --------------------------------------------------------------------------------------------

_ATTRIBUTE = r"""\s+[^\s=<>/"']+\s*=\s*(?:"[^"<]*"|'[^'<]*')"""
_COLOR_TAG = re.compile(r"<Color((?:" + _ATTRIBUTE + r")*)\s*(/?)>")
_REFERENCE = re.compile(rb"""\s([A-Za-z][\w:.-]*)\s*=\s*"((?:Color|Tint|Gradient|MixedInk|MixedInkGroup)/[^"<]*)\"""")
_XML_ENTITIES = {"&lt;": "<", "&gt;": ">", "&quot;": '"', "&apos;": "'", "&amp;": "&"}
_XML_ENTITY = re.compile(r"&(?:lt|gt|quot|apos|amp|#\d+|#x[0-9A-Fa-f]+);")


def _unescape(text: str) -> str:
    def swap(match: re.Match) -> str:
        token = match.group(0)
        if token in _XML_ENTITIES:
            return _XML_ENTITIES[token]
        try:
            return chr(int(token[3:-1], 16) if token[2] in "xX" else int(token[2:-1]))
        except (ValueError, OverflowError):
            return token

    return _XML_ENTITY.sub(swap, text)


def _parse_xml(data: bytes, name: str) -> ElementTree.Element:
    """Parse one XML entry. A DOCTYPE or entity declaration is refused (IDML never has one).

    The refusal is made by the XML reader itself (expat, which works out the encoding the same
    way ElementTree does), so it cannot be dodged by saving the entry in another encoding: a
    byte search for "<!ENTITY" misses UTF-16, where the letters are spread over two bytes each.
    """
    refused = IdmlError(f"'{name}' inside the file holds declarations Colorroom does not accept, so it was left alone.")

    def refuse(*_args) -> None:
        raise refused

    scanner = expat.ParserCreate()
    scanner.StartDoctypeDeclHandler = refuse
    scanner.EntityDeclHandler = refuse
    try:
        scanner.Parse(data, True)
    except IdmlError:
        raise
    except (expat.ExpatError, ValueError):  # ValueError: an encoding expat does not read
        raise IdmlError(f"'{name}' inside the file is not valid XML.") from None
    try:
        return ElementTree.fromstring(data)
    except ElementTree.ParseError:
        raise IdmlError(f"'{name}' inside the file is not valid XML.") from None


def _local(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def _graphic_entry(archive: _Archive) -> _Entry:
    name = DEFAULT_GRAPHIC
    designmap = archive.by_name("designmap.xml")
    if designmap is not None:
        root = _parse_xml(archive.read(designmap), designmap.name)
        for element in root.iter():
            if _local(element.tag) == "Graphic" and element.get("src"):
                name = element.get("src")
                break
    entry = archive.by_name(name)
    if entry is None:
        raise IdmlError("This IDML file has no swatch list (Resources/Graphic.xml).")
    return entry


def _swatches_from_xml(data: bytes, name: str) -> tuple[str, list[IdmlSwatch]]:
    root = _parse_xml(data, name)
    swatches: list[IdmlSwatch] = []
    by_id: dict[str, IdmlSwatch] = {}
    for element in root.iter():
        tag = _local(element.tag)
        self_id = element.get("Self", "")
        raw_name = element.get("Name", "")
        # "$ID/Black" is a built-in name; "$ID/" alone is an unnamed color.
        display = raw_name[4:] if raw_name.startswith("$ID/") else raw_name
        if tag == "Color":
            space = _space_label(element.get("Space", ""))
            values = _parse_values(element.get("ColorValue", ""))
            hex_value, exact = color_to_hex(space, values)
            model = {"Process": "process", "Spot": "spot", "Registration": "registration", "MixedInkModel": "mixed"}.get(element.get("Model", ""), element.get("Model", "").lower())
            builtin = (display in BUILTIN_NAMES and self_id == f"Color/{display}") or element.get("ColorOverride", "") in _RESERVED
            item = IdmlSwatch(
                id=self_id,
                name=display,
                kind="color",
                named=bool(display),
                listed=element.get("Visible", "true") != "false",
                builtin=builtin,
                editable=element.get("ColorEditable", "true") != "false" and not builtin,
                model=model,
                space=space,
                values=values,
                hex=hex_value,
                hex_exact=exact,
            )
        elif tag == "Tint":
            try:
                tint = float(element.get("TintValue", ""))
            except ValueError:
                tint = None
            item = IdmlSwatch(id=self_id, name=display, kind="tint", named=bool(display), listed=element.get("Visible", "true") != "false", base=element.get("BaseColor", ""), tint=tint)
        elif tag == "Gradient":
            item = IdmlSwatch(id=self_id, name=display, kind="gradient", named=bool(display), listed=element.get("Visible", "true") != "false", gradient_type=element.get("Type", "").lower())
            for stop in element:
                if _local(stop.tag) != "GradientStop":
                    continue
                item.stops.append(
                    {
                        "location": _parse_values(stop.get("Location", ""))[:1],
                        "midpoint": _parse_values(stop.get("Midpoint", ""))[:1],
                        "color": stop.get("StopColor", ""),
                        "hex": None,
                    }
                )
            for stop in item.stops:
                stop["location"] = stop["location"][0] if stop["location"] else None
                stop["midpoint"] = stop["midpoint"][0] if stop["midpoint"] else None
        else:
            continue
        swatches.append(item)
        by_id[self_id] = item

    for item in swatches:
        if item.kind == "tint":
            base = by_id.get(item.base)
            if base is not None:
                base.tint_uses += 1
                item.space, item.model = base.space, base.model
                item.hex, item.values = _tint_hex(base, item.tint)
        elif item.kind == "gradient":
            for stop in item.stops:
                target = by_id.get(stop["color"])
                if target is not None:
                    target.stop_uses += 1
                    stop["hex"] = target.hex
    for item in swatches:  # a tint used as a gradient stop gets its hex after the tints are known
        if item.kind == "gradient":
            for stop in item.stops:
                target = by_id.get(stop["color"])
                stop["hex"] = target.hex if target is not None else None
    return root.get("DOMVersion", ""), swatches


def _tint_hex(base: IdmlSwatch, tint: Optional[float]) -> tuple[Optional[str], list]:
    """A tint's color: the base mixed toward white (close to InDesign's result, not exact)."""
    if tint is None or base.hex is None:
        return None, []
    amount = min(100.0, max(0.0, tint)) / 100.0
    if base.space == "CMYK" and len(base.values) == 4:
        values = [round(v * amount, 4) for v in base.values]
        return color_to_hex("CMYK", values)[0], values
    red, green, blue = _hex_to_rgb(base.hex)
    return _rgb_to_hex(*(255 - (255 - channel) * amount for channel in (red, green, blue))), []


def _count_uses(archive: _Archive, swatches: list[IdmlSwatch], skip: str, notes: list[str]) -> tuple[int, int, int]:
    by_id = {item.id: item for item in swatches}
    spreads = masters = stories = 0
    budget = MAX_SCAN_BYTES
    cut_short = False
    for entry in archive.entries:
        name = entry.name
        if name.startswith("Spreads/"):
            spreads += 1
        elif name.startswith("MasterSpreads/"):
            masters += 1
        elif name.startswith("Stories/"):
            stories += 1
        if name == skip or not name.lower().endswith(".xml"):
            continue
        in_styles = name.startswith("Resources/")
        if not (in_styles or name.startswith(("Spreads/", "MasterSpreads/", "Stories/"))):
            continue
        if entry.size > min(budget, MAX_XML_BYTES):
            cut_short = True
            continue
        data = archive.read(entry)
        budget -= len(data)
        for match in _REFERENCE.finditer(data):
            target = by_id.get(_unescape(match.group(2).decode("utf-8", "replace")))
            if target is None:
                continue
            if in_styles:
                target.style_uses += 1
            else:
                target.uses += 1
    if cut_short:
        notes.append("The document is very large, so the count of where each swatch is used is not complete.")
    return spreads, masters, stories


def _mark_in_use(swatches: list[IdmlSwatch]) -> None:
    """A swatch is in use when pages, text or styles point at it, or at a tint or gradient
    built on it that is in use. Listed gradients count as in use (the user may apply them)."""
    by_id = {item.id: item for item in swatches}
    pending = [item for item in swatches if item.uses or item.style_uses or (item.kind == "gradient" and item.listed)]
    while pending:
        item = pending.pop()
        if item.in_use:
            continue
        item.in_use = True
        parts = [item.base] if item.kind == "tint" else [stop["color"] for stop in item.stops] if item.kind == "gradient" else []
        pending.extend(by_id[part] for part in parts if part in by_id and not by_id[part].in_use)


def _read(archive: _Archive, path: Path) -> IdmlDocument:
    entry = _graphic_entry(archive)
    dom_version, swatches = _swatches_from_xml(archive.read(entry), entry.name)
    notes: list[str] = []
    spreads, masters, stories = _count_uses(archive, swatches, entry.name, notes)
    _mark_in_use(swatches)
    if any(item.kind == "color" and item.hex is not None and not item.hex_exact for item in swatches):
        notes.append("CMYK and Lab swatches are shown as close screen colors, worked out without the document's color profile. InDesign may show a slightly different hex.")
    return IdmlDocument(path, entry.name, dom_version, swatches, spreads, masters, stories, notes)


def read_swatches(path: Union[str, os.PathLike]) -> IdmlDocument:
    """Every swatch of an IDML file, with where it is used. Nothing is written anywhere."""
    path = Path(path)
    return _read(_open_archive(path), path)


# --------------------------------------------------------------------------------------------
# Rewriting
# --------------------------------------------------------------------------------------------


def _set_attribute(tag: str, name: str, value: str) -> str:
    """The tag text with one attribute's value replaced. The attribute must already be there."""
    pattern = re.compile(r"(\s" + re.escape(name) + r"\s*=\s*)(\"[^\"<]*\"|'[^'<]*')")
    match = pattern.search(tag)
    if match is None:
        raise IdmlError("A swatch in this file is stored in a way Colorroom does not understand, so nothing was changed.")
    return tag[: match.start(2)] + '"' + value + '"' + tag[match.end(2) :]


def _target(value) -> tuple[Optional[str], Optional[str], Optional[list]]:
    """A mapping value as (hex, space, values). A plain hex, or {"hex":..} / {"space":.., "values":[..]}."""
    if isinstance(value, Mapping):
        hex_value = normalize_hex(value["hex"]) if value.get("hex") else None
        space = _space_label(str(value["space"])) if value.get("space") else None
        values = [float(v) for v in value["values"]] if value.get("values") is not None else None
        if values is not None and space is None:
            raise IdmlError("A new color given as values also needs its color space.")
        if values is not None and len(values) != {"RGB": 3, "CMYK": 4, "Lab": 3}.get(space, -1):
            raise IdmlError(f"A new color has the wrong number of values for {space}.")
        if hex_value is None and values is None:
            raise IdmlError("A new color needs a hex or values.")
        return hex_value, space, values
    return normalize_hex(value), None, None


def rewrite(
    path: Union[str, os.PathLike],
    mapping: Mapping,
    save_as: Union[str, os.PathLike],
    *,
    space: str = "keep",
    include_spot: bool = True,
    known_hex: Optional[Mapping[str, str]] = None,
    overwrite: bool = False,
) -> ColorReport:
    """Write a copy of an IDML file with new swatch colors, and check the copy.

    mapping     {old: new}. `old` is a hex ("#1D4ED8": every swatch and unnamed color of that
                color), or a swatch name or id ("Brand Blue", "Color/Brand Blue"). `new` is a
                hex, or {"space": "CMYK", "values": [c, m, y, k]} for exact ink values (an
                optional "hex" beside it says what that should look like).
    space       "keep": each swatch stays in its color space; a hex for a CMYK swatch becomes
                CMYK values with the plain formula (no color profile; reported as not exact).
                "rgb": changed swatches become RGB, so every hex is exact.
    known_hex   {swatch name or id: hex} when the true screen colors are known (for example
                from InDesign through `indesign_win.read`). Used to match `old` hex colors of
                CMYK and Lab swatches instead of the profile-less guess.
    """
    source = Path(path)
    target_path = Path(save_as)
    if space not in ("keep", "rgb"):
        raise IdmlError("space must be 'keep' or 'rgb'.")
    if target_path.suffix.lower() != ".idml":
        raise IdmlError("The new file needs a name that ends in .idml.")
    if target_path.resolve() == source.resolve():
        raise IdmlError("Colorroom never writes over the original file. Choose another name.")
    if target_path.exists() and not overwrite:
        raise IdmlError(f"'{target_path.name}' already exists. Choose another name.")
    if not target_path.parent.is_dir():
        raise IdmlError("The folder for the new file does not exist.")

    archive = _open_archive(source)
    before = _read(archive, source)
    entry = archive.by_name(before.graphic_entry)
    raw = archive.read(entry)
    bom = b"\xef\xbb\xbf" if raw.startswith(b"\xef\xbb\xbf") else b""
    try:
        text = raw[len(bom) :].decode("utf-8")
    except UnicodeDecodeError:
        raise IdmlError("The swatch list of this file is not UTF-8 text, so it was left alone.") from None

    colors = before.colors()
    tags = list(_COLOR_TAG.finditer(text))
    tag_ids = []
    for match in tags:
        found = re.search(r"""\sSelf\s*=\s*(?:"([^"<]*)"|'([^'<]*)')""", match.group(0))
        tag_ids.append(_unescape(found.group(1) if found.group(1) is not None else found.group(2)) if found else None)
    if tag_ids != [item.id for item in colors]:
        raise IdmlError("The swatch list of this file is stored in a way Colorroom does not understand, so nothing was changed.")

    report = ColorReport(route="idml", source=str(source))
    known = {str(key): normalize_hex(value) for key, value in (known_hex or {}).items()}

    def shown_hex(item: IdmlSwatch) -> Optional[str]:
        return known.get(item.id) or (known.get(item.name) if item.name else None) or item.hex

    # Which swatch gets which new color.
    wanted: dict[str, tuple] = {}
    for old, new in mapping.items():
        old_text = str(old).strip()
        try:
            new_hex, new_space, new_values = _target(new)
        except (KeyError, TypeError, ValueError):
            raise IdmlError(f"The new color for {old_text} is not a color.") from None
        is_hex = old_text.startswith("#")
        if is_hex:
            old_hex = normalize_hex(old_text)
            # Not InDesign's hidden inks, and not unnamed colors that nothing shows.
            matches = [item for item in colors if shown_hex(item) == old_hex and not (item.named and not item.listed) and (item.named or item.in_use)]
        else:
            matches = [item for item in colors if item.id == old_text] or [item for item in colors if item.name and item.name == old_text]
        if not matches:
            others = [item for item in before.swatches if item.kind != "color" and ((is_hex and item.hex == old_hex) or (not is_hex and old_text in (item.id, item.name)))]
            if others and others[0].kind == "tint":
                base = before.swatch(others[0].base)
                why = f"it is a tint of '{base.name if base else others[0].base}'. Change that color and the tint follows."
            elif others:
                why = "it is a gradient. Change the colors of its stops and the gradient follows."
            else:
                why = "no swatch in the file has this color."
            report.skipped.append({"what": old_text, "why": why})
            continue
        for item in matches:
            label = f"'{item.name}'" if item.name else f"an unnamed color ({shown_hex(item)})"
            if item.builtin or not item.editable:
                report.skipped.append({"what": label, "why": "it is built into InDesign and cannot be changed. Give the frames and text a new swatch in InDesign instead."})
            elif item.model == "mixed" or item.space not in ("RGB", "CMYK", "Lab"):
                report.skipped.append({"what": label, "why": f"it is a {item.space or item.model} swatch, which Colorroom does not change."})
            elif item.model == "spot" and not include_spot:
                report.skipped.append({"what": label, "why": "it is a spot color and spot colors were left alone."})
            elif item.id in wanted:
                report.skipped.append({"what": f"{old_text} for {label}", "why": "this swatch was already given a new color by another line of the mapping."})
            else:
                wanted[item.id] = (item, new_hex, new_space, new_values)

    # Edit the tags, last first so earlier positions stay valid.
    pending: list[SwatchChange] = []
    for match, item in reversed(list(zip(tags, colors))):
        if item.id not in wanted:
            continue
        _, new_hex, new_space, new_values = wanted[item.id]
        out_space = new_space or ("RGB" if space == "rgb" else item.space)
        if new_values is None:
            new_values = hex_to_values(new_hex, out_space)
        expected_hex, exact_math = color_to_hex(out_space, new_values)
        tag = _set_attribute(match.group(0), "ColorValue", _format_values(new_values))
        if out_space != item.space:
            tag = _set_attribute(tag, "Space", out_space.replace("Lab", "LAB") if out_space == "Lab" else out_space)
        text = text[: match.start()] + tag + text[match.end() :]
        change = SwatchChange(
            id=item.id,
            name=item.name,
            model=item.model,
            old_hex=shown_hex(item),
            wanted_hex=new_hex or expected_hex,
            space_before=item.space,
            space_after=out_space,
            values_before=list(item.values),
            values_after=[float(v) for v in new_values],
        )
        notes = []
        if out_space != item.space:
            notes.append(f"Converted from {item.space} to {out_space}.")
        if not exact_math:
            notes.append(f"Stored as {out_space} values worked out without a color profile, so InDesign may show a slightly different hex.")
        if item.model == "spot":
            notes.append("This is a spot color: the ink keeps its name, so tell your printer.")
        change.note = " ".join(notes)
        pending.append(change)
    pending.reverse()
    report.changed = pending

    if not pending:
        report.notes.append("Nothing was changed, so no file was written.")
        return report

    new_raw = bom + text.encode("utf-8")
    if len(new_raw) > MAX_XML_BYTES:
        raise IdmlError("The swatch list would be too large.")
    _parse_xml(new_raw, entry.name)
    output = _build_archive(archive, {entry.name: new_raw})

    handle_number, temporary_name = tempfile.mkstemp(prefix=f".{target_path.stem}-", suffix=".colorroom-part", dir=target_path.parent)
    os.close(handle_number)  # a new, unique name: never an existing file
    temporary = Path(temporary_name)
    try:
        with open(temporary, "wb") as handle:
            handle.write(output)
        _verify(archive, before, temporary, entry.name, new_raw, report)
        if report.ok:
            os.replace(temporary, target_path)
            report.saved = str(target_path)
        else:
            report.notes.append("The new file did not pass its check, so it was not kept.")
    finally:
        if temporary.exists():
            try:
                temporary.unlink()
            except OSError:
                pass

    changed_ids = {change.id for change in report.changed}
    for item in before.swatches:
        if item.kind == "tint" and item.base in changed_ids:
            report.follows.append({"kind": "tint", "name": item.name, "id": item.id, "because": item.base})
        elif item.kind == "gradient" and any(stop["color"] in changed_ids for stop in item.stops):
            report.follows.append({"kind": "gradient", "name": item.name, "id": item.id, "because": [stop["color"] for stop in item.stops if stop["color"] in changed_ids]})
    report.notes.append("Open the new .idml in InDesign and use File > Save As to get an .indd file.")
    return report


def _verify(original: _Archive, before: IdmlDocument, written: Path, edited: str, new_raw: bytes, report: ColorReport) -> None:
    """Read the written file back with fresh eyes and fill in the report's checks."""
    checks = report.checks
    result = _open_archive(written)
    names_before = [entry.name for entry in original.in_file_order()]
    names_after = [entry.name for entry in result.in_file_order()]
    checks["same parts in the same order"] = names_before == names_after and [e.name for e in original.entries] == [e.name for e in result.entries]
    first = result.in_file_order()[0]
    checks["mimetype is first and not compressed"] = first.name == "mimetype" and first.method == 0 and first.header_offset == 0 and result.read(first, 1024) == original.read(original.in_file_order()[0], 1024)

    untouched = True
    if checks["same parts in the same order"]:
        for old in original.entries:
            if old.name == edited:
                continue
            new = result.by_name(old.name)
            if original.span(old) != result.span(new) or old.central[:16] != new.central[:16] or old.central_tail != new.central_tail:
                untouched = False
                break
    else:
        untouched = False
    checks["every other part is byte for byte the same"] = untouched
    checks["the swatch list holds exactly the new text"] = result.read(result.by_name(edited)) == new_raw

    # A second opinion from Python's own zip reader.
    second = True
    try:
        with zipfile.ZipFile(written) as bundle:
            infos = bundle.infolist()
            second = infos[0].filename == "mimetype" and infos[0].compress_type == zipfile.ZIP_STORED and bundle.testzip() is None
            second = second and bundle.read(edited) == new_raw
    except (zipfile.BadZipFile, OSError, KeyError):
        second = False
    checks["the new file opens as a zip archive"] = second

    # Only the asked-for values differ in the swatch list.
    after = _read(result, written)
    by_id = {item.id: item for item in after.swatches}
    changed_ids = {change.id for change in report.changed}
    others_same = [item.id for item in before.swatches] == [item.id for item in after.swatches]
    if others_same:
        for old in before.swatches:
            new = by_id[old.id]
            if old.kind == "color" and old.id not in changed_ids and (old.space, old.values, old.name, old.model) != (new.space, new.values, new.name, new.model):
                others_same = False
            if old.kind == "tint" and (old.base, old.tint) != (new.base, new.tint):
                others_same = False
            if old.kind == "gradient" and [(s["color"], s["location"], s["midpoint"]) for s in old.stops] != [(s["color"], s["location"], s["midpoint"]) for s in new.stops]:
                others_same = False
    checks["no other swatch changed"] = others_same
    checks["same pages and stories"] = (before.spreads, before.master_spreads, before.stories) == (after.spreads, after.master_spreads, after.stories)

    for change in report.changed:
        item = by_id.get(change.id)
        if item is None:
            continue
        close = len(item.values) == len(change.values_after) and all(abs(a - b) < 0.00006 for a, b in zip(item.values, change.values_after))
        change.verified = close and item.space == change.space_after
        change.got_hex = item.hex
        change.exact = change.verified and item.hex_exact and item.hex == change.wanted_hex
    checks["every changed swatch reads back as written"] = all(change.verified for change in report.changed)
    report.ok = all(checks.values())
