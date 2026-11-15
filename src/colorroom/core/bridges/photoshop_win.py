"""Route A for Photoshop on Windows: Colorroom drives the person's own Photoshop through COM.

Made by Opus 5.5 (2026-11-14), for Fable 5.2 to review.

Photoshop itself opens a copy of the file, changes the exact colors on the real layers (fills,
shapes and their strokes, text runs, gradient fills, gradient maps, other adjustment colors and
layer effects) and saves it, so every layer stays editable. Colorroom then opens the saved file
again, reads every color back and compares (`base.verify`).

    bridge = PhotoshopBridge()
    bridge.available()                       # installed and pywin32 present; never starts it
    manifest = bridge.read("poster.psd", work_dir=folder)
    report = bridge.apply("poster.psd", {"#1D4ED8": "#E63946"}, option="A")
    report.ok, report.summary()

The scripts sent into Photoshop are plain files in `scripts/` (photoshop_*.jsx), one job each.
pywin32 is imported only when a bridge call needs it, so Colorroom runs without it. No window
code in here.
"""

from __future__ import annotations

import json
import re
import shutil
import sys
import tempfile
import threading
from pathlib import Path
from typing import Mapping, Optional

from ..manifest import MANIFEST_SUFFIX, PREVIEW_SUFFIX, Manifest, parse_manifest
from .base import (
    AppBusy,
    ApplyReport,
    Bridge,
    BridgeError,
    NotAvailable,
    Skipped,
    check_targets,
    compose_script,
    file_hash,
    normalize_mapping,
    output_path,
    verify,
)

PROGID = "Photoshop.Application"
EXTENSIONS = (".psd", ".psb")

# COM results that mean "busy, try later" and "it went away".
_BUSY = {-2147418111, -2147417846}  # RPC_E_CALL_REJECTED, RPC_E_SERVERCALL_RETRYLATER
_GONE = {-2147023174, -2147023170, -2147417848, -2147221021}  # server unavailable, call failed, disconnected, unavailable
_NEVER_SHOW_DEBUGGER = 1  # PsJavaScriptExecutionMode: a script error must never open a window

_registered: Optional[bool] = None

EFFECTS = {
    "solidFill": "Color Overlay",
    "frameFX": "Stroke effect",
    "dropShadow": "Drop Shadow",
    "innerShadow": "Inner Shadow",
    "outerGlow": "Outer Glow",
    "innerGlow": "Inner Glow",
    "bevelEmboss": "Bevel & Emboss",
    "gradientFill": "Gradient Overlay",
    "chromeFX": "Satin",
    "patternFill": "Pattern Overlay",
}
SKIP_REASONS = {
    "hidden": "hidden layer (change hidden layers is off)",
    "locked": "locked in Photoshop",
    "group-locked": "inside a locked group",
    "missing-font": "its font is not installed, and changing the text would replace the font",
}


def _com():
    try:
        import pythoncom
        import pywintypes
        import win32com.client
    except ImportError:
        raise NotAvailable(
            "Colorroom needs the pywin32 package to talk to Photoshop, and it is not installed.",
            code="no-pywin32",
        ) from None
    return pythoncom, pywintypes, win32com.client


def registered() -> bool:
    """One read-only registry lookup: is Photoshop's automation interface installed?"""
    global _registered
    if _registered is None:
        if sys.platform != "win32":
            _registered = False
        else:
            import winreg

            try:
                with winreg.OpenKey(winreg.HKEY_CLASSES_ROOT, PROGID + "\\CLSID"):
                    _registered = True
            except OSError:
                _registered = False
    return _registered


def _posix(path) -> str:
    return str(Path(path)).replace("\\", "/")


# ---- turning Photoshop's answer into a manifest ----------------------------------------------

_STOP = re.compile(r"/(?:colors|Clrs)/(\d+)/(?:color|Clr )$")


def _stop_words(key: str, stop: Optional[dict]) -> str:
    match = _STOP.search(key)
    words = f"stop {int(match.group(1)) + 1}" if match else "stop"
    if stop and isinstance(stop.get("location"), (int, float)):
        words += f" ({round(stop['location'] / 4096 * 100)}%)"
    return words


def describe_part(layer: dict, part: dict) -> tuple[str, str]:
    """(where, label in plain words) for one color found by the scripts."""
    key = str(part.get("key", ""))
    head = key.split("/")
    shape = layer.get("ps_kind") == 4
    off = " (off)" if part.get("off") else ""
    if head[0] == "adj" and len(head) > 2:
        kind = head[2]
        if kind == "solidColorLayer":
            return "fill", ("shape fill" if shape else "fill color") + off
        if kind == "gradientLayer":
            return "gradient", ("shape gradient " if shape else "gradient fill ") + _stop_words(key, part.get("stop")) + off
        if kind == "gradientMapClass":
            return "gradient", "gradient map " + _stop_words(key, part.get("stop"))
        name = layer.get("detail") or kind
        if "/gradient/" in key or "/Grad/" in key:
            return "gradient", f"{name} gradient {_stop_words(key, part.get('stop'))}"
        return "adjustment", f"{name} color"
    if head[0] == "stroke":
        return "stroke", "shape stroke" + off
    if head[0] == "fx" and len(head) > 1:
        base = head[1][:-5] if head[1].endswith("Multi") else head[1]
        name = EFFECTS.get(base, base)
        if "/gradient/" in key or "/Grad/" in key:
            name += " gradient " + _stop_words(key, part.get("stop"))
        elif key.endswith("/highlightColor"):
            name += " highlight"
        elif key.endswith("/shadowColor"):
            name += " shadow"
        return "effect", name + off
    if head[0] == "text":
        span = part.get("range")
        if isinstance(span, list) and len(span) == 2:
            end = int(span[1])
            if isinstance(layer.get("text"), str):
                end = min(end, len(layer["text"]))  # Photoshop's last run also covers the paragraph's end mark
            return "text", f"text color, characters {int(span[0]) + 1} to {end}"
        return "text", "text color"
    return "fill", key


def manifest_data(raw: dict, preview_name: str = "") -> dict:
    """The JSON of a manifest file (manifest.py's format) from what photoshop_read.jsx returned."""
    layers = []
    pictures = 0
    for item in raw.get("layers") or []:
        parts, colors = [], []
        for part in item.get("parts") or []:
            if not part.get("hex"):
                continue
            where, label = describe_part(item, part)
            parts.append({"key": part["key"], "where": where, "hex": part["hex"], "label": label, "off": bool(part.get("off"))})
            if not part.get("off") and {"where": where, "hex": part["hex"]} not in colors:
                colors.append({"where": where, "hex": part["hex"]})
        if item.get("kind") in ("image", "smart-object"):
            pictures += 1
        layers.append(
            {
                "id": item.get("id"),
                "name": item.get("name"),
                "path": item.get("path"),
                "kind": item.get("kind"),
                "detail": item.get("detail"),
                "visible": item.get("visible", True),
                "locked": item.get("locked", False),
                "lock": item.get("lock", ""),
                "bounds": item.get("bounds"),
                "parts": parts,
                "colors": colors,
                "notes": item.get("notes") or [],
                "signature": item.get("signature", ""),
            }
        )
    notes = list(raw.get("notes") or [])
    if raw.get("color_mode") and raw.get("color_mode") != "RGB":
        notes.append(f"This document is in {raw['color_mode']} mode. Colorroom shows its colors as RGB and can only change RGB documents for now.")
    if raw.get("layer_comps"):
        notes.append(
            f"This file has {raw['layer_comps']} layer comps. A comp that remembers layer styles can bring back old effect colors when you switch to it."
        )
    if pictures:
        notes.append(f"{pictures} picture {'layer keeps' if pictures == 1 else 'layers keep'} their pixels as they are.")
    return {
        "format": "colorroom-manifest",
        "manifest_version": 1,
        "program": "Photoshop",
        "program_version": raw.get("program_version", ""),
        "document": raw.get("document", ""),
        "source": raw.get("source") or None,
        "color_mode": raw.get("color_mode", "RGB"),
        "width": raw.get("width", 0),
        "height": raw.get("height", 0),
        "resolution": raw.get("resolution", 0),
        "bit_depth": raw.get("bit_depth", 8),
        "color_profile": raw.get("color_profile", ""),
        "layer_comps": raw.get("layer_comps", 0),
        "preview": preview_name or None,
        "layers": layers,
        "notes": notes,
    }


def manifest_from(raw: dict, path) -> Manifest:
    """A Manifest object straight from the scripts' answer, without writing a file."""
    return parse_manifest(json.dumps(manifest_data(raw)), path)


def script_message(data: dict) -> str:
    """The plain message for a failed script."""
    code = data.get("code", "")
    message = str(data.get("message") or "")
    if code and code != "script":
        return message
    lowered = message.lower()
    if "locked" in lowered:
        return "Photoshop refused because a layer is locked. Unlock it in Photoshop, or turn on changing locked layers."
    if "not currently available" in lowered or "modal" in lowered:
        return "Photoshop is busy. Close any open dialog box or finish what you are doing there, then try again."
    return f"Photoshop reported a problem: {message}"


# ---- the bridge -------------------------------------------------------------------------------


class PhotoshopBridge(Bridge):
    """Photoshop on this computer, through COM. One object can serve several threads."""

    program = "Photoshop"
    extensions = EXTENSIONS

    def __init__(self, *, start: bool = True, answer_timeout: float = 30.0) -> None:
        self.start = start  # may Colorroom start Photoshop when it is not open?
        self.answer_timeout = answer_timeout
        self._local = threading.local()

    # -- reaching Photoshop

    def available(self) -> bool:
        if sys.platform != "win32":
            return False
        try:
            _com()
        except NotAvailable:
            return False
        return registered()

    def _init_thread(self):
        pythoncom, pywintypes, client = _com()
        if not getattr(self._local, "ready", False):
            try:
                pythoncom.CoInitialize()
            except pythoncom.com_error:
                pass  # this thread already set COM up its own way (a window's main thread): use that
            self._local.ready = True
        return pythoncom, pywintypes, client

    def running(self) -> bool:
        """Looks Photoshop up in Windows' list of running programs, without asking it anything,
        so this never waits on a busy Photoshop."""
        if not self.available():
            return False
        pythoncom, pywintypes, _ = self._init_thread()
        try:
            pythoncom.GetActiveObject(pywintypes.IID(PROGID))
            return True
        except Exception:
            return False

    def _app(self, start: bool):
        pythoncom, pywintypes, client = self._init_thread()
        app = getattr(self._local, "app", None)
        if app is not None:
            return app
        if not registered():
            raise NotAvailable("Photoshop is not installed on this computer, or its automation interface is missing.", code="not-installed")
        try:
            app = client.GetActiveObject(PROGID)
        except Exception:
            if not start:
                raise NotAvailable("Photoshop is not open. Open it, or let Colorroom start it.", code="not-running") from None
            try:
                app = client.Dispatch(PROGID)
            except pywintypes.com_error as error:
                raise self._com_error(error, "Colorroom could not start Photoshop.") from None
        self._local.app = app
        return app

    def _check_answers(self) -> bool:
        """Ask Photoshop a trivial question in another thread; no answer in time means busy.

        Returns False when Photoshop is not running at all. Everything that talks to
        Photoshop happens inside the timed thread, so a busy Photoshop cannot hang the caller.
        """
        outcome: dict = {}

        def ask():
            try:
                pythoncom, pywintypes, client = _com()
                pythoncom.CoInitialize()
                try:
                    try:
                        unknown = pythoncom.GetActiveObject(pywintypes.IID(PROGID))
                    except pywintypes.com_error:
                        outcome["absent"] = True
                        return
                    app = client.Dispatch(unknown.QueryInterface(pythoncom.IID_IDispatch))
                    outcome["answer"] = app.DoJavaScript("'ok'", None, _NEVER_SHOW_DEBUGGER)
                    del app, unknown
                finally:
                    pythoncom.CoUninitialize()
            except Exception as error:  # reported below
                outcome["error"] = error

        worker = threading.Thread(target=ask, name="colorroom-photoshop-probe", daemon=True)
        worker.start()
        worker.join(self.answer_timeout)
        if worker.is_alive():
            raise AppBusy(
                "Photoshop is not answering. If a dialog box is open in Photoshop, close it, then try again.",
                code="busy",
            )
        if outcome.get("absent"):
            self._local.app = None
            return False
        error = outcome.get("error")
        if error is not None:
            _, pywintypes, _ = _com()
            if isinstance(error, pywintypes.com_error):
                raise self._com_error(error)
            raise BridgeError("Colorroom could not reach Photoshop.", detail=repr(error), code="unreachable")
        return True

    def _com_error(self, error, fallback: str = "") -> BridgeError:
        code = error.args[0] if error.args else 0
        detail = repr(error.args)
        if code in _BUSY:
            return AppBusy(
                "Photoshop is busy. Close any open dialog box in Photoshop or finish what you are doing there, then try again.",
                detail=detail,
                code="busy",
            )
        if code in _GONE:
            self._local.app = None
            return BridgeError("Photoshop closed or stopped answering. Open it again and try once more.", detail=detail, code="gone")
        text = ""
        try:
            text = str(error.args[2][2] or "")
        except (IndexError, TypeError):
            pass
        if text:
            return BridgeError(script_message({"message": text}), detail=detail, code="script")
        return BridgeError(fallback or "Photoshop reported a problem Colorroom does not recognise.", detail=detail, code="com")

    def run_script(self, script, args: Optional[dict] = None, *, start: Optional[bool] = None) -> dict:
        """Send one script from `scripts/` (or a path) and return its JSON answer."""
        code = compose_script(script, args)
        if not registered():
            raise NotAvailable("Photoshop is not installed on this computer, or its automation interface is missing.", code="not-installed")
        self._init_thread()
        if not self._check_answers():
            self._local.app = None  # not running: _app starts it when allowed
        _, pywintypes, _ = _com()
        for attempt in (1, 2):
            app = self._app(self.start if start is None else start)
            try:
                text = app.DoJavaScript(code, None, _NEVER_SHOW_DEBUGGER)
                break
            except pywintypes.com_error as error:
                problem = self._com_error(error)
                if problem.code != "gone" or attempt == 2:  # a Photoshop restarted since the last call: connect again once
                    raise problem from None
        try:
            data = json.loads(text)
        except (TypeError, ValueError):
            raise BridgeError("Photoshop gave an answer Colorroom does not understand.", detail=str(text)[:500], code="answer") from None
        if not isinstance(data, dict):
            raise BridgeError("Photoshop gave an answer Colorroom does not understand.", detail=str(text)[:500], code="answer")
        if not data.get("ok"):
            raise BridgeError(script_message(data), detail=f"{data.get('message')} (line {data.get('line')})", code=data.get("code", "script"))
        return data

    # -- what is open

    def state(self, *, start: Optional[bool] = None) -> dict:
        """`{version, documents: [{name, path, saved, active}]}`."""
        return self.run_script("photoshop_probe.jsx", {}, start=start)

    def active_document(self) -> Optional[dict]:
        if not self.running():
            return None
        for document in self.state(start=False).get("documents", []):
            if document.get("active"):
                return document
        return None

    # -- read

    def read(self, path=None, *, work_dir=None, preview: bool = True, leave_open: bool = False) -> Manifest:
        """Every layer with its exact colors, as a Manifest; the manifest and preview are saved
        in `work_dir` (a "Colorroom" folder in the system's temporary folder by default)."""
        if path is not None:
            source = Path(path).resolve()
            if not source.is_file():
                raise BridgeError(f"Colorroom cannot find the file {source.name}.", code="missing")
            stem = source.stem
        else:
            source = None
            front = self.active_document()
            if front is None:
                raise BridgeError("There is no document open in Photoshop.", code="no-document")
            stem = Path(front["name"]).stem
        work = Path(work_dir) if work_dir is not None else Path(tempfile.gettempdir()) / "Colorroom"
        work.mkdir(parents=True, exist_ok=True)
        stem = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", stem) or "document"
        preview_file = work / (stem + PREVIEW_SUFFIX)
        if preview and preview_file.exists():
            preview_file.unlink()
        raw = self.run_script(
            "photoshop_read.jsx",
            {"path": _posix(source) if source else "", "preview": _posix(preview_file) if preview else "", "leave_open": bool(leave_open)},
        )
        has_preview = preview and preview_file.is_file()
        data = manifest_data(raw, preview_file.name if has_preview else "")
        manifest_file = work / (stem + MANIFEST_SUFFIX)
        text = json.dumps(data, indent=2, ensure_ascii=False) + "\n"
        manifest_file.write_text(text, encoding="utf-8", newline="\n")
        return parse_manifest(text, manifest_file)

    # -- apply

    def apply(
        self,
        source_path,
        mapping: Mapping[str, str],
        save_as=None,
        *,
        option: str = "A",
        change_hidden: bool = True,
        change_locked: bool = False,
        leave_open: bool = False,
        overwrite: bool = False,
        report_path=None,
    ) -> ApplyReport:
        """Change `mapping`'s colors in a copy of `source_path` and check the saved copy.

        The copy is `save_as`, or "name - Colorroom <option>.psd" beside the original. The
        original is never opened by Photoshop: Colorroom copies the file first and Photoshop
        works on the copy. Layer kinds Colorroom cannot change exactly (pixel layers, smart
        object contents) are left alone and listed in the report.
        """
        changes = normalize_mapping(mapping)
        if not changes:
            raise BridgeError("No color is set to change, so there is nothing to export.", code="no-changes")
        target = Path(save_as) if save_as is not None else output_path(source_path, option)
        source, target = check_targets(source_path, target, self.extensions, overwrite)
        report = ApplyReport(self.program, str(source), None, dict(changes))
        original = file_hash(source)
        for document in self.state().get("documents", []):
            path = Path(document.get("path") or "")
            if document.get("path") and str(path).casefold() == str(target).casefold():
                raise BridgeError(f"{target.name} is open in Photoshop. Close it there and try again.", code="already-open")
            if document.get("path") and str(path).casefold() == str(source).casefold() and not document.get("saved", True):
                report.notes.append(
                    f"{source.name} has unsaved changes in Photoshop. Colorroom used the version saved on disk; your open document was not touched."
                )
        shutil.copyfile(source, target)
        try:
            raw = self.run_script(
                "photoshop_apply.jsx",
                {"path": _posix(target), "map": changes, "hidden": bool(change_hidden), "locked": bool(change_locked)},
            )
        except BaseException:
            target.unlink(missing_ok=True)  # only the copy made a moment ago
            raise
        report.saved = str(target)
        before = manifest_from(raw["before"], target)
        after = manifest_from(self.run_script("photoshop_read.jsx", {"path": _posix(target), "preview": "", "leave_open": bool(leave_open)}), target)
        verify(before, after, raw.get("changes") or [], report)
        self._explain(report, before, raw)
        report.checks["the original file is unchanged"] = file_hash(source) == original
        if report_path is not None:
            report.write(report_path)
        return report

    @staticmethod
    def _explain(report: ApplyReport, before: Manifest, raw: dict) -> None:
        """Skipped layers and Photoshop's own errors, in plain words."""
        listed = set()
        for item in raw.get("skipped") or []:
            layer = before.layer(int(item.get("id", -1)))
            if layer is None:
                continue
            reason = SKIP_REASONS.get(item.get("reason"), str(item.get("reason")))
            if item.get("reason") == "missing-font":
                reason = f"text color not changed: the font {item.get('detail')} is not installed, and changing the text would replace it"
            elif item.get("reason") == "group-locked":
                reason = f"inside the locked group “{item.get('detail')}”"
            report.skipped.append(Skipped(layer.id, layer.name, layer.path, layer.kind, reason))
            listed.add(layer.id)
        for layer in before.layers:
            if layer.id in listed:
                continue
            if layer.kind == "smart-object":
                report.skipped.append(Skipped(layer.id, layer.name, layer.path, layer.kind, "smart object: its contents are left as they are"))
            elif layer.kind == "image":
                what = "background" if layer.detail == "background" else layer.detail or "pixel layer"
                report.skipped.append(Skipped(layer.id, layer.name, layer.path, layer.kind, f"{what}: its pixels are left as they are"))
        for error in raw.get("errors") or []:
            layer = before.layer(int(error.get("id", -1)))
            where = f"“{layer.path}”" if layer else "A layer"
            report.problems.append(f"{where}: Photoshop could not change the {error.get('part', 'colors')}: {error.get('message', '')}")
        if before.layer_comps:
            report.notes.append(
                f"This file has {before.layer_comps} layer comps. Comps that remember layer styles may bring back old effect colors when you switch to them."
            )
