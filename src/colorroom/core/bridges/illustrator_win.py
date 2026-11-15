"""Route A for Illustrator on Windows: Colorroom drives the person's own Illustrator through COM.

Made by Fable 5.2 (start, 2026-11-13) and Opus 5.5 (2026-11-14), for review by the other model.

Illustrator itself opens a copy of the .ai file, sets the exact new colors on the vector objects
(path fills and strokes, compound paths, objects inside groups and clipping groups, text
ranges), changes global and spot swatches and gradient stops in their definition so everything
that uses them follows, and saves the copy as .ai with PDF compatibility on. Nothing is
rasterized, no object is added, removed or moved, and placed pictures are never touched.
Colorroom then opens the saved copy again and compares every color, every layer, every object
and every artboard with what it expected (`base.verify` plus the checks below).

    bridge = IllustratorBridge()
    bridge.available()                                  # installed + pywin32; never starts it
    manifest = bridge.read("logo.ai", work_dir=folder)  # every object and its exact colors
    report = bridge.apply("logo.ai", {"#1D4ED8": "#E63946"}, option="A")
    report.ok, report.summary()                         # "logo - Colorroom A.ai" beside it

In a CMYK document the hex codes are the ones Illustrator converts the CMYK values to, and a
new hex is converted to CMYK by Illustrator (the document stays CMYK); the report says so.

The scripts sent into Illustrator are plain files in `scripts/` (illustrator_*.jsx), one job
each. pywin32 is imported only when a bridge call needs it. No window code in here.
"""

from __future__ import annotations

import json
import re
import shutil
import sys
import tempfile
import threading
import time
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

PROGID = "Illustrator.Application"
EXTENSIONS = (".ai",)
PLAIN = ("rgb", "cmyk", "gray", "lab")  # color types that are set directly on an object
SWATCHED = ("global", "spot")  # color types that point at a swatch

# COM results that mean "busy, try later" and "it went away".
_BUSY = {-2147418111, -2147417846}  # RPC_E_CALL_REJECTED, RPC_E_SERVERCALL_RETRYLATER
_GONE = {-2147023174, -2147023170, -2147417848, -2147221021}
_NEVER_SHOW_DEBUGGER = 1  # AiJavaScriptExecutionMode.aiNeverStartDebugger
_BUSY_RETRIES = 6  # a rejected call is asked again this many times...
_BUSY_WAIT = 0.5  # ...this many seconds apart, before Colorroom says Illustrator is busy

_registered: Optional[bool] = None

DETAILS = {
    "layer": "layer",
    "path": "path",
    "compound": "compound path",
    "group": "group",
    "clip-group": "clipping group",
    "text": "text frame",
    "placed": "linked picture",
    "raster": "embedded picture",
    "mesh": "gradient mesh",
    "symbol": "symbol",
    "plugin": "blend or effect object",
    "graph": "graph",
    "legacy-text": "legacy text",
    "non-native": "non-native art",
    "guide": "guide",
    "other": "other object",
}
KINDS = {
    "path": "shape",
    "compound": "shape",
    "text": "text",
    "placed": "image",
    "raster": "image",
    "group": "group",
    "clip-group": "group",
    "layer": "group",
}
#: Object kinds Colorroom never changes, and why, in plain words.
LEFT_ALONE = {
    "placed": "linked picture: left exactly as it is",
    "raster": "embedded picture: its pixels are left as they are",
    "mesh": "gradient mesh: its colors are left as they are",
    "symbol": "symbol: change the symbol itself in Illustrator",
    "plugin": "blend or effect object: left as it is",
    "graph": "graph: left as it is",
    "legacy-text": "legacy text: update it in Illustrator first",
    "non-native": "non-native art: left as it is",
}


def _com():
    try:
        import pythoncom
        import pywintypes
        import win32com.client
    except ImportError:
        raise NotAvailable(
            "Colorroom needs the pywin32 package to talk to Illustrator, and it is not installed.",
            code="no-pywin32",
        ) from None
    return pythoncom, pywintypes, win32com.client


def registered() -> bool:
    """One read-only registry lookup: is Illustrator's automation interface installed?"""
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


# ---- colors, as the scripts describe them -----------------------------------------------------


def color_text(color: Optional[dict]) -> str:
    """A short plain description: "#1D4ED8", "CMYK 85/72/0/0 (#3D59A8)", "global swatch Brand Green"."""
    if not color:
        return "none"
    kind = color.get("type")
    if kind == "cmyk":
        values = "/".join(f"{value:g}" for value in (round(v, 1) for v in color.get("values") or []))
        return f"CMYK {values} ({color.get('hex')})"
    if kind in SWATCHED:
        tint = color.get("tint", 100)
        tint_words = "" if tint in (100, None) else f", {tint:g}% tint"
        return f"{'global swatch' if kind == 'global' else 'spot color'} {color.get('swatch')} ({color.get('hex')}{tint_words})"
    if kind == "gradient":
        return f"gradient {color.get('gradient')}"
    if kind == "pattern":
        return f"pattern {color.get('pattern')}"
    return str(color.get("hex") or kind)


def same_color(a: Optional[dict], b: Optional[dict], tolerance: float = 0.01) -> bool:
    """True when two described colors are the same color stored the same way."""
    if not a or not b:
        return not a and not b
    if a.get("type") != b.get("type"):
        return False
    kind = a.get("type")
    if kind in PLAIN:
        values_a, values_b = a.get("values") or [], b.get("values") or []
        return len(values_a) == len(values_b) and all(abs(x - y) <= tolerance for x, y in zip(values_a, values_b)) and a.get("hex") == b.get("hex")
    if kind in SWATCHED:
        return a.get("swatch") == b.get("swatch") and abs((a.get("tint") or 0) - (b.get("tint") or 0)) <= tolerance
    if kind == "gradient":
        return a.get("gradient") == b.get("gradient")
    if kind == "pattern":
        return a.get("pattern") == b.get("pattern")
    return a == b


# ---- turning Illustrator's answer into a manifest ----------------------------------------------


def _ordered(raw: dict) -> list[tuple[str, dict]]:
    """Layers and objects in document order: each layer, then its objects, then its sublayers."""
    by_layer: dict[str, list[dict]] = {}
    for item in raw.get("items") or []:
        by_layer.setdefault(str(item.get("id", "")).split("/", 1)[0], []).append(item)
    out: list[tuple[str, dict]] = []
    for layer in raw.get("layers") or []:
        out.append(("layer", layer))
        out.extend(("item", item) for item in by_layer.get(layer.get("id"), []))
    return out


def _gradients(raw: dict) -> dict[str, dict]:
    return {gradient.get("name"): gradient for gradient in raw.get("gradients") or []}


def _spots(raw: dict) -> dict[str, dict]:
    return {spot.get("name"): spot for spot in raw.get("spots") or []}


def _part_hex(color: Optional[dict], spots: dict[str, dict]) -> Optional[str]:
    """The hex a part shows in the manifest: the color itself, or its swatch's full color."""
    if not color:
        return None
    if color.get("type") in SWATCHED:
        spot = spots.get(color.get("swatch")) or {}
        return (spot.get("color") or {}).get("hex") or color.get("hex")
    return color.get("hex")


def _swatch_words(color: dict) -> str:
    if color.get("type") not in SWATCHED:
        return ""
    tint = color.get("tint", 100)
    tint_words = "" if tint in (100, None) else f", {tint:g}% tint"
    return f" ({'global swatch' if color['type'] == 'global' else 'spot color'} {color.get('swatch')}{tint_words})"


def _per_character(item: dict) -> list[tuple[Optional[dict], Optional[dict]]]:
    out: list[tuple[Optional[dict], Optional[dict]]] = []
    for run in item.get("runs") or []:
        out.extend([(run.get("fill"), run.get("stroke"))] * int(run.get("length") or 0))
    return out


def item_parts(item: dict, raw: dict, runs_from: Optional[dict] = None) -> list[dict]:
    """Every color of one object as manifest parts with stable keys.

    Text parts are keyed by the character runs of `runs_from` (the same object before a change)
    when it is given, so a read-back compares the same characters even when Illustrator joins
    or splits runs.
    """
    spots, gradients = _spots(raw), _gradients(raw)
    parts: list[dict] = []

    def add(key, where, color, label):
        if not color:
            return
        kind = color.get("type")
        if kind == "gradient":
            gradient = gradients.get(color.get("gradient")) or {}
            stops = gradient.get("stops") or []
            for number, stop in enumerate(stops):
                hex_ = _part_hex(stop.get("color"), spots)
                if hex_:
                    percent = round(float(stop.get("ramp") or 0))
                    words = _swatch_words(stop.get("color") or {})
                    parts.append(
                        {"key": f"{key}/gradient/{number}", "where": "gradient", "hex": hex_,
                         "label": f"{label} gradient {color.get('gradient')}, stop {number + 1} ({percent}%){words}"}
                    )
            return
        hex_ = _part_hex(color, spots)
        if hex_:
            parts.append({"key": key, "where": where, "hex": hex_, "label": label + _swatch_words(color)})

    if item.get("kind") in ("path", "compound"):
        add("fill", "fill", item.get("fill"), "fill")
        add("stroke", "stroke", item.get("stroke"), "stroke")
    elif item.get("kind") == "text":
        layout = runs_from if runs_from is not None else item
        characters = _per_character(item)
        for run in layout.get("runs") or []:
            start, length = int(run.get("start") or 0), int(run.get("length") or 0)
            span = characters[start : start + length]
            words = f"characters {start + 1} to {start + length}"
            for slot, where, label in ((0, "text", "text color"), (1, "stroke", "text outline")):
                if run.get("fill" if slot == 0 else "stroke") is None and runs_from is not None:
                    continue
                if runs_from is None:
                    color = run.get("fill" if slot == 0 else "stroke")
                elif len(span) == length and span and all(same_color(c[slot], span[0][slot]) for c in span):
                    color = span[0][slot]
                else:
                    continue  # the characters no longer share one color: the check reports it
                add(f"text/{start}-{start + length}/{'fill' if slot == 0 else 'stroke'}", where, color, f"{label}, {words}")
    return parts


def _signature(item: dict) -> str:
    """Everything about an object except its colors, so a read-back can prove only colors moved."""
    def how(color):
        if not color:
            return None
        kind = color.get("type")
        if kind in PLAIN:
            return "plain"
        return [kind, color.get("swatch"), color.get("tint"), color.get("gradient"), color.get("pattern")]

    keep = {
        key: item.get(key)
        for key in ("id", "kind", "name", "layer", "locked", "hidden", "clipping", "children", "paths", "chars", "text", "fonts", "strokeWidth")
        if key in item
    }
    keep["bounds"] = [round(float(value), 2) for value in item.get("bounds") or []]
    keep["fill"] = how(item.get("fill"))
    keep["stroke"] = how(item.get("stroke"))
    if item.get("runs") is not None:
        keep["runs"] = [[how(fill), how(stroke)] for fill, stroke in _per_character(item)]
    return json.dumps(keep, sort_keys=True, ensure_ascii=False)


def _board(raw: dict) -> tuple[float, float, float, float]:
    boards = (raw.get("document") or {}).get("artboards") or []
    if boards:
        left, top, right, bottom = boards[0]["rect"]
        return float(left), float(top), float(right), float(bottom)
    return 0.0, 0.0, 0.0, 0.0


def manifest_data(raw: dict, preview_name: str = "", runs_from: Optional[dict] = None, version: str = "") -> dict:
    """The JSON of a manifest file (manifest.py's format) from what illustrator_read.jsx returned.

    Every Illustrator layer and sublayer becomes a "group" entry and every object an entry of
    its own, in document order; the position in that order is the id. Bounds are fractions of
    the first artboard.
    """
    info = raw.get("document") or {}
    left, top, right, bottom = _board(raw)
    width, height = max(0.0, right - left), max(0.0, top - bottom)
    before_items = {item.get("id"): item for item in (runs_from or {}).get("items") or []}
    entries = []
    for number, (what, item) in enumerate(_ordered(raw), start=1):
        if what == "layer":
            entries.append(
                {
                    "id": number,
                    "name": item.get("name"),
                    "path": item.get("path"),
                    "kind": "group",
                    "detail": "layer" if "." not in str(item.get("id")) else "sublayer",
                    "visible": not item.get("hiddenWithParents"),
                    "locked": bool(item.get("lockedWithParents")),
                    "lock": "all" if item.get("locked") else "",
                    "bounds": None,
                    "parts": [],
                    "signature": json.dumps(
                        {key: item.get(key) for key in ("id", "name", "path", "visible", "locked", "printable", "items", "sublayers")},
                        sort_keys=True,
                        ensure_ascii=False,
                    ),
                }
            )
            continue
        kind = item.get("kind", "other")
        name = item.get("name") or f"<{DETAILS.get(kind, 'object')}>"
        bounds = item.get("bounds")
        box = None
        if bounds and len(bounds) == 4:
            box = [bounds[0] - left, top - bounds[1], bounds[2] - left, top - bounds[3]]
        notes = []
        if item.get("missingFonts"):
            notes.append("font not installed: " + ", ".join(item["missingFonts"]))
        for slot in ("fill", "stroke"):
            color = item.get(slot) or {}
            if color.get("type") == "pattern":
                notes.append(f"{slot}: pattern {color.get('pattern')}, left as it is")
        entries.append(
            {
                "id": number,
                "name": name,
                "path": " / ".join(part for part in (item.get("layer"), *_group_names(raw, item), name) if part),
                "kind": KINDS.get(kind, "other"),
                "detail": DETAILS.get(kind, "other object") + (" (clipping path)" if item.get("clipping") else ""),
                "visible": not item.get("hidden"),
                "locked": bool(item.get("locked")),
                "lock": "all" if item.get("locked") else "",
                "bounds": box,
                "parts": item_parts(item, raw, before_items.get(item.get("id")) if runs_from is not None else None),
                "notes": notes,
                "signature": _signature(item),
            }
        )
    notes = []
    if info.get("colorMode") == "CMYK":
        notes.append(
            "This document is in CMYK. Colorroom shows each color as the hex Illustrator converts it to; "
            "new colors are converted to CMYK by Illustrator, so they can come back slightly different."
        )
    notes.extend(swatch_notes(raw))
    counts = raw.get("counts") or {}
    pictures = (counts.get("placedItems") or 0) + (counts.get("rasterItems") or 0)
    if pictures:
        notes.append(f"{pictures} {'picture is' if pictures == 1 else 'pictures are'} left exactly as {'it is' if pictures == 1 else 'they are'}.")
    return {
        "format": "colorroom-manifest",
        "manifest_version": 1,
        "program": "Illustrator",
        "program_version": version,
        "document": info.get("name", ""),
        "source": info.get("path") or None,
        "color_mode": info.get("colorMode", "RGB"),
        "width": round(width),
        "height": round(height),
        "resolution": 72,
        "bit_depth": 8,
        "preview": preview_name or None,
        "layers": entries,
        "notes": notes,
    }


def _group_names(raw: dict, item: dict) -> list[str]:
    """Names of the groups an object sits in, outermost first."""
    item_id = str(item.get("id", ""))
    if "/" not in item_id:
        return []
    layer, rest = item_id.split("/", 1)
    steps = rest.split(".")
    by_id = {str(other.get("id")): other for other in raw.get("items") or []}
    names = []
    for depth in range(1, len(steps)):
        group = by_id.get(layer + "/" + ".".join(steps[:depth]))
        if group is not None:
            names.append(group.get("name") or f"<{DETAILS.get(group.get('kind'), 'group')}>")
    return names


def swatch_users(raw: dict) -> dict[str, list[str]]:
    """Global and spot swatch name -> the objects (and gradients) that use it."""
    users: dict[str, list[str]] = {}
    gradients_used: set[str] = set()

    def note(color, who):
        if not color:
            return
        if color.get("type") in SWATCHED:
            names = users.setdefault(color.get("swatch"), [])
            if who not in names:
                names.append(who)
        elif color.get("type") == "gradient":
            gradients_used.add(color.get("gradient"))

    for item in raw.get("items") or []:
        who = item.get("name") or f"<{DETAILS.get(item.get('kind'), 'object')}>"
        note(item.get("fill"), who)
        note(item.get("stroke"), who)
        for run in item.get("runs") or []:
            note(run.get("fill"), who)
            note(run.get("stroke"), who)
    for gradient in raw.get("gradients") or []:
        if gradient.get("name") in gradients_used:
            for stop in gradient.get("stops") or []:
                note(stop.get("color"), f"gradient {gradient.get('name')}")
    return users


def swatch_notes(raw: dict) -> list[str]:
    users = swatch_users(raw)
    notes = []
    for spot in raw.get("spots") or []:
        if spot.get("kind") == "registration":
            continue
        who = users.get(spot.get("name")) or []
        if not who:
            continue
        kind = "Global swatch" if spot.get("kind") == "global" else "Spot color"
        names = ", ".join(who[:4]) + (f" and {len(who) - 4} more" if len(who) > 4 else "")
        notes.append(
            f"{kind} “{spot.get('name')}” ({(spot.get('color') or {}).get('hex')}) is used by {len(who)} "
            f"{'object' if len(who) == 1 else 'objects'} ({names}); changing its color changes all of them."
        )
    return notes


def manifest_from(raw: dict, path, runs_from: Optional[dict] = None) -> Manifest:
    """A Manifest object straight from the scripts' answer, without writing a file."""
    return parse_manifest(json.dumps(manifest_data(raw, runs_from=runs_from)), path)


# ---- what an apply should do, worked out in Python from the "before" read ------------------------


def eligible(item: dict, *, change_locked: bool, change_hidden: bool, change_missing_fonts: bool) -> Optional[str]:
    """None when the object's own colors may change, else the reason, in plain words."""
    kind = item.get("kind")
    if kind in LEFT_ALONE:
        return LEFT_ALONE[kind]
    if kind not in ("path", "compound", "text"):
        return "" if kind in ("group", "clip-group", "guide") else "left as it is"
    if item.get("locked") and not change_locked:
        return "locked (changing locked objects is off)"
    if item.get("hidden") and not change_hidden:
        return "hidden (changing hidden objects is off)"
    if kind == "text" and item.get("missingFonts") and not change_missing_fonts:
        return "its font is not installed (" + ", ".join(item["missingFonts"]) + "), so its text is left alone"
    if kind == "compound" and not item.get("paths"):
        return "compound path whose parts scripts cannot reach"
    return None


def expected_colors(before: dict, mapping: Mapping[str, str], targets: Mapping[str, dict], **rules) -> dict:
    """What every color of the document should be after the apply.

    Returns {"items": {id: {slot: color}}, "spots": {name: color}, "gradients": {name: [colors]},
    "swatches": {name: color}}, where an item slot is "fill", "stroke" or ("text", i, 0|1) per
    character.
    """
    change_swatches = rules.pop("change_swatches", True)

    def new(color):
        if color and color.get("type") in PLAIN and color.get("hex") in mapping:
            return dict(targets[color["hex"]])
        return color

    spots = {}
    for spot in before.get("spots") or []:
        color = spot.get("color")
        spots[spot.get("name")] = new(color) if spot.get("kind") != "registration" else color
    gradients = {gradient.get("name"): [new(stop.get("color")) for stop in gradient.get("stops") or []] for gradient in before.get("gradients") or []}
    swatches = {}
    for swatch in before.get("swatches") or []:
        color = swatch.get("color")
        swatches[swatch.get("name")] = new(color) if change_swatches else color
    items: dict[str, dict] = {}
    for item in before.get("items") or []:
        may = eligible(item, **rules) is None
        slots: dict = {}
        for slot in ("fill", "stroke"):
            if slot in item:
                slots[slot] = new(item[slot]) if may else item[slot]
        for index, (fill, stroke) in enumerate(_per_character(item)):
            slots[("text", index, 0)] = new(fill) if may else fill
            slots[("text", index, 1)] = new(stroke) if may else stroke
        items[item.get("id")] = slots
    return {"items": items, "spots": spots, "gradients": gradients, "swatches": swatches}


def future_snapshot(before: dict, expected: dict) -> dict:
    """The snapshot the saved file should give: `before` with every color as `expected` says.

    Text is written one run per character, so it compares the same characters whatever runs
    Illustrator keeps.
    """
    future = dict(before)
    future["spots"] = [dict(spot, color=expected["spots"].get(spot.get("name"), spot.get("color"))) for spot in before.get("spots") or []]
    future["gradients"] = [
        dict(gradient, stops=[dict(stop, color=color) for stop, color in zip(gradient.get("stops") or [], expected["gradients"].get(gradient.get("name"), []))])
        for gradient in before.get("gradients") or []
    ]
    future["swatches"] = [dict(swatch, color=expected["swatches"].get(swatch.get("name"), swatch.get("color"))) for swatch in before.get("swatches") or []]
    future_items = []
    for item in before.get("items") or []:
        slots = expected["items"].get(item.get("id"), {})
        copy = dict(item)
        for slot in ("fill", "stroke"):
            if slot in item:
                copy[slot] = slots.get(slot)
        if item.get("runs") is not None:
            copy["runs"] = [
                {"start": index, "length": 1, "fill": slots.get(("text", index, 0)), "stroke": slots.get(("text", index, 1))}
                for index in range(len(_per_character(item)))
            ]
        future_items.append(copy)
    future["items"] = future_items
    return future


def predicted_changes(before: dict, mapping: Mapping[str, str], targets: Mapping[str, dict], **rules) -> list[dict]:
    """The manifest parts that should hold a new hex: [{id, key, from, to}] for `base.verify`.

    Worked out in Python from the "before" read, not taken from what the script says it did.
    Parts that change through a swatch or a gradient are included, because the object shows
    the new color even when the object itself was not touched.
    """
    future = future_snapshot(before, expected_colors(before, mapping, targets, **rules))
    now = manifest_data(before)
    then = manifest_data(future, runs_from=before)
    changes = []
    for old, new in zip(now["layers"], then["layers"]):
        new_parts = {part["key"]: part for part in new["parts"]}
        for part in old["parts"]:
            other = new_parts.get(part["key"])
            if other and other["hex"] != part["hex"]:
                changes.append({"id": old["id"], "key": part["key"], "from": part["hex"], "to": other["hex"]})
    return changes


def rgb_targets(mapping: Mapping[str, str]) -> dict[str, dict]:
    """What illustrator_apply.jsx answers as `targets` in an RGB document (for tests and previews)."""
    out = {}
    for old, new in normalize_mapping(mapping).items():
        values = [int(new[1:3], 16), int(new[3:5], 16), int(new[5:7], 16)]
        out[old] = {"type": "rgb", "values": values, "hex": new, "asked": new}
    return out


def check_definitions(before: dict, after: dict, expected: dict, report: ApplyReport) -> None:
    """Swatches, spot colors and gradients: each must hold exactly what was expected."""
    spots_after = _spots(after)
    bad = []
    for name, wanted in expected["spots"].items():
        now = (spots_after.get(name) or {}).get("color")
        old = (_spots(before).get(name) or {}).get("color")
        if not same_color(now, wanted, 0.05):
            bad.append(f"{'swatch' if name else 'a swatch'} “{name}” should be {color_text(wanted)} but is {color_text(now)}")
        elif not same_color(old, wanted):
            report.notes.append(f"Swatch “{name}” changed from {color_text(old)} to {color_text(wanted)}; everything that uses it follows.")
    gradients_after = _gradients(after)
    for name, stops in expected["gradients"].items():
        now = [stop.get("color") for stop in (gradients_after.get(name) or {}).get("stops") or []]
        if len(now) != len(stops) or not all(same_color(a, b, 0.05) for a, b in zip(now, stops)):
            bad.append(f"gradient “{name}” does not hold the expected stop colors")
    swatches_after = {swatch.get("name"): swatch.get("color") for swatch in after.get("swatches") or []}
    for name, wanted in expected["swatches"].items():
        if (wanted or {}).get("type") in SWATCHED or (wanted or {}).get("type") in ("gradient", "pattern"):
            continue  # those swatches show their definition, checked above
        if not same_color(swatches_after.get(name), wanted, 0.05):
            bad.append(f"swatch “{name}” should be {color_text(wanted)} but is {color_text(swatches_after.get(name))}")
    report.problems.extend(text[0].upper() + text[1:] + "." for text in bad)
    report.checks["swatches, spot colors and gradients hold the new colors"] = not bad


def check_structure(before: dict, after: dict, report: ApplyReport) -> None:
    """Layers, artboards and object counts must be exactly as they were."""
    def layers(raw):
        return [{key: layer.get(key) for key in ("id", "name", "path", "visible", "locked", "printable", "items", "sublayers")} for layer in raw.get("layers") or []]

    def boards(raw):
        return [(board.get("name"), [round(float(v), 3) for v in board.get("rect") or []]) for board in (raw.get("document") or {}).get("artboards") or []]

    def shapes(raw):
        return [(item.get("id"), item.get("kind"), item.get("name")) for item in raw.get("items") or []]

    counts_before, counts_after = before.get("counts") or {}, after.get("counts") or {}
    report.checks["every layer and sublayer is still there, with the same names, visibility and locks"] = layers(before) == layers(after)
    report.checks["every artboard has the same name, size and position"] = boards(before) == boards(after)
    report.checks["every object is still there, of the same kind, in the same order"] = shapes(before) == shapes(after)
    report.checks["the same number of objects of every kind"] = {k: v for k, v in counts_before.items() if k not in ("swatches", "gradients", "spots")} == {
        k: v for k, v in counts_after.items() if k not in ("swatches", "gradients", "spots")
    }
    report.checks["nothing was rasterized, and the pictures are untouched"] = (
        counts_before.get("rasterItems") == counts_after.get("rasterItems")
        and counts_before.get("placedItems") == counts_after.get("placedItems")
        and [i.get("bounds") for i in before.get("items") or [] if i.get("kind") in ("placed", "raster")]
        == [i.get("bounds") for i in after.get("items") or [] if i.get("kind") in ("placed", "raster")]
    )
    report.checks["the document's color mode is the same"] = (before.get("document") or {}).get("colorMode") == (after.get("document") or {}).get("colorMode")
    for key in ("swatches", "gradients", "spots"):
        if counts_before.get(key) != counts_after.get(key):
            report.notes.append(f"Illustrator now lists {counts_after.get(key)} {key} instead of {counts_before.get(key)}.")


def script_message(data: dict) -> str:
    message = str(data.get("error") or data.get("message") or "")
    lowered = message.lower()
    if "not open" in lowered or "does not exist" in lowered or "already open" in lowered or "no document" in lowered:
        return message
    if "modal" in lowered or "busy" in lowered:
        return "Illustrator is busy. Close any open dialog box or finish what you are doing there, then try again."
    return f"Illustrator reported a problem: {message}"


# ---- the bridge -------------------------------------------------------------------------------


class IllustratorBridge(Bridge):
    """Illustrator on this computer, through COM. One object can serve several threads."""

    program = "Illustrator"
    extensions = EXTENSIONS

    def __init__(self, *, start: bool = True, answer_timeout: float = 30.0) -> None:
        self.start = start  # may Colorroom start Illustrator when it is not open?
        self.answer_timeout = answer_timeout
        self._local = threading.local()

    # -- reaching Illustrator

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
            pythoncom.CoInitialize()
            self._local.ready = True
        return pythoncom, pywintypes, client

    def running(self) -> bool:
        if not self.available():
            return False
        _, _, client = self._init_thread()
        try:
            client.GetActiveObject(PROGID)
            return True
        except Exception:
            return False

    def _app(self, start: bool):
        _, pywintypes, client = self._init_thread()
        app = getattr(self._local, "app", None)
        if app is not None:
            return app
        if not registered():
            raise NotAvailable("Illustrator is not installed on this computer, or its automation interface is missing.", code="not-installed")
        try:
            app = client.GetActiveObject(PROGID)
        except Exception:
            if not start:
                raise NotAvailable("Illustrator is not open. Open it, or let Colorroom start it.", code="not-running") from None
            try:
                app = client.Dispatch(PROGID)
            except pywintypes.com_error as error:
                raise self._com_error(error, "Colorroom could not start Illustrator.") from None
        self._local.app = app
        return app

    def _check_answers(self) -> None:
        """Ask Illustrator a trivial question in another thread; no answer in time means busy."""
        outcome: dict = {}

        def ask():
            try:
                pythoncom, _, client = _com()
                pythoncom.CoInitialize()
                try:
                    outcome["answer"] = client.GetActiveObject(PROGID).DoJavaScript("'ok'", None, _NEVER_SHOW_DEBUGGER)
                finally:
                    pythoncom.CoUninitialize()
            except Exception as error:  # reported below
                outcome["error"] = error

        worker = threading.Thread(target=ask, name="colorroom-illustrator-probe", daemon=True)
        worker.start()
        worker.join(self.answer_timeout)
        if worker.is_alive():
            raise AppBusy("Illustrator is not answering. If a dialog box is open in Illustrator, close it, then try again.", code="busy")
        error = outcome.get("error")
        if error is not None:
            _, pywintypes, _ = _com()
            if isinstance(error, pywintypes.com_error):
                raise self._com_error(error)
            raise BridgeError("Colorroom could not reach Illustrator.", detail=repr(error), code="unreachable")

    def _com_error(self, error, fallback: str = "") -> BridgeError:
        code = error.args[0] if error.args else 0
        detail = repr(error.args)
        if code in _BUSY:
            return AppBusy(
                "Illustrator is busy. Close any open dialog box in Illustrator or finish what you are doing there, then try again.",
                detail=detail,
                code="busy",
            )
        if code in _GONE:
            self._local.app = None
            return BridgeError("Illustrator closed or stopped answering. Open it again and try once more.", detail=detail, code="gone")
        text = ""
        try:
            text = str(error.args[2][2] or "")
        except (IndexError, TypeError):
            pass
        if text:
            return BridgeError(script_message({"error": text}), detail=detail, code="script")
        return BridgeError(fallback or "Illustrator reported a problem Colorroom does not recognise.", detail=detail, code="com")

    def run_script(self, script, args: Optional[dict] = None, *, start: Optional[bool] = None) -> dict:
        """Send one script from `scripts/` (or a path) and return its JSON answer."""
        code = compose_script(script, args)
        app = self._app(self.start if start is None else start)
        self._check_answers()
        _, pywintypes, _ = _com()
        for attempt in range(_BUSY_RETRIES + 1):
            try:
                text = app.DoJavaScript(code, None, _NEVER_SHOW_DEBUGGER)
                break
            except pywintypes.com_error as error:
                # A call Illustrator rejected never ran, so asking again is safe. It does this for
                # a moment after saving or closing a document.
                if error.args and error.args[0] in _BUSY and attempt < _BUSY_RETRIES:
                    time.sleep(_BUSY_WAIT)
                    continue
                raise self._com_error(error) from None
        try:
            data = json.loads(text)
        except (TypeError, ValueError):
            raise BridgeError("Illustrator gave an answer Colorroom does not understand.", detail=str(text)[:500], code="answer") from None
        if not isinstance(data, dict):
            raise BridgeError("Illustrator gave an answer Colorroom does not understand.", detail=str(text)[:500], code="answer")
        if not data.get("ok"):
            raise BridgeError(script_message(data), detail=f"{data.get('error')} (line {data.get('line')})", code="script")
        return data

    # -- what is open

    def state(self, *, start: Optional[bool] = None) -> dict:
        """`{version, active, documents: [{name, path, saved, colorMode}]}`."""
        return self.run_script("illustrator_status.jsx", {}, start=start)

    def active_document(self) -> Optional[dict]:
        if not self.running():
            return None
        state = self.state(start=False)
        for document in state.get("documents", []):
            if state.get("active") and (document.get("path") or document.get("name")) == state.get("active"):
                return document
        return None

    def _open_documents(self) -> list[dict]:
        return self.state().get("documents", [])

    # -- read

    def read_raw(self, path=None, *, preview_file=None) -> dict:
        """Illustrator's own answer for `path` (opened and closed again when it is not open)."""
        return self.run_script(
            "illustrator_read.jsx",
            {"path": _posix(path) if path else "", "open": path is not None, "preview": _posix(preview_file) if preview_file else ""},
        )

    def read(self, path=None, *, work_dir=None, preview: bool = True, leave_open: bool = False) -> Manifest:
        """Every layer and object with its exact colors, as a Manifest. The manifest and preview
        are saved in `work_dir` (a "Colorroom" folder in the system's temporary folder by default).
        A file that is not open is opened, read and closed again, unless `leave_open`."""
        if path is not None:
            source = Path(path).resolve()
            if not source.is_file():
                raise BridgeError(f"Colorroom cannot find the file {source.name}.", code="missing")
            stem = source.stem
        else:
            source = None
            front = self.active_document()
            if front is None:
                raise BridgeError("There is no document open in Illustrator.", code="no-document")
            stem = Path(front["name"]).stem
        work = Path(work_dir) if work_dir is not None else Path(tempfile.gettempdir()) / "Colorroom"
        work.mkdir(parents=True, exist_ok=True)
        stem = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", stem) or "document"
        preview_file = work / (stem + PREVIEW_SUFFIX)
        if preview and preview_file.exists():
            preview_file.unlink()
        if leave_open and source is not None and not self._is_open(source):
            self.run_script("illustrator_open.jsx", {"path": _posix(source)})
        raw = self.read_raw(source, preview_file=preview_file if preview else None)
        version = str(self.state(start=False).get("version", ""))
        has_preview = preview and preview_file.is_file()
        data = manifest_data(raw, preview_file.name if has_preview else "", version=version)
        manifest_file = work / (stem + MANIFEST_SUFFIX)
        text = json.dumps(data, indent=2, ensure_ascii=False) + "\n"
        manifest_file.write_text(text, encoding="utf-8", newline="\n")
        return parse_manifest(text, manifest_file)

    def _is_open(self, path: Path) -> bool:
        wanted = str(Path(path).resolve()).casefold()
        return any(document.get("path") and str(Path(document["path"])).casefold() == wanted for document in self._open_documents())

    # -- apply

    def apply(
        self,
        source_path,
        mapping: Mapping[str, str],
        save_as=None,
        *,
        option: str = "A",
        change_hidden: bool = False,
        change_locked: bool = False,
        change_missing_fonts: bool = False,
        change_swatches: bool = True,
        leave_open: bool = False,
        overwrite: bool = False,
        report_path=None,
    ) -> ApplyReport:
        """Change `mapping`'s colors in a copy of `source_path`, save it, read it back and check it.

        `mapping` is {old hex: new hex} with the hex codes as `read` reports them. The copy is
        `save_as`, or "name - Colorroom <option>.ai" beside the original. The original is never
        opened by Illustrator: Colorroom copies the file first and Illustrator works on the copy.
        Global swatches, spot colors and gradients change in their definition, so every object
        that uses them follows, locked and hidden ones too (the report lists them).
        """
        changes = normalize_mapping(mapping)
        if not changes:
            raise BridgeError("No color is set to change, so there is nothing to export.", code="no-changes")
        target = Path(save_as) if save_as is not None else output_path(source_path, option)
        source, target = check_targets(source_path, target, self.extensions, overwrite)
        report = ApplyReport(self.program, str(source), None, dict(changes))
        original = file_hash(source)
        for document in self._open_documents():
            path = document.get("path") or ""
            if path and str(Path(path)).casefold() == str(target).casefold():
                raise BridgeError(f"{target.name} is open in Illustrator. Close it there and try again.", code="already-open")
            if path and str(Path(path)).casefold() == str(source).casefold() and not document.get("saved", True):
                report.notes.append(
                    f"{source.name} has unsaved changes in Illustrator. Colorroom used the version saved on disk; your open document was not touched."
                )
        rules = {"change_locked": change_locked, "change_hidden": change_hidden, "change_missing_fonts": change_missing_fonts}
        shutil.copyfile(source, target)
        opened = False
        try:
            self.run_script("illustrator_open.jsx", {"path": _posix(target)})
            opened = True
            before = self.read_raw(target)
            done = self.run_script(
                "illustrator_apply.jsx",
                {
                    "path": _posix(target),
                    "mapping": changes,
                    "includeLocked": change_locked,
                    "includeHidden": change_hidden,
                    "includeMissingFonts": change_missing_fonts,
                    "swatches": change_swatches,
                },
            )
            self.run_script("illustrator_save.jsx", {"path": _posix(target), "close": True})
            opened = False
        except BaseException:
            if opened:
                try:
                    self.run_script("illustrator_close.jsx", {"path": _posix(target)})
                except BridgeError:
                    pass
            target.unlink(missing_ok=True)  # only the copy made a moment ago
            raise
        report.saved = str(target)
        after = self.read_raw(target)  # the saved file, opened again from disk
        targets = done.get("targets") or {}
        expected = expected_colors(before, changes, targets, change_swatches=change_swatches, **rules)
        wanted = predicted_changes(before, changes, targets, change_swatches=change_swatches, **rules)
        verify(manifest_from(before, target), manifest_from(after, target, runs_from=before), wanted, report)
        self._check_values(before, after, expected, report)
        check_definitions(before, after, expected, report)
        check_structure(before, after, report)
        self._explain(report, before, done, rules, wanted)
        report.checks["the original file is unchanged"] = file_hash(source) == original
        if leave_open:
            self.run_script("illustrator_open.jsx", {"path": _posix(target)})
        if report_path is not None:
            report.write(report_path)
        return report

    @staticmethod
    def _check_values(before: dict, after: dict, expected: dict, report: ApplyReport) -> None:
        """Every color slot of every object, stored values included (CMYK numbers, swatch links)."""
        after_items = {item.get("id"): item for item in after.get("items") or []}
        wrong = []
        for item in before.get("items") or []:
            other = after_items.get(item.get("id"))
            if other is None:
                continue
            slots = expected["items"].get(item.get("id"), {})
            now: dict = {slot: other.get(slot) for slot in ("fill", "stroke") if slot in other}
            for index, (fill, stroke) in enumerate(_per_character(other)):
                now[("text", index, 0)] = fill
                now[("text", index, 1)] = stroke
            for slot, wanted_color in slots.items():
                if not same_color(now.get(slot), wanted_color, 0.05):
                    where = slot if isinstance(slot, str) else f"character {slot[1] + 1} {'color' if slot[2] == 0 else 'outline'}"
                    wrong.append(f"“{item.get('name') or item.get('id')}” {where}: should be {color_text(wanted_color)}, is {color_text(now.get(slot))}")
        report.problems.extend(wrong[:50])
        if len(wrong) > 50:
            report.problems.append(f"And {len(wrong) - 50} more colors that are not what they should be.")
        report.checks["every stored color value is exactly as expected"] = not wrong

    @staticmethod
    def _explain(report: ApplyReport, before: dict, done: dict, rules: dict, wanted: list[dict]) -> None:
        """Skipped objects, objects that follow a swatch, and CMYK conversions, in plain words."""
        manifest = manifest_data(before)
        entries = {}
        for number, (what, item) in enumerate(_ordered(before), start=1):
            if what == "item":
                entries[item.get("id")] = manifest["layers"][number - 1]
        changed_ids = {change["id"] for change in wanted}
        mapped = set(report.mapping)
        for item in before.get("items") or []:
            reason = eligible(item, **rules)
            if not reason:
                continue
            entry = entries.get(item.get("id")) or {}
            own = {c.get("hex") for c in (item.get("fill"), item.get("stroke")) if c and c.get("type") in PLAIN}
            own |= {c.get("hex") for pair in _per_character(item) for c in pair if c and c.get("type") in PLAIN}
            if item.get("kind") in LEFT_ALONE or own & mapped:
                report.skipped.append(Skipped(entry.get("id", 0), entry.get("name", ""), entry.get("path", ""), entry.get("kind", "other"), reason))
            if item.get("kind") not in LEFT_ALONE and entry.get("id") in changed_ids:
                report.notes.append(
                    f"“{entry.get('path')}” is {reason.split(' (')[0]}, but it uses a swatch or gradient that changed, so it shows the new color."
                )
        for item in done.get("skipped") or []:
            if str(item.get("reason", "")).startswith("Illustrator refused"):
                report.problems.append(f"“{item.get('layer')} / {item.get('name')}”: {item.get('reason')}")
        if done.get("colorMode") == "CMYK":
            words = []
            for old, target in (done.get("targets") or {}).items():
                values = "/".join(f"{value:.1f}" for value in target.get("values") or [])
                words.append(f"{old} -> {report.mapping.get(old)} became CMYK {values} (Illustrator shows it as {target.get('hex')})")
            report.notes.append("This document is CMYK, so Illustrator converted the new colors: " + "; ".join(words) + ".")


def make_bridge(**options) -> IllustratorBridge:
    return IllustratorBridge(**options)
