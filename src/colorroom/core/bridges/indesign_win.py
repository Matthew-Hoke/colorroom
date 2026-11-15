"""Route A for InDesign on Windows: Colorroom drives the user's own InDesign through COM.

Made by Opus 5.5 (2026-11-14). For Fable 5.2 to review.

InDesign itself reads and writes the ``.indd``, so every page item, text frame, style, link and
effect stays exactly as InDesign keeps it. Colorroom sends small ExtendScript files from
``scripts/indesign_*.jsx`` through ``DoScript`` and gets JSON back.

    available()                                  InDesign and pywin32 are installed
    running()                                    InDesign is open now (never starts it)
    read(path=None, start=False)                 -> IndesignDocument (path None: the active document)
    active_document()                            -> {name, path, saved, modified} or None, never starts InDesign
    apply(source, mapping, save_as, ...)         -> ColorReport (from .idml), checked by reading back
    preview(path, out, page=1, resolution=72)    -> Path of a PNG or JPEG of one page
    show(path)                                   opens a file in an InDesign window
    IndesignBridge                               base.Bridge: read -> Manifest, apply -> ApplyReport

InDesign colors live in swatches, not in layers: changing a swatch recolors every frame, stroke,
text run, tint and gradient stop that uses it, which is the native InDesign way. So the rich
results here are per swatch. `IndesignBridge` turns them into the shared Manifest and
ApplyReport (one entry per swatch) for the window.

The rules every call follows:

* InDesign is only started when the caller passes ``start=True`` (the user said yes).
* No dialogs: the scripts run with ``NEVER_INTERACT`` and put the user's setting back after.
  So opening a file never shows the missing-font or missing-link dialog, and fonts are never
  substituted.
* The original file is never opened by ``apply``: Colorroom copies it first and InDesign works
  on the copy. Documents the user had open are never closed or changed.
* Every apply ends with a read-back from the saved file on disk.
* A busy InDesign (an open dialog) is detected and reported instead of waiting for ever.

``pywin32`` is imported only when InDesign is used, so the rest of Colorroom runs without it.
No window code in here.
"""

from __future__ import annotations

import json
import os
import queue
import re
import shutil
import tempfile
import threading
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Callable, Mapping, Optional, Union

from . import base
from ..manifest import MANIFEST_SUFFIX, PREVIEW_SUFFIX, ColorPart, Manifest, ManifestLayer
from .base import AppBusy, ApplyReport, BridgeError, LayerResult, NotAvailable, PartResult, Skipped
from .idml import ColorReport, IdmlError, SwatchChange, _target, normalize_hex, read_swatches

__all__ = [
    "IndesignError",
    "IndesignUnavailable",
    "IndesignBusy",
    "IndesignSwatch",
    "IndesignLayer",
    "IndesignDocument",
    "IndesignBridge",
    "available",
    "running",
    "read",
    "active_document",
    "apply",
    "preview",
    "show",
    "documents",
    "run_script",
    "to_manifest",
    "to_apply_report",
]

PROGID = "InDesign.Application"
JAVASCRIPT = 1246973031  # ScriptLanguage.JAVASCRIPT ('jscr')
SCRIPTS = Path(__file__).with_name("scripts")
SOURCE_SUFFIXES = (".indd", ".idml")
BUILTIN_NAMES = ("Black", "Paper", "Registration", "None")

_RPC_BUSY = (-2147418111, -2147417846)  # RPC_E_CALL_REJECTED, RPC_E_SERVERCALL_RETRYLATER
_RETRIES = 40
_RETRY_PAUSE = 0.5
_START_TIMEOUT = 240.0
_PING_TIMEOUT = 30.0
_DISPATCH_TRIES = 5
#: InDesign's "Name with Color Value" swatch names.
_VALUE_NAME = re.compile(r"^(C=-?[\d.]+ M=-?[\d.]+ Y=-?[\d.]+ K=-?[\d.]+|R=[\d.]+ G=[\d.]+ B=[\d.]+|L=-?[\d.]+ a=-?[\d.]+ b=-?[\d.]+)$")
_MODELS = {"PROCESS": "process", "SPOT": "spot", "REGISTRATION": "registration", "MIXEDINKMODEL": "mixed"}
_SPACES = {"RGB": "RGB", "CMYK": "CMYK", "LAB": "Lab", "MIXEDINK": "MixedInk"}


class IndesignError(BridgeError):
    """Something went wrong with InDesign. The message is written for the person using the app."""


class IndesignUnavailable(IndesignError, NotAvailable):
    """InDesign or pywin32 is not installed, or InDesign is not open and may not be started."""


class IndesignBusy(IndesignError, AppBusy):
    """InDesign did not answer, usually because a dialog is open in it."""


# --------------------------------------------------------------------------------------------
# Talking to InDesign
# --------------------------------------------------------------------------------------------


def _com():
    try:
        import pythoncom  # noqa: F401
        import pywintypes  # noqa: F401
        import win32com.client  # noqa: F401
    except ImportError:
        raise IndesignUnavailable("Colorroom needs the pywin32 package to work with InDesign.") from None
    import pythoncom
    import pywintypes
    import win32com.client

    return pythoncom, pywintypes, win32com.client


def available() -> bool:
    """True when pywin32 is installed and InDesign has registered its scripting interface."""
    if os.name != "nt":
        return False
    try:
        pythoncom, pywintypes, _ = _com()
        pywintypes.IID(PROGID)  # resolves the ProgID through COM; starts nothing
        return True
    except (IndesignUnavailable, Exception):
        return False


def _process_running(image: str = "InDesign.exe") -> bool:
    """True when a process with this file name runs. InDesign does not put itself in the COM
    running object table, so GetActiveObject cannot see it; the process list can."""
    if os.name != "nt":
        return False
    import ctypes
    from ctypes import wintypes

    class Entry(ctypes.Structure):
        _fields_ = [
            ("dwSize", wintypes.DWORD),
            ("cntUsage", wintypes.DWORD),
            ("th32ProcessID", wintypes.DWORD),
            ("th32DefaultHeapID", ctypes.c_size_t),
            ("th32ModuleID", wintypes.DWORD),
            ("cntThreads", wintypes.DWORD),
            ("th32ParentProcessID", wintypes.DWORD),
            ("pcPriClassBase", ctypes.c_long),
            ("dwFlags", wintypes.DWORD),
            ("szExeFile", ctypes.c_wchar * 260),
        ]

    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.CreateToolhelp32Snapshot.restype = wintypes.HANDLE
    kernel.CreateToolhelp32Snapshot.argtypes = [wintypes.DWORD, wintypes.DWORD]
    kernel.Process32FirstW.argtypes = [wintypes.HANDLE, ctypes.POINTER(Entry)]
    kernel.Process32NextW.argtypes = [wintypes.HANDLE, ctypes.POINTER(Entry)]
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    snapshot = kernel.CreateToolhelp32Snapshot(0x2, 0)  # TH32CS_SNAPPROCESS
    if not snapshot or snapshot == wintypes.HANDLE(-1).value:
        return False
    try:
        entry = Entry()
        entry.dwSize = ctypes.sizeof(Entry)
        found = kernel.Process32FirstW(snapshot, ctypes.byref(entry))
        while found:
            if entry.szExeFile.lower() == image.lower():
                return True
            found = kernel.Process32NextW(snapshot, ctypes.byref(entry))
        return False
    finally:
        kernel.CloseHandle(snapshot)


def running() -> bool:
    """True when InDesign is open now. Never starts it."""
    return available() and _process_running()


class _Worker:
    """One thread that owns the COM connection for one public call.

    COM objects belong to the thread that made them. Running the calls on a thread of our own
    means a hung InDesign (a dialog is open) can be reported after a timeout. Once a step has
    timed out the worker runs nothing more, so no change is made later behind the user's back.
    """

    def __init__(self) -> None:
        self._jobs: "queue.Queue" = queue.Queue()
        self._abandoned = False
        self.app = None
        self.started_app = False
        self._thread = threading.Thread(target=self._loop, name="colorroom-indesign", daemon=True)
        self._thread.start()

    def _loop(self) -> None:
        pythoncom, _, _ = _com()
        pythoncom.CoInitialize()
        try:
            while True:
                job = self._jobs.get()
                if job is None:
                    break
                function, box, done = job
                try:
                    if not self._abandoned:
                        box["value"] = function()
                except BaseException as error:  # handed back to the caller's thread
                    box["error"] = error
                finally:
                    done.set()
        finally:
            self.app = None
            pythoncom.CoUninitialize()

    def call(self, function: Callable[[], Any], timeout: float, what: str) -> Any:
        box: dict = {}
        done = threading.Event()
        self._jobs.put((function, box, done))
        if not done.wait(timeout):
            self._abandoned = True
            self._jobs.put(None)
            raise IndesignBusy(f"InDesign did not answer while {what}. If a dialog is open in InDesign, close it and try again.")
        if "error" in box:
            raise box["error"]
        return box.get("value")

    def close(self) -> None:
        self._jobs.put(None)
        if not self._abandoned:
            self._thread.join(10.0)  # let COM let go before the next call connects


def _retry(function: Callable[[], Any]) -> Any:
    """Run a COM call, waiting while InDesign says it is busy."""
    _, pywintypes, _ = _com()
    for attempt in range(_RETRIES):
        try:
            return function()
        except pywintypes.com_error as error:
            if error.hresult in _RPC_BUSY and attempt < _RETRIES - 1:
                time.sleep(_RETRY_PAUSE)
                continue
            if error.hresult in _RPC_BUSY:
                raise IndesignBusy("InDesign is busy. If a dialog is open in InDesign, close it and try again.") from None
            raise IndesignError(f"InDesign reported a problem: {_com_message(error)}") from None
    raise IndesignBusy("InDesign is busy.")  # pragma: no cover


def _com_message(error) -> str:
    try:
        details = error.excepinfo
        if details and details[2]:
            return str(details[2]).strip()
    except (AttributeError, IndexError, TypeError):
        pass
    return str(error.strerror or error)


def _connect(worker: _Worker, start: bool) -> None:
    if not available():
        raise IndesignUnavailable("InDesign is not installed on this computer, or Colorroom cannot reach it.")
    _, _, client = _com()

    def attach():
        # Dispatch joins a running InDesign, or starts one. So it is only called when InDesign
        # already runs, or when the caller may start it.
        was_running = _process_running()
        if not was_running and not start:
            raise IndesignUnavailable("InDesign is not open. Open it, or let Colorroom start it.")
        _, pywintypes, _ = _com()
        for attempt in range(_DISPATCH_TRIES):
            try:
                worker.app = _retry(lambda: client.dynamic.Dispatch(PROGID))
                break
            except IndesignBusy:
                raise
            except IndesignError:
                # Seen once right after InDesign closed a document: "Cannot find window class".
                # Joining again a moment later works.
                if attempt == _DISPATCH_TRIES - 1:
                    raise
                time.sleep(1.0)
        worker.started_app = not was_running

    worker.call(attach, _START_TIMEOUT if start else _PING_TIMEOUT, "starting" if start else "connecting")

    def ping():
        return _retry(lambda: worker.app.DoScript("app.modalState ? 'busy' : 'ready'", JAVASCRIPT))

    state = worker.call(ping, _PING_TIMEOUT, "checking that it is ready")
    if state != "ready":
        raise IndesignBusy("A dialog is open in InDesign. Close it and try again.")


def _compose(script: Path, args: Mapping) -> str:
    """`var CR_ARGS = {...};`, then the script with its include line replaced by indesign_lib.jsx."""
    return base.compose_script(script, dict(args), SCRIPTS)


def _script_path(name: Union[str, Path]) -> Path:
    path = Path(name)
    if not path.is_absolute():
        path = SCRIPTS / (path.name if path.suffix else f"{path.name}.jsx")
    if not path.is_file():
        raise IndesignError(f"The script {path.name} is missing from Colorroom.")
    return path


def _run(worker: _Worker, script: Union[str, Path], args: Mapping, timeout: float, what: str) -> dict:
    source = _compose(_script_path(script), args)

    def job():
        return _retry(lambda: worker.app.DoScript(source, JAVASCRIPT))

    raw = worker.call(job, timeout, what)
    try:
        data = json.loads(raw)
    except (TypeError, ValueError):
        raise IndesignError("InDesign sent back an answer Colorroom does not understand.") from None
    if not isinstance(data, dict):
        raise IndesignError("InDesign sent back an answer Colorroom does not understand.")
    if not data.get("ok"):
        message = str(data.get("error") or "unknown problem")
        code, _, text = message.partition(": ")
        if code in ("NO_DOCUMENT", "NO_FILE", "NO_PAGE", "IN_USE"):
            raise IndesignError(text)
        raise IndesignError(f"InDesign reported a problem: {message}")
    return data


def _session(start: bool):
    worker = _Worker()
    try:
        _connect(worker, start)
    except BaseException:
        worker.close()
        raise
    return worker


def _js_path(path: Union[str, os.PathLike]) -> str:
    return Path(path).resolve().as_posix()


def run_script(script: Union[str, Path], args: Optional[Mapping] = None, *, start: bool = False, timeout: float = 300.0) -> dict:
    """Run one ``indesign_*.jsx`` script (or a .jsx file by full path) and return its JSON answer."""
    worker = _session(start)
    try:
        return _run(worker, script, dict(args or {}), timeout, "running a script")
    finally:
        worker.close()


# --------------------------------------------------------------------------------------------
# Reading
# --------------------------------------------------------------------------------------------


@dataclass
class IndesignSwatch:
    id: str  # InDesign's id for the object, stable in the saved file
    name: str  # "" for an unnamed color
    kind: str  # "color", "tint", "gradient", "mixed-ink", "none"
    named: bool = True  # False: an unnamed color (frames and text use it, the Swatches panel does not list it)
    listed: bool = True  # in the Swatches panel
    builtin: bool = False  # Black, Paper, Registration, None, and InDesign's hidden process inks
    editable: bool = True
    model: str = ""  # "process", "spot", "registration", "mixed"
    space: str = ""  # "RGB", "CMYK", "Lab"
    values: list = field(default_factory=list)
    hex: Optional[str] = None  # InDesign's own RGB for it, with the document's profiles
    hex_exact: bool = False  # True for RGB colors; tints are worked out and close, not exact
    base: str = ""  # tints: id of the base color
    tint: Optional[float] = None
    gradient_type: str = ""
    stops: list = field(default_factory=list)  # {"color": id, "location", "midpoint", "hex"}
    uses: int = 0  # page item fills and strokes
    text_uses: int = 0  # runs of text (fill or stroke)
    locked_uses: int = 0  # of `uses`, on locked items
    stop_uses: int = 0  # gradient stops
    tint_uses: int = 0  # tints built on it
    in_use: bool = False  # a page item or text shows it, directly or through a tint or gradient in use

    @property
    def used(self) -> bool:
        return bool(self.uses or self.text_uses or self.stop_uses or self.tint_uses)

    def to_dict(self) -> dict:
        data = asdict(self)
        data["used"] = self.used
        return data


@dataclass
class IndesignLayer:
    name: str
    visible: bool
    locked: bool


@dataclass
class IndesignDocument:
    path: Optional[Path]
    document: str
    program_version: str
    intent: str  # "PRINT_INTENT", "WEB_INTENT", "MOBILE_INTENT"
    blending_space: str  # "CMYK" or "RGB": the transparency blend space
    rgb_profile: str
    cmyk_profile: str
    page_width: float  # points
    page_height: float
    pages: int
    spreads: int
    master_spreads: int
    layers: list[IndesignLayer]
    swatches: list[IndesignSwatch]
    items: int
    item_kinds: dict
    locked_items: int
    stories: int
    text_ranges: int
    characters: int
    images: int  # placed pictures, left alone
    image_kinds: dict
    fonts_needing_attention: list  # {"name", "status"}
    links_needing_attention: list
    modified: bool  # the user's open document has unsaved changes
    opened_by_colorroom: bool
    notes: list[str] = field(default_factory=list)

    @property
    def color_mode(self) -> str:
        return {"PRINT_INTENT": "CMYK"}.get(self.intent, "RGB")

    def swatch(self, key: str) -> Optional[IndesignSwatch]:
        """Find a swatch by id or by name."""
        for item in self.swatches:
            if item.id == str(key):
                return item
        for item in self.swatches:
            if item.name and item.name == key:
                return item
        return None

    def colors(self) -> list[IndesignSwatch]:
        return [item for item in self.swatches if item.kind == "color"]

    def shape(self) -> dict:
        """Everything that must stay the same when only colors change."""
        return {
            "pages": self.pages,
            "spreads": self.spreads,
            "master spreads": self.master_spreads,
            "layers": [(layer.name, layer.visible, layer.locked) for layer in self.layers],
            "page items": self.items,
            "page item kinds": dict(sorted(self.item_kinds.items())),
            "locked items": self.locked_items,
            "stories": self.stories,
            "text runs": self.text_ranges,
            "characters": self.characters,
            "placed images": self.images,
            # Sorted: InDesign itself reorders the Swatches panel when it opens an IDML and saves
            # it as .indd. None added, removed or renamed is what matters.
            "swatch names": sorted((item.kind, item.name) for item in self.swatches if item.listed),
            "fonts needing attention": sorted((font["name"], font["status"]) for font in self.fonts_needing_attention),
        }

    def to_dict(self) -> dict:
        data = asdict(self)
        data["path"] = str(self.path) if self.path else None
        data["swatches"] = [item.to_dict() for item in self.swatches]
        data["color_mode"] = self.color_mode
        return data


def _document_from(data: dict) -> IndesignDocument:
    swatches: list[IndesignSwatch] = []
    for raw in data.get("swatches") or []:
        name = str(raw.get("name") or "")
        named = bool(name)
        listed = bool(raw.get("listed", True))
        kind = str(raw.get("kind") or "none")
        # Named colors missing from the Swatches panel are InDesign's own process inks (Cyan,
        # Magenta, Yellow): never changed.
        builtin = named and (name in BUILTIN_NAMES or not listed)
        rgb_hex = None
        if raw.get("hex"):
            try:
                rgb_hex = normalize_hex(raw["hex"])
            except IdmlError:
                rgb_hex = None
        swatches.append(
            IndesignSwatch(
                id=str(raw.get("id")),
                name=name,
                kind=kind,
                named=named,
                listed=listed,
                builtin=builtin,
                editable=bool(raw.get("editable", True)) and not builtin,
                model=_MODELS.get(str(raw.get("model") or ""), str(raw.get("model") or "").lower()),
                space=_SPACES.get(str(raw.get("space") or ""), str(raw.get("space") or "")),
                values=[float(v) for v in raw.get("values") or []],
                hex=rgb_hex,
                hex_exact=bool(raw.get("hexExact")),
                base=str(raw.get("base") or ""),
                tint=raw.get("tint"),
                gradient_type=str(raw.get("gradientType") or "").lower(),
                stops=[{"color": str(s.get("color") or ""), "location": s.get("location"), "midpoint": s.get("midpoint"), "hex": None} for s in raw.get("stops") or []],
                uses=int(raw.get("uses") or 0),
                text_uses=int(raw.get("textUses") or 0),
                locked_uses=int(raw.get("lockedUses") or 0),
                stop_uses=int(raw.get("stopUses") or 0),
                tint_uses=int(raw.get("tintUses") or 0),
            )
        )
    by_id = {item.id: item for item in swatches}
    for item in swatches:
        for stop in item.stops:
            target = by_id.get(stop["color"])
            stop["hex"] = target.hex if target else None
    # In use: shown by a page item or text, or part of a tint or gradient in use. Listed
    # gradients count as in use (the user may apply them).
    pending = [item for item in swatches if item.uses or item.text_uses or (item.kind == "gradient" and item.listed)]
    while pending:
        item = pending.pop()
        if item.in_use:
            continue
        item.in_use = True
        parts = [item.base] if item.kind == "tint" else [stop["color"] for stop in item.stops]
        pending.extend(by_id[part] for part in parts if part in by_id and not by_id[part].in_use)

    doc = IndesignDocument(
        path=Path(data["path"]) if data.get("path") else None,
        document=str(data.get("document") or ""),
        program_version=str(data.get("programVersion") or ""),
        intent=str(data.get("intent") or ""),
        blending_space=str(data.get("blendingSpace") or ""),
        rgb_profile=str(data.get("rgbProfile") or ""),
        cmyk_profile=str(data.get("cmykProfile") or ""),
        page_width=float(data.get("pageWidth") or 0),
        page_height=float(data.get("pageHeight") or 0),
        pages=int(data.get("pages") or 0),
        spreads=int(data.get("spreads") or 0),
        master_spreads=int(data.get("masterSpreads") or 0),
        layers=[IndesignLayer(str(l.get("name")), bool(l.get("visible")), bool(l.get("locked"))) for l in data.get("layers") or []],
        swatches=swatches,
        items=int(data.get("items") or 0),
        item_kinds=dict(data.get("itemKinds") or {}),
        locked_items=int(data.get("lockedItems") or 0),
        stories=int(data.get("stories") or 0),
        text_ranges=int(data.get("textRanges") or 0),
        characters=int(data.get("characters") or 0),
        images=int(data.get("images") or 0),
        image_kinds=dict(data.get("imageKinds") or {}),
        fonts_needing_attention=list(data.get("fontsNeedingAttention") or []),
        links_needing_attention=list(data.get("linksNeedingAttention") or []),
        modified=bool(data.get("modified")),
        opened_by_colorroom=bool(data.get("openedByColorroom")),
    )
    doc.notes = _notes(doc)
    return doc


def _notes(doc: IndesignDocument) -> list[str]:
    notes = []
    if doc.images:
        notes.append(f"{doc.images} placed {'picture is' if doc.images == 1 else 'pictures are'} left alone.")
    if doc.fonts_needing_attention:
        names = ", ".join(sorted({font["name"] for font in doc.fonts_needing_attention})[:5])
        notes.append(f"Fonts that are missing or replaced on this computer: {names}. Colorroom never changes fonts.")
    if doc.links_needing_attention:
        notes.append(f"{len(doc.links_needing_attention)} linked files are missing or out of date. Colorroom leaves links alone.")
    hidden = [layer.name for layer in doc.layers if not layer.visible]
    if hidden:
        notes.append(f"Hidden layers: {', '.join(hidden)}. Their swatches change too.")
    locked = [layer.name for layer in doc.layers if layer.locked]
    if locked:
        notes.append(f"Locked layers: {', '.join(locked)}. A swatch change recolors them too, as it does in InDesign.")
    if doc.locked_items:
        notes.append(f"{doc.locked_items} locked {'item' if doc.locked_items == 1 else 'items'}. A swatch change recolors {'it' if doc.locked_items == 1 else 'them'} too, as it does in InDesign.")
    unnamed = [item for item in doc.colors() if not item.named and item.in_use]
    if unnamed:
        notes.append(f"{len(unnamed)} unnamed {'color is' if len(unnamed) == 1 else 'colors are'} used (not in the Swatches panel). They are listed too and can be changed.")
    if doc.intent == "PRINT_INTENT":
        notes.append("This is a print document: CMYK swatches stay CMYK, converted by InDesign with the document's color profile, so their hex can differ a little from the one asked for.")
    if doc.modified and not doc.opened_by_colorroom:  # an .idml Colorroom opened is always "modified"
        notes.append("This document has unsaved changes in InDesign. Exports use the last saved version.")
    return notes


def read(path: Optional[Union[str, os.PathLike]] = None, *, start: bool = False, timeout: float = 600.0) -> IndesignDocument:
    """Read an .indd or .idml file through InDesign, or the active document when `path` is None.

    A file the user already has open is read as it is in InDesign; any other file is opened
    without a window and closed again without saving. Nothing is changed.
    """
    args = {"path": ""}
    if path is not None:
        source = Path(path)
        if source.suffix.lower() not in SOURCE_SUFFIXES:
            raise IndesignError("Colorroom reads InDesign files that end in .indd or .idml.")
        if not source.is_file():
            raise IndesignError("The file could not be found.")
        args["path"] = _js_path(source)
    worker = _session(start)
    try:
        return _document_from(_run(worker, "indesign_read", args, timeout, "reading the document"))
    finally:
        worker.close()


def documents() -> list[dict]:
    """The documents open in InDesign: {name, path, saved, modified, active}. Never starts it."""
    if not running():
        return []
    try:
        return list(run_script("indesign_status", start=False, timeout=60.0).get("documents") or [])
    except IndesignUnavailable:
        return []


def active_document() -> Optional[dict]:
    """{name, path, saved, modified} of the document in front in InDesign, or None. Never starts
    InDesign. Use `read(None)` for its swatches."""
    for document in documents():
        if document.get("active"):
            return {key: document.get(key) for key in ("name", "path", "saved", "modified")}
    return None


# --------------------------------------------------------------------------------------------
# Applying
# --------------------------------------------------------------------------------------------


def _plan(doc: IndesignDocument, mapping: Mapping, space: str, include_spot: bool, report: ColorReport) -> list[dict]:
    """Turn {old: new} into one step per color. Skips go into the report with a reason."""
    colors = doc.colors()
    steps: dict[str, dict] = {}
    for old, new in mapping.items():
        old_text = str(old).strip()
        try:
            new_hex, new_space, new_values = _target(new)
        except (KeyError, TypeError, ValueError, IdmlError):
            raise IndesignError(f"The new color for {old_text} is not a color.") from None
        is_hex = old_text.startswith("#")
        old_hex = normalize_hex(old_text) if is_hex else None
        if is_hex:
            # Not InDesign's hidden process inks, and not unnamed colors nothing uses.
            matches = [item for item in colors if item.hex == old_hex and (item.listed or not item.named) and (item.named or item.in_use)]
        else:
            matches = [item for item in colors if item.id == old_text] or [item for item in colors if item.name and item.name == old_text]
        if not matches:
            others = [item for item in doc.swatches if item.kind != "color" and ((is_hex and item.hex == old_hex) or (not is_hex and old_text in (item.id, item.name)))]
            if others and others[0].kind == "tint":
                base = doc.swatch(others[0].base)
                why = f"it is a tint of '{base.name if base else others[0].base}'. Change that color and the tint follows."
            elif others and others[0].kind == "gradient":
                why = "it is a gradient. Change the colors of its stops and the gradient follows."
            elif others:
                why = "it is a mixed ink, which Colorroom does not change."
            else:
                why = "no swatch or color in the document has this color."
            report.skipped.append({"what": old_text, "why": why})
            continue
        for item in matches:
            label = f"'{item.name}'" if item.named else f"an unnamed color ({item.hex})"
            if item.builtin or not item.editable:
                report.skipped.append({"what": label, "why": "it is built into InDesign and cannot be changed. Give the frames and text a new swatch in InDesign instead."})
            elif item.model in ("mixed", "registration") or item.space not in ("RGB", "CMYK", "Lab"):
                report.skipped.append({"what": label, "why": f"it is a {item.space or item.model} color, which Colorroom does not change."})
            elif item.model == "spot" and not include_spot:
                report.skipped.append({"what": label, "why": "it is a spot color and spot colors were left alone."})
            elif item.id in steps:
                report.skipped.append({"what": f"{old_text} for {label}", "why": "this color was already given a new color by another line of the mapping."})
            else:
                out_space = new_space or ("RGB" if space == "rgb" else item.space)
                step = {"id": item.id, "name": item.name, "space": out_space.upper()}
                if new_values is not None:
                    step["values"] = new_values
                else:
                    red, green, blue = (int(new_hex[i : i + 2], 16) for i in (1, 3, 5))
                    step["rgb"] = [red, green, blue]
                steps[item.id] = {"step": step, "item": item, "wanted": new_hex}
    return list(steps.values())


def apply(
    source_path: Union[str, os.PathLike],
    mapping: Mapping,
    save_as: Union[str, os.PathLike],
    *,
    space: str = "keep",
    include_spot: bool = True,
    export_idml: Union[bool, str, os.PathLike, None] = None,
    leave_open: bool = False,
    start: bool = False,
    overwrite: bool = False,
    timeout: float = 900.0,
) -> ColorReport:
    """Change swatch colors in a copy of an InDesign document, save it, and check it.

    source_path  the user's .indd (never opened: Colorroom copies it and InDesign opens the
                 copy) or an .idml file.
    mapping      {old: new}, as in `idml.rewrite`: `old` is a hex (every swatch and unnamed
                 color InDesign shows as that hex), a swatch name or an id; `new` is a hex or
                 {"space": "CMYK", "values": [...]} for exact ink values.
    save_as      the new .indd, for example "Poster - Colorroom A.indd". Never the original.
    space        "keep": each color stays in its color space (a CMYK swatch stays CMYK, and
                 InDesign converts the new hex with the document's profile); "rgb": changed
                 colors become RGB, so every hex is exact.
    export_idml  True (beside save_as) or a path: also export IDML from InDesign.
    leave_open   show the saved file in an InDesign window afterwards.
    start        allow Colorroom to start InDesign if it is not open.
    """
    source = Path(source_path)
    target = Path(save_as)
    if space not in ("keep", "rgb"):
        raise IndesignError("space must be 'keep' or 'rgb'.")
    if source.suffix.lower() not in SOURCE_SUFFIXES or not source.is_file():
        raise IndesignError("Choose an InDesign file that ends in .indd or .idml.")
    if target.suffix.lower() != ".indd":
        raise IndesignError("The new file needs a name that ends in .indd.")
    if target.resolve() == source.resolve():
        raise IndesignError("Colorroom never writes over the original file. Choose another name.")
    if not target.parent.is_dir():
        raise IndesignError("The folder for the new file does not exist.")
    if target.exists() and not overwrite:
        raise IndesignError(f"'{target.name}' already exists. Choose another name.")
    idml_path: Optional[Path] = None
    if export_idml:
        idml_path = target.with_suffix(".idml") if export_idml is True else Path(export_idml)
        if idml_path.suffix.lower() != ".idml" or idml_path.resolve() == source.resolve():
            raise IndesignError("The IDML copy needs its own name that ends in .idml.")
        if idml_path.exists() and not overwrite:
            raise IndesignError(f"'{idml_path.name}' already exists. Choose another name.")

    report = ColorReport(route="indesign", source=str(source))
    worker = _session(start)
    made_copy = False
    try:
        if source.suffix.lower() == ".indd":
            if target.exists():
                target.unlink()
            shutil.copy2(source, target)  # InDesign only ever opens this copy
            made_copy = True
            open_path = target
        else:
            open_path = source  # an .idml opens as a new untitled document; the file is not changed

        before = _document_from(_run(worker, "indesign_read", {"path": _js_path(open_path)}, timeout, "reading the document"))
        for note in before.notes:
            if space == "rgb" and note.startswith("This is a print document"):
                note = "This is a print document: the changed swatches are now RGB, so every hex is exact; InDesign converts them to CMYK when it prints or exports."
            report.notes.append(note)
        steps = _plan(before, mapping, space, include_spot, report)
        if not steps:
            report.notes.append("Nothing was changed, so no file was written.")
            if made_copy:
                target.unlink()
                made_copy = False
            return report

        answer = _run(
            worker,
            "indesign_apply",
            {
                "open": _js_path(open_path),
                "save": _js_path(target),
                "idml": _js_path(idml_path) if idml_path else "",
                "plan": [entry["step"] for entry in steps],
            },
            timeout,
            "changing the colors",
        )
        made_copy = True
        report.saved = str(target)
        if idml_path:
            report.extra_files["idml"] = str(idml_path)

        results = {str(item.get("id")): item for item in answer.get("results") or []}
        after = _document_from(_run(worker, "indesign_read", {"path": _js_path(target)}, timeout, "checking the saved file"))
        renamed = _check(before, after, steps, results, report, space)
        if idml_path:
            _check_idml(idml_path, report, renamed)
        report.ok = all(report.checks.values()) and bool(report.changed)
        if leave_open:
            _run(worker, "indesign_open", {"path": _js_path(target)}, 120.0, "opening the new file")
    except BaseException:
        if made_copy and not report.saved and target.exists():
            try:
                target.unlink()  # a half-made copy is not kept
            except OSError:
                pass
        raise
    finally:
        worker.close()

    _follows(after, report)
    return report


def _close(first: list, second: list, tolerance: float) -> bool:
    return len(first) == len(second) and all(abs(float(a) - float(b)) <= tolerance for a, b in zip(first, second))


def _check(before: IndesignDocument, after: IndesignDocument, steps: list[dict], results: dict, report: ColorReport, space: str) -> dict:
    """Fill in the report's checks. Returns {id: new name} for swatches InDesign renamed."""
    checks = report.checks
    renamed: dict = {}
    shape_before, shape_after = before.shape(), after.shape()
    # A swatch named after its values ("C=100 M=90 Y=10 K=0") is renamed by InDesign itself to
    # its new values. That one rename is expected; any other is a difference.
    for entry in steps:
        item = entry["item"]
        now = after.swatch(item.id)
        if now is not None and now.name != item.name and _VALUE_NAME.match(item.name or "") and _VALUE_NAME.match(now.name or ""):
            names = shape_before["swatch names"]
            shape_before["swatch names"] = sorted(("color", now.name) if pair == ("color", item.name) else pair for pair in names)
            renamed[item.id] = now.name
            report.notes.append(f"InDesign renamed the swatch '{item.name}' to '{now.name}', as it does for swatches named after their color values.")
    differences = [name for name in shape_before if shape_before[name] != shape_after.get(name)]
    checks["same pages, layers, items, text and fonts"] = not differences
    if differences:
        report.notes.append("These changed although only colors should have: " + ", ".join(differences) + ".")

    changed_ids = {entry["item"].id for entry in steps}
    others_same = True
    for old in before.swatches:
        if old.id in changed_ids:
            continue
        new = after.swatch(old.id) or (after.swatch(old.name) if old.named else None)
        if new is None:
            others_same = False
            continue
        if old.kind == "color" and (old.space, old.model, old.name) != (new.space, new.model, new.name):
            others_same = False
        elif old.kind == "color" and not _close(old.values, new.values, 0.001):
            others_same = False
        elif old.kind == "tint" and (old.base, old.tint) != (new.base, new.tint):
            others_same = False
        elif old.kind == "gradient" and [(s["color"], s["location"], s["midpoint"]) for s in old.stops] != [(s["color"], s["location"], s["midpoint"]) for s in new.stops]:
            others_same = False
    checks["no other swatch changed"] = others_same

    for entry in steps:
        item: IndesignSwatch = entry["item"]
        step = entry["step"]
        result = results.get(item.id) or {}
        now = after.swatch(item.id) or (after.swatch(item.name) if item.named else None)
        change = SwatchChange(
            id=item.id,
            name=item.name,
            model=item.model,
            old_hex=item.hex,
            wanted_hex=entry["wanted"] or (now.hex if now else None),
            space_before=item.space,
            space_after=_SPACES.get(step["space"], step["space"]),
            values_before=list(item.values),
            values_after=[float(v) for v in result.get("values") or []],
        )
        notes = []
        if not result.get("ok"):
            change.note = f"InDesign did not change it: {result.get('error') or 'no answer'}"
            report.changed.append(change)
            continue
        if now is not None:
            change.got_hex = now.hex
            asked = step.get("rgb") or step.get("values")
            set_ok = now.space == change.space_after and _close(now.values, change.values_after, 0.01)
            if step["space"] == "RGB" or "values" in step:
                set_ok = set_ok and _close(now.values, asked, 0.01)
            change.verified = set_ok
            change.exact = set_ok and now.hex == change.wanted_hex
        if change.space_before != change.space_after:
            notes.append(f"Converted from {change.space_before} to {change.space_after}.")
        if "rgb" in step and step["space"] != "RGB":
            notes.append(f"Kept as {change.space_after}: InDesign converted the new color with the document's color profile.")
            if change.verified and not change.exact:
                notes.append(f"It shows as {change.got_hex}; choose 'rgb' to get {change.wanted_hex} exactly.")
        if item.model == "spot":
            notes.append("This is a spot color: the ink keeps its name, so tell your printer.")
        if item.locked_uses:
            notes.append(f"{item.locked_uses} locked {'item uses' if item.locked_uses == 1 else 'items use'} it and changed with the swatch.")
        if not item.named:
            notes.append("This was an unnamed color; it was changed in place and stays unnamed.")
        change.note = " ".join(notes)
        report.changed.append(change)
    checks["every changed color reads back from the saved file as set"] = all(change.verified for change in report.changed)
    if space == "rgb" or all(entry["step"]["space"] == "RGB" for entry in steps):
        checks["every changed color has the exact new hex"] = all(change.exact for change in report.changed)
    return renamed


def _check_idml(idml_path: Path, report: ColorReport, renamed: Optional[dict] = None) -> None:
    """The IDML InDesign exported holds the same new values (a second, independent read)."""
    try:
        exported = read_swatches(idml_path)
    except IdmlError as error:
        report.checks["the IDML copy opens and holds the new colors"] = False
        report.notes.append(f"The IDML copy could not be read: {error}")
        return
    good = True
    for change in report.changed:
        name = (renamed or {}).get(change.id, change.name)
        found = exported.swatch(name) if name else None
        if change.name and (found is None or not _close(found.values, change.values_after, 0.01)):
            good = False
    report.checks["the IDML copy opens and holds the new colors"] = good


def _follows(after: IndesignDocument, report: ColorReport) -> None:
    changed = {change.id for change in report.changed}
    for item in after.swatches:
        if item.kind == "tint" and item.base in changed:
            report.follows.append({"kind": "tint", "name": item.name, "id": item.id, "because": item.base, "hex": item.hex})
        elif item.kind == "gradient" and any(stop["color"] in changed for stop in item.stops):
            report.follows.append({"kind": "gradient", "name": item.name, "id": item.id, "because": [s["color"] for s in item.stops if s["color"] in changed]})


# --------------------------------------------------------------------------------------------
# Preview and showing
# --------------------------------------------------------------------------------------------


def preview(
    path: Optional[Union[str, os.PathLike]],
    out: Union[str, os.PathLike],
    *,
    page: int = 1,
    resolution: float = 72.0,
    start: bool = False,
    overwrite: bool = True,
    timeout: float = 300.0,
) -> Path:
    """Save a picture of one page of an .indd or .idml (or of the active document when `path`
    is None) as PNG or JPEG.

    At the default 72 pixels per inch one pixel is one point, InDesign's own unit, so a
    1920 x 1080 web document gives a 1920 x 1080 picture. Use a higher resolution for print.
    The document and InDesign's export settings are left as they were.
    """
    target = Path(out)
    if target.suffix.lower() not in (".png", ".jpg", ".jpeg"):
        raise IndesignError("The preview needs a name that ends in .png or .jpg.")
    if not target.parent.is_dir():
        raise IndesignError("The folder for the preview does not exist.")
    if target.exists() and not overwrite:
        raise IndesignError(f"'{target.name}' already exists.")
    if not 1 <= float(resolution) <= 2400:
        raise IndesignError("The resolution must be between 1 and 2400 pixels per inch.")
    args = {"path": "", "out": _js_path(target), "page": int(page), "resolution": float(resolution)}
    if path is not None:
        source = Path(path)
        if source.suffix.lower() not in SOURCE_SUFFIXES or not source.is_file():
            raise IndesignError("Choose an InDesign file that ends in .indd or .idml.")
        if source.resolve() == target.resolve():
            raise IndesignError("The preview needs its own file name.")
        args["path"] = _js_path(source)
    worker = _session(start)
    try:
        answer = _run(worker, "indesign_preview", args, timeout, "making the preview")
    finally:
        worker.close()
    if not target.is_file():
        raise IndesignError("InDesign did not write the preview picture.")
    del answer
    return target


page_preview = preview  # the same function, for code where `preview` is a flag


def show(path: Union[str, os.PathLike], *, start: bool = False) -> None:
    """Open a file in an InDesign window so the user can work on it."""
    source = Path(path)
    if not source.is_file():
        raise IndesignError("The file could not be found.")
    run_script("indesign_open", {"path": _js_path(source)}, start=start, timeout=180.0)


def quit_if_idle() -> bool:
    """Quit InDesign only when it has no documents open at all. For tests; True if it quit."""
    if not running():
        return False
    worker = _session(False)
    try:
        def job():
            if int(worker.app.Documents.Count) == 0:
                worker.app.Quit(1852776480)  # SaveOptions.NO
                return True
            return False

        return bool(worker.call(job, 60.0, "quitting"))
    finally:
        worker.close()


# --------------------------------------------------------------------------------------------
# The shared bridge shape (base.Bridge): Manifest in, ApplyReport out
# --------------------------------------------------------------------------------------------

_UNSAFE_NAME = re.compile(r'[<>:"/\\|?*\x00-\x1f]')


def _swatch_label(item) -> str:
    return item.name if item.name else f"Unnamed color {item.hex or ''}".strip()


def _swatch_detail(item) -> str:
    kind = {"spot": "spot color", "process": "process color"}.get(item.model, "color")
    return f"{kind} swatch ({item.space})" if item.named else f"unnamed {kind} ({item.space})"


def to_manifest(doc: IndesignDocument, manifest_path: Path, preview_path: Optional[Path] = None) -> Manifest:
    """The document as the shared Manifest: one entry per color in use (a swatch or an unnamed
    color), since a swatch is what recolors everything that uses it. Page items are not listed
    one by one; the counts are in each entry's notes."""
    layers: list[ManifestLayer] = []
    unused = 0
    for item in doc.colors():
        if item.builtin or not item.editable or not item.hex or item.space not in ("RGB", "CMYK", "Lab"):
            continue
        if not item.in_use:
            unused += 1 if (item.named and item.listed) else 0
            continue
        wheres = [where for where, count in (("fill", item.uses), ("text", item.text_uses), ("gradient", item.stop_uses), ("fill", item.tint_uses)) if count]
        wheres = list(dict.fromkeys(wheres)) or ["fill"]
        uses = []
        if item.uses:
            uses.append(f"{item.uses} frame {'fill or stroke' if item.uses == 1 else 'fills and strokes'}")
        if item.text_uses:
            uses.append(f"{item.text_uses} text {'run' if item.text_uses == 1 else 'runs'}")
        if item.stop_uses:
            uses.append(f"{item.stop_uses} gradient {'stop' if item.stop_uses == 1 else 'stops'}")
        if item.tint_uses:
            uses.append(f"{item.tint_uses} {'tint' if item.tint_uses == 1 else 'tints'}")
        notes = ["Used by " + ", ".join(uses) + "."] if uses else []
        if item.locked_uses:
            notes.append(f"{item.locked_uses} of them locked.")
        if not item.hex_exact:
            notes.append("Shown as InDesign converts it with the document's color profile.")
        label = _swatch_label(item)
        layers.append(
            ManifestLayer(
                id=int(item.id),
                name=label,
                path=f"Swatches / {label}",
                kind="fill",
                visible=True,
                locked=False,
                bounds=None,
                colors=[(where, item.hex) for where in wheres],
                parts=[ColorPart(key=f"swatch/{item.id}", where=wheres[0], hex=item.hex, label="swatch color")],
                detail=_swatch_detail(item),
                notes=notes,
                signature=json.dumps([item.name, item.model, item.space]),
            )
        )
    notes = list(doc.notes)
    if unused:
        notes.append(f"{unused} {'swatch is' if unused == 1 else 'swatches are'} not used in the document, so not listed.")
    follow = [item for item in doc.swatches if item.kind in ("tint", "gradient") and item.in_use]
    if follow:
        notes.append(f"{len(follow)} tints and gradients follow the colors they are made of.")
    profile = doc.cmyk_profile if doc.color_mode == "CMYK" else doc.rgb_profile
    return Manifest(
        path=manifest_path,
        program="InDesign",
        program_version=doc.program_version,
        document=doc.document,
        color_mode=doc.color_mode,
        width=int(round(doc.page_width)),
        height=int(round(doc.page_height)),
        preview=preview_path,
        layers=layers,
        notes=notes,
        source=doc.path,
        resolution=72.0,  # points: InDesign's own unit, one pixel per point in the preview
        bit_depth=8,
        layer_comps=0,
        color_profile=profile,
    )


def to_apply_report(color_report: ColorReport, mapping: Mapping[str, str]) -> ApplyReport:
    """A ColorReport in the shared ApplyReport shape (one entry per swatch). A CMYK or spot
    swatch that InDesign converted with the document's profile counts as done but not exact
    (`PartResult.converted`), and the summary lists it."""
    report = ApplyReport("InDesign", color_report.source, color_report.saved or None, dict(mapping))
    for change in color_report.changed:
        label = change.name or f"Unnamed color {change.old_hex or ''}".strip()
        result = LayerResult(
            id=int(change.id) if str(change.id).isdigit() else 0,
            name=label,
            path=f"Swatches / {label}",
            kind="fill",
            detail=f"{change.model} color swatch ({change.space_after})",
            visible=True,
            parts=[
                PartResult(
                    f"swatch/{change.id}", "fill", f"swatch {label}", change.old_hex or "", change.wanted_hex or "", change.got_hex,
                    converted=bool(change.verified and not change.exact),
                )
            ],
        )
        if not change.verified:
            result.problems.append(change.note or "InDesign did not keep the new color.")
        elif not change.exact and change.note:
            report.notes.append(f"{label}: {change.note}")
        report.changed.append(result)
    for item in color_report.skipped:
        report.skipped.append(Skipped(0, str(item["what"]), "", "swatch", str(item["why"])))
    report.checks.update(color_report.checks)
    if color_report.follows:
        report.notes.append(f"{len(color_report.follows)} tints and gradients changed with their colors.")
    report.notes.extend(color_report.notes)
    return report


class IndesignBridge(base.Bridge):
    """InDesign through the shape every Colorroom bridge shares (base.Bridge).

    `start` says whether Colorroom may start InDesign; the window asks the user first. For the
    full per-swatch results use the module functions `read` and `apply`.
    """

    program = "InDesign"
    extensions = SOURCE_SUFFIXES

    def __init__(self, start: bool = False) -> None:
        self.start = start

    def available(self) -> bool:
        return available()

    def running(self) -> bool:
        return running()

    def active_document(self) -> Optional[dict]:
        return active_document()

    def read(self, path=None, *, work_dir=None, preview: bool = True, leave_open: bool = False) -> Manifest:
        """The document's colors as a Manifest, with a picture of page 1 in `work_dir` (a
        "Colorroom" folder in the system's temporary folder by default)."""
        doc = read(path, start=self.start)
        stem = _UNSAFE_NAME.sub("_", Path(doc.document).stem) or "document"
        work = Path(work_dir) if work_dir is not None else Path(tempfile.gettempdir()) / "Colorroom"
        work.mkdir(parents=True, exist_ok=True)
        picture = None
        if preview:
            picture = page_preview(path, work / (stem + PREVIEW_SUFFIX), start=self.start)
        if leave_open and path is not None:
            show(path, start=self.start)
        return to_manifest(doc, work / (stem + MANIFEST_SUFFIX), picture)

    def apply(
        self,
        source_path,
        mapping: Mapping[str, str],
        save_as=None,
        *,
        option: str = "A",
        space: str = "keep",
        include_spot: bool = True,
        export_idml=None,
        leave_open: bool = False,
        overwrite: bool = False,
        report_path=None,
    ) -> ApplyReport:
        """Change `mapping`'s colors ({old hex: new hex}) in a copy and check it. The copy is
        `save_as`, or "name - Colorroom <option>.indd" beside the original."""
        changes = base.normalize_mapping(mapping)
        if not changes:
            raise BridgeError("No color is set to change, so there is nothing to export.", code="no-changes")
        source = Path(source_path)
        target = Path(save_as) if save_as is not None else base.output_path(source.with_suffix(".indd"), option)
        original = base.file_hash(source) if source.is_file() else ""
        result = apply(
            source, changes, target, space=space, include_spot=include_spot, export_idml=export_idml,
            leave_open=leave_open, start=self.start, overwrite=overwrite,
        )
        report = to_apply_report(result, changes)
        report.checks["the original file is unchanged"] = bool(original) and base.file_hash(source) == original
        if report_path is not None:
            report.write(report_path)
        return report

    def preview(self, path, out, **options) -> Path:
        options.setdefault("start", self.start)
        return page_preview(path, out, **options)
