"""Route A for Photoshop: core/bridges/base.py, photoshop_win.py and scripts/photoshop_*.jsx.

Made by Opus 5.5 (2026-11-14), for Fable 5.2 to review.

Engine tests need no Photoshop: script building, the manifest from a recorded answer
(`fixtures/photoshop_read_answer.json`, recorded from Photoshop 2026 reading the test document
that `fixtures/photoshop_make_test_doc.jsx` builds), labels, mappings, file names, the check,
and the whole apply flow against a fake Photoshop.

Live tests are marked ``@pytest.mark.photoshop`` and run only with ``COLORROOM_LIVE=1`` on a
computer with Photoshop. They build the test document inside Photoshop in ``test-kit/live/``
(git-ignored), work only on that folder, and never touch any other file. Files in
``test-kit/real/`` are copied into ``test-kit/live/`` first; Photoshop only ever opens the copy.
"""

from __future__ import annotations

import copy
import json
import os
import re
import shutil
import subprocess
import sys
import threading
import time
from pathlib import Path

import numpy as np
import pytest
from PIL import Image

from colorroom.core.bridges import base, photoshop_win
from colorroom.core.bridges.base import AppBusy, ApplyReport, BridgeError, compose_script, file_hash, normalize_mapping, output_path, verify
from colorroom.core.bridges.photoshop_win import PhotoshopBridge, describe_part, manifest_data, manifest_from
from colorroom.core.manifest import parse_manifest

FIXTURES = Path(__file__).parent / "fixtures"
PROJECT = Path(__file__).resolve().parents[1]
ANSWER = json.loads((FIXTURES / "photoshop_read_answer.json").read_text(encoding="utf-8"))
MAKER = FIXTURES / "photoshop_make_test_doc.jsx"

#: The colors of the test document, layer by layer (visible parts only).
EXPECTED = {
    "Caption": {("text", "#1D3557")},
    "Headline": {("text", "#111827"), ("text", "#E63946")},
    "Group A / Hidden fill": {("fill", "#E63946")},
    "Group A / Locked fill": {("fill", "#E63946")},
    "Group A / Badge": {("effect", "#2A9D8F"), ("effect", "#264653")},
    "Rectangle": {("fill", "#457B9D"), ("stroke", "#1D3557")},
    "Gradient fill": {("gradient", "#A8DADC"), ("gradient", "#6D597A")},
    "Solid fill": {("fill", "#E63946")},
    "Gradient Map": {("gradient", "#1D3557"), ("gradient", "#F4A261"), ("gradient", "#F1FAEE")},
}
KINDS = {
    "Caption": "text",
    "Headline": "text",
    "Group A": "group",
    "Group A / Hidden fill": "fill",
    "Group A / Locked fill": "fill",
    "Group A / Badge": "image",
    "Rectangle": "shape",
    "Gradient fill": "fill",
    "Solid fill": "fill",
    "Gradient Map": "adjustment",
    "Gray ramp": "image",
    "Smart photo": "smart-object",
    "Photo pixels": "image",
    "Background": "image",
}
#: At least four colors, including a gradient map stop and two text colors.
MAPPING = {
    "#111827": "#0B6E4F",  # Headline run 1
    "#E63946": "#FF9F1C",  # Headline run 2, Solid fill, Hidden fill (Locked fill is skipped)
    "#1D3557": "#3A0CA3",  # Gradient Map stop 1, Rectangle stroke, Caption (one-color text)
    "#F4A261": "#E76F51",  # Gradient Map stop 2
    "#457B9D": "#4CC9F0",  # Rectangle fill
    "#A8DADC": "#FFD6A5",  # Gradient fill stop 1
    "#2A9D8F": "#F72585",  # Badge Color Overlay
    "#264653": "#7209B7",  # Badge Stroke effect
    "#3366CC": "#00FF00",  # only in pixels: must stay as it is
}


def _layers_by_path(manifest):
    return {layer.path: layer for layer in manifest.layers}


# ---- scripts ---------------------------------------------------------------------------------


def test_scripts_are_plain_es3_and_one_job_each():
    names = sorted(path.name for path in base.SCRIPTS.glob("photoshop_*.jsx"))
    assert names == ["photoshop_apply.jsx", "photoshop_lib.jsx", "photoshop_probe.jsx", "photoshop_read.jsx"]
    for name in names + [MAKER.name]:
        path = MAKER if name == MAKER.name else base.SCRIPTS / name
        text = path.read_text(encoding="utf-8")
        assert "Made by Opus 5.5" in text
        code = "\n".join(line for line in text.splitlines() if not line.lstrip().startswith("//"))
        for modern in ("let ", "const ", "=>", "JSON.", "`"):
            assert modern not in code, f"{name} uses {modern!r}, which ExtendScript does not know"
        if name != "photoshop_lib.jsx":
            assert text.startswith('//@include "photoshop_lib.jsx"')
            assert text.rstrip().endswith("});"), "a script must end with CR.run(...) so its answer is returned"


def test_compose_script_puts_arguments_and_library_in_front_once():
    code = compose_script("photoshop_read.jsx", {"path": "C:/a b/ü.psd", "odd": "line\u2028sep"})
    first = code.splitlines()[0]
    assert first.startswith("var CR_ARGS = {") and first.endswith("};")
    assert "\\u2028" in first and "\u2028" not in first  # a raw line separator would end the line in JavaScript
    assert json.loads(first[len("var CR_ARGS = ") : -1]) == {"path": "C:/a b/ü.psd", "odd": "line\u2028sep"}
    assert not re.search(r"^\s*//\s*@include", code, re.MULTILINE)
    assert code.count("var CR = {};") == 1
    assert code.index("var CR = {};") < code.rindex("CR.run(function")


def test_compose_script_finds_the_library_for_a_script_elsewhere(tmp_path):
    script = tmp_path / "extra.jsx"
    script.write_text('//@include "photoshop_lib.jsx"\nCR.run(function () { return {}; });\n', encoding="utf-8")
    assert "var CR = {};" in compose_script(script)
    missing = tmp_path / "broken.jsx"
    missing.write_text('//@include "nowhere.jsx"\n', encoding="utf-8")
    with pytest.raises(BridgeError):
        compose_script(missing)


# ---- mappings, names and targets -------------------------------------------------------------


def test_normalize_mapping():
    assert normalize_mapping({"#abc": "#ffffff", "#123456": "#123456", "00ff00": "#0000FF"}) == {"#AABBCC": "#FFFFFF", "#00FF00": "#0000FF"}
    assert normalize_mapping({"#AABBCC": "#000000", "#abc": "#000"}) == {"#AABBCC": "#000000"}
    with pytest.raises(BridgeError):
        normalize_mapping({"#AABBCC": "#000000", "#abc": "#111111"})
    with pytest.raises(BridgeError):
        normalize_mapping({"blue": "#000000"})


def test_output_path_never_reuses_a_name(tmp_path):
    source = tmp_path / "Poster.psd"
    source.write_bytes(b"8BPS")
    first = output_path(source, "A")
    assert first.name == "Poster - Colorroom A.psd"
    first.write_bytes(b"x")
    assert output_path(source, "A").name == "Poster - Colorroom A (2).psd"
    assert output_path(source, 'B/:*"').name == "Poster - Colorroom B.psd"


def test_check_targets_protects_the_original(tmp_path):
    source = tmp_path / "Poster.psd"
    source.write_bytes(b"8BPS")
    extensions = (".psd", ".psb")
    with pytest.raises(BridgeError) as same:
        base.check_targets(source, tmp_path / "." / "Poster.psd", extensions)
    assert same.value.code == "same-file"
    with pytest.raises(BridgeError) as kind:
        base.check_targets(source, tmp_path / "Poster - Colorroom A.psb", extensions)
    assert kind.value.code == "type"
    existing = tmp_path / "Poster - Colorroom A.psd"
    existing.write_bytes(b"x")
    with pytest.raises(BridgeError) as exists:
        base.check_targets(source, existing, extensions)
    assert exists.value.code == "exists"
    assert base.check_targets(source, existing, extensions, overwrite=True)[1] == existing.resolve()
    with pytest.raises(BridgeError) as missing:
        base.check_targets(tmp_path / "gone.psd", existing, extensions)
    assert missing.value.code == "missing"


# ---- the manifest from Photoshop's answer ---------------------------------------------------


def test_manifest_from_recorded_answer():
    manifest = manifest_from(ANSWER, "C:/Colorroom/ps-bridge-test.psd")
    assert (manifest.width, manifest.height, manifest.resolution, manifest.bit_depth, manifest.color_mode) == (800, 600, 144.0, 8, "RGB")
    assert manifest.layer_comps == 1
    layers = _layers_by_path(manifest)
    assert {path: layer.kind for path, layer in layers.items()} == KINDS
    for path, colors in EXPECTED.items():
        assert set(layers[path].colors) == colors, path
    assert layers["Group A / Hidden fill"].visible is False
    assert layers["Group A / Locked fill"].lock == "all"
    assert layers["Gradient Map"].detail == "gradient map"
    labels = [part.label for part in layers["Gradient Map"].parts]
    assert labels == ["gradient map stop 1 (0%)", "gradient map stop 2 (50%)", "gradient map stop 3 (100%)"]
    assert [part.label for part in layers["Headline"].parts] == ["text color, characters 1 to 10", "text color, characters 11 to 14"]
    # Photoshop 2026 keeps switched-off placeholder effects; they are listed but never counted.
    badge = layers["Group A / Badge"]
    assert any(part.off for part in badge.parts)
    assert all(not part.off for part in badge.parts if part.hex in ("#2A9D8F", "#264653"))
    hexes = {color.hex for color in manifest.colors(include_hidden=True)}
    assert "#000000" not in hexes  # only off shadows are black
    assert {"#E63946", "#1D3557", "#2A9D8F"} <= hexes
    assert next(c for c in manifest.colors() if c.hex == "#E63946").layers == ["Headline", "Group A / Locked fill", "Solid fill"]
    assert any("layer comps" in note for note in manifest.notes)
    assert any("picture layers" in note for note in manifest.notes)


def test_manifest_file_round_trip_and_preview(tmp_path):
    data = manifest_data(ANSWER, "doc.colorroom-preview.png")
    Image.new("RGB", (8, 6)).save(tmp_path / "doc.colorroom-preview.png")
    path = tmp_path / "doc.colorroom-manifest.json"
    path.write_text(json.dumps(data), encoding="utf-8")
    manifest = parse_manifest(path.read_text(encoding="utf-8"), path)
    assert manifest.preview == tmp_path / "doc.colorroom-preview.png"
    assert manifest.stem == "doc"
    assert manifest.source == Path("C:/Colorroom/test-kit/ps-bridge-test.psd")


def test_old_plugin_manifests_still_read_the_same():
    old = {
        "format": "colorroom-manifest",
        "manifest_version": 1,
        "width": 100,
        "height": 100,
        "layers": [
            {"id": 1, "name": "Logo", "kind": "shape", "colors": [{"where": "fill", "hex": "#123456"}], "bounds": [0, 0, 50, 50]},
            {"id": 2, "name": "Photo", "kind": "image", "colors": [], "bounds": [0, 0, 100, 100]},
            {"id": 3, "name": "Odd", "kind": "mystery", "colors": [{"where": "glow", "hex": "#FFFFFF"}]},
        ],
    }
    manifest = parse_manifest(json.dumps(old), "x.colorroom-manifest.json")
    assert [c.hex for c in manifest.colors()] == ["#123456"]
    assert manifest.layers[2].kind == "other" and manifest.layers[2].colors == []
    assert manifest.resolution == 0.0 and manifest.layers[0].parts == []


@pytest.mark.parametrize(
    "layer, key, extra, expected",
    [
        ({"ps_kind": 11}, "adj/0/solidColorLayer/color", {}, ("fill", "fill color")),
        ({"ps_kind": 4}, "adj/0/solidColorLayer/color", {}, ("fill", "shape fill")),
        ({"ps_kind": 4}, "stroke/strokeStyleContent/color", {"off": True}, ("stroke", "shape stroke (off)")),
        ({"ps_kind": 9}, "adj/0/gradientLayer/gradient/colors/1/color", {"stop": {"location": 2048}}, ("gradient", "gradient fill stop 2 (50%)")),
        ({"ps_kind": 2}, "adj/0/gradientMapClass/gradient/colors/0/color", {"stop": {"location": 0}}, ("gradient", "gradient map stop 1 (0%)")),
        ({"ps_kind": 2, "detail": "photo filter"}, "adj/0/photoFilter/color", {}, ("adjustment", "photo filter color")),
        ({"ps_kind": 1}, "fx/solidFill/color", {}, ("effect", "Color Overlay")),
        ({"ps_kind": 1}, "fx/frameFXMulti/1/color", {}, ("effect", "Stroke effect")),
        ({"ps_kind": 1}, "fx/dropShadowMulti/0/color", {"off": True}, ("effect", "Drop Shadow (off)")),
        ({"ps_kind": 1}, "fx/bevelEmboss/highlightColor", {}, ("effect", "Bevel & Emboss highlight")),
        ({"ps_kind": 1}, "fx/gradientFill/gradient/colors/0/color", {"stop": {"location": 4096}}, ("effect", "Gradient Overlay gradient stop 1 (100%)")),
        ({"ps_kind": 3}, "text/textStyleRange/2/textStyle/color", {"range": [4, 9]}, ("text", "text color, characters 5 to 9")),
    ],
)
def test_describe_part(layer, key, extra, expected):
    assert describe_part(layer, {"key": key, **extra}) == expected


# ---- the check ------------------------------------------------------------------------------


def _after(answer, mapping, skip_ids=()):
    """What Photoshop would read back if it changed every visible-or-hidden part in `mapping`."""
    after = copy.deepcopy(answer)
    changes = []
    for layer in after["layers"]:
        if layer["id"] in skip_ids:
            continue
        for part in layer["parts"]:
            if part["hex"] in mapping:
                changes.append({"id": layer["id"], "key": part["key"], "from": part["hex"], "to": mapping[part["hex"]]})
                part["hex"] = mapping[part["hex"]]
    return after, changes


def test_verify_passes_when_every_color_landed():
    mapping = normalize_mapping(MAPPING)
    after, changes = _after(ANSWER, mapping, skip_ids={13})
    report = verify(manifest_from(ANSWER, "a.psd"), manifest_from(after, "a.psd"), changes, ApplyReport("Photoshop", "a.psd", "b.psd", mapping))
    assert report.ok, report.summary()
    changed = {layer.path for layer in report.changed}
    assert "Group A / Locked fill" not in changed and "Gradient Map" in changed and "Caption" in changed
    assert sum(len(layer.parts) for layer in report.changed) == 12  # off placeholder effects never match this mapping
    assert "every color is exact" in report.summary()
    data = report.to_dict()
    assert data["ok"] is True and data["changed"][0]["verified"] is True


@pytest.mark.parametrize("damage", ["wrong-hex", "missing-part", "extra-change", "signature", "lost-layer", "ppi", "size", "bits"])
def test_verify_catches_every_kind_of_damage(damage):
    mapping = normalize_mapping(MAPPING)
    after, changes = _after(ANSWER, mapping, skip_ids={13})
    layers = {layer["name"]: layer for layer in after["layers"]}
    if damage == "wrong-hex":
        layers["Gradient Map"]["parts"][1]["hex"] = "#E76F50"
    elif damage == "missing-part":
        layers["Headline"]["parts"] = layers["Headline"]["parts"][:1]
    elif damage == "extra-change":
        layers["Locked fill"]["parts"][0]["hex"] = "#FF9F1C"
    elif damage == "signature":
        layers["Headline"]["signature"] = layers["Headline"]["signature"].replace("ArialMT", "MyriadPro-Regular") + " "
    elif damage == "lost-layer":
        after["layers"] = [layer for layer in after["layers"] if layer["name"] != "Smart photo"]
    elif damage == "ppi":
        after["resolution"] = 72
    elif damage == "size":
        after["width"] = 400
    elif damage == "bits":
        after["bit_depth"] = 16
    report = verify(manifest_from(ANSWER, "a.psd"), manifest_from(after, "a.psd"), changes, ApplyReport("Photoshop", "a.psd", "b.psd", mapping))
    assert not report.ok
    assert "every color is exact" not in report.summary()


def test_verify_ignores_changes_to_colors_nobody_sees():
    mapping = normalize_mapping(MAPPING)
    after, changes = _after(ANSWER, mapping, skip_ids={13})
    changes.append({"id": 16, "key": "text/paragraphStyleRange/0/paragraphStyle/defaultStyle/color", "from": "#000000", "to": "#FFFFFF"})
    report = verify(manifest_from(ANSWER, "a.psd"), manifest_from(after, "a.psd"), changes, ApplyReport("Photoshop", "a.psd", "b.psd", mapping))
    assert report.ok


def test_report_summary_lists_failures_in_plain_words():
    report = ApplyReport("Photoshop", "a.psd", "C:/x/a - Colorroom A.psd", {"#000000": "#FFFFFF"})
    report.changed.append(base.LayerResult(1, "Title", "Title", "text", "text", True, [base.PartResult("k", "text", "text color", "#000000", "#FFFFFF", "#FEFEFE")]))
    report.skipped.append(base.Skipped(2, "Logo", "Logo", "fill", "locked in Photoshop"))
    report.checks["same resolution (ppi)"] = False
    text = report.summary()
    assert "1 layer changed in a - Colorroom A.psd." in text
    assert "“Title”: the text color should be #FFFFFF but is #FEFEFE." in text
    assert "Check failed: same resolution (ppi)." in text
    assert "Left alone: 1 × locked in Photoshop." in text
    assert not report.ok


# ---- the apply flow against a fake Photoshop -------------------------------------------------


class FakePhotoshop:
    """Answers like the scripts do, from the recorded answer."""

    def __init__(self, land=True, fail_apply=False, open_documents=()):
        self.land = land
        self.fail_apply = fail_apply
        self.open_documents = list(open_documents)
        self.calls = []

    def __call__(self, script, args=None, *, start=None):
        name = Path(script).name
        self.calls.append((name, dict(args or {})))
        if name == "photoshop_probe.jsx":
            return {"ok": True, "version": "27.4.0", "documents": self.open_documents}
        mapping = normalize_mapping(MAPPING)
        after, changes = _after(ANSWER, mapping, skip_ids={13})
        if name == "photoshop_apply.jsx":
            assert Path(args["path"]).is_file(), "the copy must exist before Photoshop opens it"
            if self.fail_apply:
                raise BridgeError("Photoshop reported a problem: test", code="script")
            return {"ok": True, "before": ANSWER, "changes": changes, "skipped": [{"id": 13, "reason": "locked", "detail": "all"}], "errors": [], "saved": True}
        if name == "photoshop_read.jsx":
            if not self.land:
                after["layers"][0]["parts"][0]["hex"] = "#1D3557"
            return {"ok": True, **after}
        raise AssertionError(name)


def _fake_bridge(monkeypatch, fake):
    bridge = PhotoshopBridge()
    monkeypatch.setattr(bridge, "run_script", fake)
    return bridge


def test_apply_works_on_a_copy_and_reports(monkeypatch, tmp_path):
    source = tmp_path / "Poster.psd"
    source.write_bytes(b"8BPS original bytes")
    before = file_hash(source)
    fake = FakePhotoshop()
    report = _fake_bridge(monkeypatch, fake).apply(source, MAPPING, report_path=tmp_path / "report.json")
    assert report.ok, report.summary()
    assert Path(report.saved).name == "Poster - Colorroom A.psd"
    assert file_hash(source) == before
    applied = [args for name, args in fake.calls if name == "photoshop_apply.jsx"][0]
    assert Path(applied["path"]).name == "Poster - Colorroom A.psd" and applied["hidden"] is True and applied["locked"] is False
    assert all(Path(args["path"]) != source for name, args in fake.calls if "path" in args)
    reasons = {item.path: item.reason for item in report.skipped}
    assert reasons["Group A / Locked fill"] == "locked in Photoshop"
    assert reasons["Smart photo"].startswith("smart object")
    assert reasons["Background"].startswith("background")
    assert json.loads((tmp_path / "report.json").read_text(encoding="utf-8"))["ok"] is True


def test_apply_reports_a_color_that_did_not_land(monkeypatch, tmp_path):
    source = tmp_path / "Poster.psd"
    source.write_bytes(b"8BPS")
    report = _fake_bridge(monkeypatch, FakePhotoshop(land=False)).apply(source, MAPPING)
    assert not report.ok
    assert "should be #3A0CA3 but is #1D3557" in report.summary()


def test_apply_removes_its_copy_when_photoshop_fails(monkeypatch, tmp_path):
    source = tmp_path / "Poster.psd"
    source.write_bytes(b"8BPS")
    with pytest.raises(BridgeError):
        _fake_bridge(monkeypatch, FakePhotoshop(fail_apply=True)).apply(source, MAPPING)
    assert sorted(path.name for path in tmp_path.iterdir()) == ["Poster.psd"]


def test_apply_refuses_before_touching_photoshop(monkeypatch, tmp_path):
    source = tmp_path / "Poster.psd"
    source.write_bytes(b"8BPS")
    fake = FakePhotoshop()
    bridge = _fake_bridge(monkeypatch, fake)
    with pytest.raises(BridgeError):
        bridge.apply(source, MAPPING, save_as=source)
    with pytest.raises(BridgeError):
        bridge.apply(source, {"#000000": "#000000"})
    with pytest.raises(BridgeError):
        bridge.apply(tmp_path / "Poster.png", MAPPING)
    assert fake.calls == []


def test_apply_refuses_when_the_export_is_open_and_notes_unsaved_originals(monkeypatch, tmp_path):
    source = tmp_path / "Poster.psd"
    source.write_bytes(b"8BPS")
    target = tmp_path / "Poster - Colorroom A.psd"
    with pytest.raises(BridgeError) as error:
        _fake_bridge(monkeypatch, FakePhotoshop(open_documents=[{"name": target.name, "path": str(target), "saved": True}])).apply(source, MAPPING)
    assert error.value.code == "already-open" and not target.exists()
    report = _fake_bridge(monkeypatch, FakePhotoshop(open_documents=[{"name": source.name, "path": str(source.resolve()), "saved": False}])).apply(source, MAPPING)
    assert any("unsaved changes" in note for note in report.notes)


# ---- errors and availability -----------------------------------------------------------------


class _ComError(Exception):
    pass


def test_com_errors_become_plain_messages():
    bridge = PhotoshopBridge()
    busy = bridge._com_error(_ComError(-2147418111, "Call was rejected by callee.", None, None))
    assert isinstance(busy, AppBusy) and "dialog box" in busy.message
    gone = bridge._com_error(_ComError(-2147023174, "The RPC server is unavailable.", None, None))
    assert gone.code == "gone" and "closed" in gone.message
    script = bridge._com_error(_ComError(-2147352567, "Exception occurred.", (0, "Adobe Photoshop", "The command is not currently available.", None, 0, 0), None))
    assert "busy" in script.message.lower()
    assert photoshop_win.script_message({"code": "mode", "message": "This document is in CMYK mode."}) == "This document is in CMYK mode."
    assert photoshop_win.script_message({"code": "script", "message": "boom"}) == "Photoshop reported a problem: boom"


def test_available_is_false_without_pywin32(monkeypatch):
    def missing():
        raise base.NotAvailable("no pywin32", code="no-pywin32")

    monkeypatch.setattr(photoshop_win, "_com", missing)
    bridge = PhotoshopBridge()
    assert bridge.available() is False and bridge.running() is False and bridge.active_document() is None


def test_importing_the_bridges_loads_neither_pywin32_nor_qt():
    code = (
        "import sys; import colorroom.core.bridges as b; import colorroom.core.bridges.photoshop_win; b.photoshop();"
        "bad = [m for m in sys.modules if m.split('.')[0] in ('win32com', 'pythoncom', 'pywintypes', 'PySide6')]; print(bad)"
    )
    out = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, cwd=PROJECT, env={**os.environ, "PYTHONPATH": str(PROJECT / "src")})
    assert out.returncode == 0, out.stderr
    assert out.stdout.strip() == "[]"


# ---- live: Photoshop on this computer (COLORROOM_LIVE=1) ------------------------------------

LIVE = PROJECT / "test-kit" / "live"
REAL = PROJECT / "test-kit" / "real"


def _live_bridge() -> PhotoshopBridge:
    if os.environ.get("COLORROOM_LIVE") != "1":
        pytest.skip("Live Photoshop tests run only with COLORROOM_LIVE=1.")
    bridge = PhotoshopBridge()
    if not bridge.available():
        pytest.skip("Photoshop or pywin32 is not available on this computer.")
    return bridge


@pytest.fixture(scope="module")
def live():
    """(bridge, the test document Photoshop built, a work folder), all inside test-kit/live."""
    bridge = _live_bridge()
    LIVE.mkdir(parents=True, exist_ok=True)
    for old in LIVE.glob("ps-bridge-test*"):
        old.unlink()
    work = LIVE / "work"
    work.mkdir(exist_ok=True)
    document = LIVE / "ps-bridge-test.psd"
    bridge.run_script(MAKER, {"path": str(document).replace("\\", "/")})
    assert document.is_file()
    return bridge, document, work


def _pixel(path, x, y):
    return "#%02X%02X%02X" % tuple(np.asarray(Image.open(path).convert("RGB"))[y, x])


@pytest.mark.photoshop
def test_live_read(live):
    bridge, document, work = live
    manifest = bridge.read(document, work_dir=work)
    assert (manifest.width, manifest.height, manifest.resolution, manifest.bit_depth, manifest.color_mode) == (800, 600, 144.0, 8, "RGB")
    layers = _layers_by_path(manifest)
    assert {path: layer.kind for path, layer in layers.items()} == KINDS
    for path, colors in EXPECTED.items():
        assert set(layers[path].colors) == colors, path
    assert manifest.preview is not None and Image.open(manifest.preview).size == (800, 600)
    assert _pixel(manifest.preview, 440, 480) == "#1D3557"  # black under the gradient map shows stop 1
    assert _pixel(manifest.preview, 100, 480) == "#3366CC"
    assert bridge.state()["documents"] == [] or all(Path(d["path"]) != document for d in bridge.state()["documents"])


@pytest.mark.photoshop
def test_live_apply_round_trip(live):
    bridge, document, work = live
    original = file_hash(document)
    report = bridge.apply(document, MAPPING, report_path=work / "round-trip-report.json")
    assert report.ok, report.summary()
    assert file_hash(document) == original
    saved = Path(report.saved)
    assert saved.name == "ps-bridge-test - Colorroom A.psd"
    parts = {(layer.path, part.label): (part.before, part.now) for layer in report.changed for part in layer.parts}
    assert parts == {
        ("Caption", "text color, characters 1 to 9"): ("#1D3557", "#3A0CA3"),
        ("Headline", "text color, characters 1 to 10"): ("#111827", "#0B6E4F"),
        ("Headline", "text color, characters 11 to 14"): ("#E63946", "#FF9F1C"),
        ("Group A / Hidden fill", "fill color"): ("#E63946", "#FF9F1C"),
        ("Group A / Badge", "Color Overlay"): ("#2A9D8F", "#F72585"),
        ("Group A / Badge", "Stroke effect"): ("#264653", "#7209B7"),
        ("Rectangle", "shape fill"): ("#457B9D", "#4CC9F0"),
        ("Rectangle", "shape stroke"): ("#1D3557", "#3A0CA3"),
        ("Gradient fill", "gradient fill stop 1 (0%)"): ("#A8DADC", "#FFD6A5"),
        ("Solid fill", "fill color"): ("#E63946", "#FF9F1C"),
        ("Gradient Map", "gradient map stop 1 (0%)"): ("#1D3557", "#3A0CA3"),
        ("Gradient Map", "gradient map stop 2 (50%)"): ("#F4A261", "#E76F51"),
    }
    reasons = {item.path: item.reason for item in report.skipped}
    assert reasons["Group A / Locked fill"] == "locked in Photoshop"
    assert set(reasons) >= {"Smart photo", "Photo pixels", "Gray ramp", "Background"}
    assert any("layer comps" in note for note in report.notes)
    after = bridge.read(saved, work_dir=work)
    layers = _layers_by_path(after)
    assert set(layers["Group A / Locked fill"].colors) == {("fill", "#E63946")}
    assert layers["Group A / Locked fill"].lock == "all"
    assert _pixel(after.preview, 440, 480) == "#3A0CA3"  # the gradient map's new first stop
    assert _pixel(after.preview, 100, 480) == "#3366CC"  # pixels are never touched
    assert (after.width, after.height, after.resolution, after.bit_depth) == (800, 600, 144.0, 8)


@pytest.mark.photoshop
def test_live_locked_and_hidden_options(live):
    bridge, document, work = live
    save_as = LIVE / "ps-bridge-test - Colorroom B.psd"
    report = bridge.apply(document, {"#E63946": "#06D6A0"}, save_as, change_locked=True, change_hidden=False)
    assert report.ok, report.summary()
    changed = {layer.path for layer in report.changed}
    assert changed == {"Headline", "Solid fill", "Group A / Locked fill"}
    assert {item.path: item.reason for item in report.skipped}["Group A / Hidden fill"].startswith("hidden layer")
    layers = _layers_by_path(bridge.read(save_as, work_dir=work, preview=False))
    assert layers["Group A / Locked fill"].lock == "all"  # locked again after the change
    assert set(layers["Group A / Locked fill"].colors) == {("fill", "#06D6A0")}
    assert set(layers["Group A / Hidden fill"].colors) == {("fill", "#E63946")}


@pytest.mark.photoshop
def test_live_refuses_non_rgb_documents(live, tmp_path):
    bridge, document, work = live
    cmyk = LIVE / "ps-bridge-test-cmyk.psd"
    script = tmp_path / "to_cmyk.jsx"
    script.write_text(
        '//@include "photoshop_lib.jsx"\n'
        "CR.run(function (args) { var doc = CR.open(args.source); try { doc.changeMode(ChangeMode.CMYK);"
        " doc.saveAs(new File(args.target), new PhotoshopSaveOptions(), false); } finally { doc.close(SaveOptions.DONOTSAVECHANGES); } return {}; });\n",
        encoding="utf-8",
    )
    shutil.copyfile(document, LIVE / "ps-bridge-test-to-cmyk.psd")
    bridge.run_script(script, {"source": str(LIVE / "ps-bridge-test-to-cmyk.psd").replace("\\", "/"), "target": str(cmyk).replace("\\", "/")})
    manifest = bridge.read(cmyk, work_dir=work, preview=False)
    assert manifest.color_mode == "CMYK" and any("CMYK" in note for note in manifest.notes)
    with pytest.raises(BridgeError) as error:
        bridge.apply(cmyk, MAPPING)
    assert error.value.code == "mode" and "CMYK" in error.value.message
    assert not (LIVE / "ps-bridge-test-cmyk - Colorroom A.psd").exists()


@pytest.mark.photoshop
def test_live_leaves_the_persons_open_document_alone(live, tmp_path):
    bridge, document, work = live
    mine = LIVE / "ps-bridge-test-open.psd"
    shutil.copyfile(document, mine)
    script = tmp_path / "open_and_edit.jsx"
    script.write_text(
        '//@include "photoshop_lib.jsx"\n'
        "CR.run(function (args, state) { var doc = CR.open(args.path); doc.artLayers.add().name = 'unsaved work'; state.active = doc; return {}; });\n",
        encoding="utf-8",
    )
    close = tmp_path / "close.jsx"
    close.write_text(
        '//@include "photoshop_lib.jsx"\n'
        "CR.run(function (args) { var doc = CR.findOpen(args.path); if (doc) { doc.close(SaveOptions.DONOTSAVECHANGES); } return {}; });\n",
        encoding="utf-8",
    )
    bridge.run_script(script, {"path": str(mine).replace("\\", "/")})
    try:
        report = bridge.apply(mine, {"#457B9D": "#118AB2"}, LIVE / "ps-bridge-test-open - Colorroom A.psd")
        assert report.ok, report.summary()
        assert any("unsaved changes" in note for note in report.notes)
        documents = bridge.state()["documents"]
        still = [d for d in documents if d["name"] == mine.name]
        assert len(still) == 1 and still[0]["saved"] is False and still[0]["active"] is True
        assert not any("Colorroom A" in d["name"] for d in documents)  # what Colorroom opened, it closed
    finally:
        bridge.run_script(close, {"path": str(mine).replace("\\", "/")})


@pytest.mark.photoshop
def test_live_busy_photoshop_is_reported(live, tmp_path):
    bridge, document, work = live
    script = tmp_path / "sleep.jsx"
    script.write_text('//@include "photoshop_lib.jsx"\nCR.run(function () { $.sleep(8000); return {}; });\n', encoding="utf-8")
    worker = threading.Thread(target=lambda: PhotoshopBridge().run_script(script), daemon=True)
    worker.start()
    time.sleep(1.5)
    impatient = PhotoshopBridge(answer_timeout=2.0)
    started = time.monotonic()
    with pytest.raises(AppBusy) as error:
        impatient.state()
    assert time.monotonic() - started < 7
    assert "Photoshop" in error.value.message
    worker.join(30)
    assert bridge.state()["ok"] is True  # answers again once it is free


def _real_files():
    if not REAL.is_dir():
        return []
    return sorted(path for path in REAL.glob("*.psd") if " - Colorroom" not in path.stem)


@pytest.mark.photoshop
@pytest.mark.parametrize("real", _real_files(), ids=lambda path: path.stem)
def test_live_real_file_copy(real):
    """The person's own files, through a copy: every visible gradient map stop and text color changes."""
    bridge = _live_bridge()
    LIVE.mkdir(parents=True, exist_ok=True)
    work = LIVE / "work"
    work.mkdir(exist_ok=True)
    original = file_hash(real)
    copy_path = LIVE / f"{real.stem}-copy.psd"
    for old in LIVE.glob(f"{real.stem}-copy*.psd"):
        old.unlink()
    shutil.copyfile(real, copy_path)
    manifest = bridge.read(copy_path, work_dir=work, preview=False)
    wanted = {}
    for layer in manifest.layers:
        for part in layer.parts:
            if layer.visible and not part.off and (part.where == "text" or layer.detail == "gradient map"):
                rotated = "#" + part.hex[3:] + part.hex[1:3]  # RRGGBB -> GGBBRR
                if rotated != part.hex:
                    wanted[part.hex] = rotated
    if not wanted:
        pytest.skip(f"{real.name} has no visible gradient map or text color to change.")
    report = bridge.apply(copy_path, wanted, report_path=work / f"{real.stem}-report.json")
    assert report.ok, report.summary()
    assert file_hash(real) == original
    assert report.checks["same resolution (ppi)"] and report.checks["same size in pixels"]


def test_a_script_planted_in_the_current_folder_is_never_used(tmp_path, monkeypatch):
    """A bare script name always means Colorroom's own scripts folder (security review of
    2026-11-14; written by Opus 5.5)."""
    (tmp_path / "photoshop_read.jsx").write_text("PLANTED();", encoding="utf-8")
    monkeypatch.chdir(tmp_path)
    assert "PLANTED" not in compose_script("photoshop_read.jsx")
