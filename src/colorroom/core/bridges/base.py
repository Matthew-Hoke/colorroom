"""What every route-A bridge shares: the interface, the errors, the report and the check.

Made by Opus 5.5 (2026-11-14), for Fable 5.2 to review.

A bridge drives the person's own design program (Photoshop, Illustrator, InDesign) so that
the program itself writes the recolored file. Every bridge does the same four things:

    available()                          is the program installed and can Colorroom reach it?
    active_document()                    what is in front in the program right now (or None)
    read(path)              -> Manifest  every layer and its exact colors, plus a preview
    apply(source, mapping, save_as)
                            -> ApplyReport   change the colors in a copy, save it, read the
                                             saved file back and check every color

The safety rules live here so each bridge follows the same ones: the original file is never
opened for writing (`output_path` and `check_targets`), and success is only reported after the
saved file has been read again and every changed color compared (`verify`).

No window code in here, and nothing that needs Windows: the COM parts live in the
program-specific modules and are imported only when used.
"""

from __future__ import annotations

import hashlib
import json
import re
from abc import ABC, abstractmethod
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Mapping, Optional, Sequence

from ..colorspace import normalize_hex
from ..manifest import Manifest

SCRIPTS = Path(__file__).with_name("scripts")
_INCLUDE = re.compile(r'^\s*//\s*@include\s+"([^"]+)"\s*$', re.MULTILINE)


class BridgeError(Exception):
    """Something went wrong talking to the design program.

    `message` is written for the person using Colorroom and can be shown as it is. `detail`
    holds the technical text (for the report or a log), `code` a short name for tests.
    """

    def __init__(self, message: str, detail: str = "", code: str = "") -> None:
        super().__init__(message)
        self.message = message
        self.detail = detail
        self.code = code

    def __str__(self) -> str:
        return self.message


class NotAvailable(BridgeError):
    """The program is not installed, or Colorroom cannot reach it on this computer."""


class AppBusy(BridgeError):
    """The program is open but not answering, usually because a dialog box is open in it."""


# ---- mappings and file names -------------------------------------------------------------


def normalize_mapping(mapping: Mapping[str, str]) -> dict[str, str]:
    """`{old hex: new hex}` with every hex as "#RRGGBB"; colors that stay the same are dropped.

    Every color is replaced at most once, by its original value, so swaps (A to B and B to A)
    and chains (A to B, B to C) mean what they say.
    """
    out: dict[str, str] = {}
    for old, new in dict(mapping).items():
        try:
            old_hex, new_hex = normalize_hex(str(old)), normalize_hex(str(new))
        except ValueError:
            raise BridgeError(f"“{old}” or “{new}” is not a color Colorroom understands.", code="bad-color") from None
        if old_hex in out and out[old_hex] != new_hex:
            raise BridgeError(f"{old_hex} is asked to become two different colors.", code="bad-mapping")
        if old_hex != new_hex:
            out[old_hex] = new_hex
    return out


def output_path(source, option: str = "A") -> Path:
    """Where the recolored copy goes: "name - Colorroom A.psd" beside the original.

    An existing file is never replaced: "name - Colorroom A (2).psd" and so on are tried.
    """
    source = Path(source)
    option = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "", str(option)).strip() or "A"
    first = source.with_name(f"{source.stem} - Colorroom {option}{source.suffix}")
    if not first.exists():
        return first
    for number in range(2, 1000):
        candidate = source.with_name(f"{source.stem} - Colorroom {option} ({number}){source.suffix}")
        if not candidate.exists():
            return candidate
    raise BridgeError("There are too many Colorroom copies of this file beside it. Move some away and try again.")


def _same_file(a: Path, b: Path) -> bool:
    try:
        if a.exists() and b.exists():
            return a.samefile(b)
    except OSError:
        pass
    return str(a.resolve()).casefold() == str(b.resolve()).casefold()


def check_targets(source, save_as, extensions: Sequence[str], overwrite: bool = False) -> tuple[Path, Path]:
    """Refuse anything that could touch the original. Returns (source, save_as) resolved."""
    source = Path(source).resolve()
    if not source.is_file():
        raise BridgeError(f"Colorroom cannot find the file {source.name}.", code="missing")
    if source.suffix.lower() not in extensions:
        names = ", ".join(extensions)
        raise BridgeError(f"{source.name} is not a file this program saves ({names}).", code="type")
    target = Path(save_as).resolve()
    if _same_file(source, target):
        raise BridgeError("Colorroom never saves over your original file. Choose another name for the copy.", code="same-file")
    if target.suffix.lower() != source.suffix.lower():
        raise BridgeError(f"The copy must keep the original's file type ({source.suffix}).", code="type")
    if target.exists() and not overwrite:
        raise BridgeError(f"{target.name} already exists. Colorroom does not replace files; choose another name.", code="exists")
    if not target.parent.is_dir():
        raise BridgeError(f"The folder for {target.name} does not exist.", code="folder")
    return source, target


def file_hash(path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for block in iter(lambda: handle.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


# ---- scripts -----------------------------------------------------------------------------


def js_literal(value) -> str:
    """A JSON value is a valid ExtendScript literal once the two line separators are escaped."""
    return json.dumps(value, ensure_ascii=True)


def compose_script(script, args: Optional[dict] = None, scripts_dir: Path = SCRIPTS) -> str:
    """The text to send into the program: `var CR_ARGS = {...};`, the included library, the script.

    `script` is a file name inside `scripts_dir` or an absolute path. Each `//@include "x.jsx"`
    line is replaced by that file (from the script's own folder, then `scripts_dir`), once.
    A bare name is always looked up in `scripts_dir`, never in the current folder, so a file
    planted where Colorroom happens to run cannot be sent into the program instead.
    """
    path = Path(script)
    if not path.is_absolute():
        path = scripts_dir / path.name
    body = path.read_text(encoding="utf-8")
    included: list[str] = []

    def include(match: re.Match) -> str:
        name = match.group(1)
        if name in included:
            return ""
        included.append(name)
        for folder in (path.parent, scripts_dir):
            candidate = folder / name
            if candidate.is_file():
                return candidate.read_text(encoding="utf-8")
        raise BridgeError(f"A part of Colorroom is missing ({name}). Reinstall Colorroom.", code="script-missing")

    body = _INCLUDE.sub(include, body)
    return f"var CR_ARGS = {js_literal(args or {})};\n{body}"


# ---- the report ----------------------------------------------------------------------------


@dataclass
class PartResult:
    key: str
    where: str
    label: str
    before: str
    wanted: str
    now: Optional[str]  # what the saved file holds, None when it could not be found
    # True when the program converted the color into the document's own space (CMYK, spot) and
    # the saved file holds that conversion: close to `wanted`, not exact, and still a success.
    converted: bool = False

    @property
    def verified(self) -> bool:
        return self.now == self.wanted or (self.converted and self.now is not None)

    @property
    def exact(self) -> bool:
        return self.now == self.wanted


@dataclass
class LayerResult:
    id: int
    name: str
    path: str
    kind: str
    detail: str
    visible: bool
    parts: list[PartResult] = field(default_factory=list)
    problems: list[str] = field(default_factory=list)

    @property
    def verified(self) -> bool:
        return bool(self.parts) and all(part.verified for part in self.parts) and not self.problems


@dataclass
class Skipped:
    id: int
    name: str
    path: str
    kind: str
    reason: str  # in plain words


@dataclass
class ApplyReport:
    """What an export did, checked against the saved file. `ok` is the only success signal."""

    program: str
    source: str
    saved: Optional[str]
    mapping: dict
    changed: list[LayerResult] = field(default_factory=list)
    skipped: list[Skipped] = field(default_factory=list)
    problems: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    checks: dict = field(default_factory=dict)  # name -> bool, document-wide checks

    @property
    def ok(self) -> bool:
        return (
            self.saved is not None
            and bool(self.changed)
            and all(layer.verified for layer in self.changed)
            and not self.problems
            and all(self.checks.values())
        )

    def summary(self) -> str:
        """A few plain lines for the person who pressed Export."""
        lines = []
        name = Path(self.saved).name if self.saved else "the copy"
        count = len(self.changed)
        if self.saved is None:
            lines.append("Nothing was saved.")
        elif count == 0:
            lines.append(f"No layer in {name} uses the colors you changed.")
        else:
            lines.append(f"{count} {'layer' if count == 1 else 'layers'} changed in {name}.")
        bad = [layer for layer in self.changed if not layer.verified]
        close = [part for layer in self.changed for part in layer.parts if part.verified and not part.exact]
        if self.changed and not bad and not self.problems and all(self.checks.values()):
            if close:
                lines.append(
                    f"Checked by opening the saved file again: nothing else changed. {len(close)} "
                    f"{'color was' if len(close) == 1 else 'colors were'} converted to the document's own color space "
                    f"by {self.program}, so {'it is' if len(close) == 1 else 'they are'} close to, not exactly, the HEX you chose:"
                )
                lines.extend(f"  {part.label}: wanted {part.wanted}, the file holds {part.now}" for part in close)
            else:
                lines.append("Checked by opening the saved file again: every color is exact, and nothing else changed.")
        for layer in bad:
            for part in layer.parts:
                if not part.verified:
                    found = part.now or "nothing"
                    lines.append(f"“{layer.path}”: the {part.label} should be {part.wanted} but is {found}.")
            lines.extend(f"“{layer.path}”: {problem}" for problem in layer.problems)
        lines.extend(self.problems)
        for check, passed in self.checks.items():
            if not passed:
                lines.append(f"Check failed: {check}.")
        if self.skipped:
            reasons: dict[str, int] = {}
            for item in self.skipped:
                reasons[item.reason] = reasons.get(item.reason, 0) + 1
            lines.append("Left alone: " + "; ".join(f"{n} × {reason}" for reason, n in reasons.items()) + ".")
        lines.extend(self.notes)
        return "\n".join(lines)

    def to_dict(self) -> dict:
        data = asdict(self)
        data["ok"] = self.ok
        data["summary"] = self.summary()
        for layer, raw in zip(self.changed, data["changed"]):
            raw["verified"] = layer.verified
            for part, raw_part in zip(layer.parts, raw["parts"]):
                raw_part["verified"] = part.verified
        return data

    def write(self, path) -> Path:
        target = Path(path)
        target.write_text(json.dumps(self.to_dict(), indent=2, ensure_ascii=False) + "\n", encoding="utf-8", newline="\n")
        return target


# ---- the check ---------------------------------------------------------------------------


def verify(before: Manifest, after: Manifest, changes: Sequence[dict], report: ApplyReport) -> ApplyReport:
    """Compare the saved file (`after`) with the copy as it was opened (`before`).

    `changes` are what the program says it changed: `{id, key, from, to}`. Every one must be
    found in the saved file with the new color; every other color must be what it was; every
    layer must still be there with the same signature (name, kind, text, fonts, sizes, bounds,
    effects); and the document's size, resolution, mode and bit depth must be unchanged.
    """
    before_parts = {(layer.id, part.key): part for layer in before.layers for part in layer.parts}
    after_parts = {(layer.id, part.key): part for layer in after.layers for part in layer.parts}
    wanted: dict[tuple[int, str], dict] = {}
    for change in changes:
        try:
            spot = (int(change["id"]), str(change["key"]))
        except (KeyError, TypeError, ValueError):
            continue
        if spot in before_parts:  # changes to hidden defaults inside a layer are not colors anyone sees
            wanted[spot] = change
    results: dict[int, LayerResult] = {}
    for (identity, key), change in wanted.items():
        layer = before.layer(identity)
        part = before_parts[(identity, key)]
        result = results.get(identity)
        if result is None:
            result = results[identity] = LayerResult(identity, layer.name, layer.path, layer.kind, layer.detail, layer.visible)
        now = after_parts.get((identity, key))
        result.parts.append(PartResult(key, part.where, part.label or part.where, part.hex, normalize_hex(change["to"]), now.hex if now else None))
    # Nothing else may have moved.
    for spot, part in before_parts.items():
        if spot in wanted:
            continue
        now = after_parts.get(spot)
        if now is None or now.hex != part.hex:
            layer = before.layer(spot[0])
            result = results.get(spot[0])
            text = f"the {part.label or part.where} changed from {part.hex} to {now.hex if now else 'nothing'} although it was not asked to."
            if result is None:
                report.problems.append(f"“{layer.path}”: {text}")
            else:
                result.problems.append(text)
    before_ids = [layer.id for layer in before.layers]
    after_ids = [layer.id for layer in after.layers]
    report.checks["every layer is still there, in the same order"] = before_ids == after_ids
    changed_signatures = []
    for layer in before.layers:
        other = after.layer(layer.id)
        if other is not None and layer.signature != other.signature:
            changed_signatures.append(layer)
            if layer.id in results:
                results[layer.id].problems.append("something other than color changed in this layer (text, font, size, position or effects).")
            else:
                report.problems.append(f"“{layer.path}”: something other than color changed in this layer.")
    report.checks["only colors changed inside the layers"] = not changed_signatures
    report.checks["same size in pixels"] = (before.width, before.height) == (after.width, after.height)
    report.checks["same resolution (ppi)"] = abs(before.resolution - after.resolution) < 1e-6
    report.checks["same color mode"] = before.color_mode == after.color_mode
    report.checks["same bit depth"] = before.bit_depth == after.bit_depth
    report.changed = [results[layer.id] for layer in before.layers if layer.id in results]
    return report


# ---- the interface -----------------------------------------------------------------------


class Bridge(ABC):
    """One design program on this computer. See the module docstring."""

    program: str = ""
    extensions: tuple[str, ...] = ()

    @abstractmethod
    def available(self) -> bool:
        """True when the program is installed and Colorroom can talk to it. Never starts it."""

    @abstractmethod
    def running(self) -> bool:
        """True when the program is open right now. Never starts it."""

    @abstractmethod
    def active_document(self) -> Optional[dict]:
        """`{name, path, saved}` of the document in front, or None. Never starts the program."""

    @abstractmethod
    def read(self, path=None, *, work_dir=None, preview: bool = True, leave_open: bool = False) -> Manifest:
        """Every layer of `path` (or of the document in front) with its exact colors."""

    @abstractmethod
    def apply(self, source_path, mapping: Mapping[str, str], save_as=None, **options) -> ApplyReport:
        """Change `mapping`'s colors in a copy of `source_path`, save it, read it back, check it."""
