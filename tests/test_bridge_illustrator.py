"""The Illustrator bridge (route A for .ai files).

Made by Opus 5.5 (2026-11-14), on Fable 5.2's start, for review by the other model.

Engine tests need no Illustrator: they use a snapshot recorded from a test document that
Illustrator 2026 built (tests/fixtures/illustrator_snapshot_rgb.json, made by
tests/fixtures/illustrator_make_test_doc.jsx). Live tests are marked `illustrator` and run
only with COLORROOM_LIVE=1 on a computer with Illustrator; they build their own documents
inside test-kit/ (git-ignored) and never touch any other file.
"""

from __future__ import annotations

import copy
import hashlib
import json
import os
import re
import shutil
from pathlib import Path

import pytest

from colorroom.core.bridges import base
from colorroom.core.bridges import illustrator_win as ai
from colorroom.core.bridges.base import BridgeError

PROJECT = Path(__file__).resolve().parents[1]
FIXTURES = Path(__file__).parent / "fixtures"
SCRIPTS = PROJECT / "src" / "colorroom" / "core" / "bridges" / "scripts"
KIT = PROJECT / "test-kit"
MAKE_DOC = FIXTURES / "illustrator_make_test_doc.jsx"
MAPPING = {
    "#1D4ED8": "#E63946",  # plain fill and stroke, also on the locked, hidden and frozen boxes
    "#16A34A": "#7C3AED",  # the global swatch "Brand Green" (two objects and a 50% tint)
    "#0EA5E9": "#FACC15",  # the first stop of the gradient "Test Sky"
    "#E11D48": "#0D9488",  # text "World", the compound ring, the sublayer's detail
    "#111827": "#334155",  # text "Hello ", two strokes
    "#F97316": "#22D3EE",  # the spot color "Spot Orange"
}
RULES = {"change_locked": False, "change_hidden": False, "change_missing_fonts": False}


@pytest.fixture
def snapshot() -> dict:
    return json.loads((FIXTURES / "illustrator_snapshot_rgb.json").read_text(encoding="utf-8"))


@pytest.fixture
def work(request) -> Path:
    """A fresh folder inside the project's git-ignored test-kit (never outside the workspace)."""
    folder = KIT / "pytest-illustrator" / re.sub(r"[^\w.-]", "_", request.node.name)
    if folder.exists():
        shutil.rmtree(folder)
    folder.mkdir(parents=True)
    return folder


def _item(raw: dict, name: str) -> dict:
    return next(item for item in raw["items"] if item.get("name") == name)


def _entry(manifest, name: str):
    return next(layer for layer in manifest.layers if layer.name == name)


# ---- the scripts ---------------------------------------------------------------------------


def test_every_script_includes_the_library_and_composes():
    scripts = sorted(SCRIPTS.glob("illustrator_*.jsx"))
    names = {path.name for path in scripts}
    assert {"illustrator_lib.jsx", "illustrator_read.jsx", "illustrator_apply.jsx", "illustrator_open.jsx",
            "illustrator_save.jsx", "illustrator_close.jsx", "illustrator_status.jsx"} <= names
    for path in scripts:
        if path.name == "illustrator_lib.jsx":
            continue
        assert '//@include "illustrator_lib.jsx"' in path.read_text(encoding="utf-8"), path.name
        text = base.compose_script(path.name, {"path": "C:/x/y.ai", "mapping": {"#000000": "#FFFFFF"}})
        assert text.startswith('var CR_ARGS = {"path": "C:/x/y.ai"')
        assert not re.search(r"^\s*//\s*@include", text, re.MULTILINE)
        assert "CR.run(" in text and "CR.snapshot = function" in text


def test_the_test_document_script_composes_from_tests():
    text = base.compose_script(MAKE_DOC, {"path": "a.ai", "mode": "RGB"})
    assert "CR.snapshot = function" in text and "buildTestDocument" in text


@pytest.mark.parametrize("path", sorted(SCRIPTS.glob("illustrator_*.jsx")) + [MAKE_DOC], ids=lambda p: p.name)
def test_scripts_stay_inside_extendscript(path):
    """ExtendScript is ES3: no let/const, arrows, template strings, JSON or Array helpers."""
    code = re.sub(r"//[^\n]*", "", path.read_text(encoding="utf-8"))
    code = re.sub(r'"(?:\\.|[^"\\])*"', '""', code)
    for pattern in (r"\blet\s", r"\bconst\s", r"=>", r"`", r"\bJSON\.", r"\.indexOf\(", r"\.forEach\(", r"\.map\(", r"\.filter\("):
        assert not re.search(pattern, code), f"{path.name}: {pattern}"


def test_apply_and_save_never_touch_a_document_they_were_not_given():
    for name in ("illustrator_apply.jsx", "illustrator_save.jsx"):
        text = (SCRIPTS / name).read_text(encoding="utf-8")
        assert "if (!args.path || !doc)" in text  # refuses the active document fallback
    lib = (SCRIPTS / "illustrator_lib.jsx").read_text(encoding="utf-8")
    assert "UserInteractionLevel.DONTDISPLAYALERTS" in lib


# ---- reading: the manifest ------------------------------------------------------------------


def test_manifest_lists_layers_and_objects_in_document_order(snapshot):
    manifest = ai.manifest_from(snapshot, KIT / "x.colorroom-manifest.json")
    assert manifest.program == "Illustrator" and manifest.color_mode == "RGB"
    assert (manifest.width, manifest.height) == (800, 600)
    layers = [(entry.path, entry.detail) for entry in manifest.layers if entry.kind == "group" and "layer" in entry.detail]
    assert layers == [("Artwork", "layer"), ("Artwork / Frozen", "sublayer"), ("Artwork / Details", "sublayer"), ("Background", "layer")]
    assert [entry.id for entry in manifest.layers] == list(range(1, len(manifest.layers) + 1))
    assert _entry(manifest, "Badge dot").path == "Artwork / Badge / Badge dot"
    assert _entry(manifest, "Star").path == "Artwork / Clipped star / Star"
    assert _entry(manifest, "Photo").kind == "image" and _entry(manifest, "Photo").parts == []
    assert _entry(manifest, "Ring").detail == "compound path"
    assert _entry(manifest, "Mask").detail == "path (clipping path)"
    assert _entry(manifest, "Locked box").locked and not _entry(manifest, "Hidden box").visible
    assert _entry(manifest, "Frozen box").locked  # locked through its sublayer


def test_manifest_parts_cover_fills_strokes_text_gradients_and_swatches(snapshot):
    manifest = ai.manifest_from(snapshot, KIT / "x.colorroom-manifest.json")
    card = {part.key: part.hex for part in _entry(manifest, "Card").parts}
    assert card == {"fill": "#1D4ED8", "stroke": "#111827"}
    headline = _entry(manifest, "Headline")
    assert {part.key: part.hex for part in headline.parts} == {"text/0-6/fill": "#111827", "text/6-11/fill": "#E11D48"}
    assert headline.parts[0].label == "text color, characters 1 to 6"
    sky = _entry(manifest, "Sky").parts
    assert [(part.where, part.hex) for part in sky] == [("gradient", "#0EA5E9"), ("gradient", "#8B5CF6")]
    assert "stop 1 (0%)" in sky[0].label
    tint = _entry(manifest, "Global tint").parts[0]
    assert tint.hex == "#16A34A" and "global swatch Brand Green, 50% tint" in tint.label
    assert "spot color Spot Orange" in _entry(manifest, "Spot rect").parts[0].label
    colors = {color.hex: color for color in manifest.colors(include_hidden=True)}
    assert len(colors["#1D4ED8"].layers) == 6  # card, badge dot, detail stroke, locked, hidden, frozen
    assert "#F59E0B" in colors and "#F5F0E6" in colors


def test_manifest_notes_name_swatch_users_and_pictures(snapshot):
    notes = ai.manifest_data(snapshot)["notes"]
    assert any("Global swatch “Brand Green” (#16A34A) is used by 3 objects" in note for note in notes)
    assert any("Spot color “Spot Orange”" in note for note in notes)
    assert any("1 picture is left exactly as it is" in note for note in notes)
    users = ai.swatch_users(snapshot)
    assert sorted(users["Brand Green"]) == ["Global A", "Global B", "Global tint"]


def test_bounds_are_fractions_of_the_first_artboard(snapshot):
    manifest = ai.manifest_from(snapshot, KIT / "x.colorroom-manifest.json")
    left, top, right, bottom = _entry(manifest, "Card").bounds
    assert (round(left, 3), round(top, 3)) == (0.05, round(40 / 600, 3))
    assert _entry(manifest, "Card art").bounds is None  # it sits on the second artboard


def test_cmyk_manifest_says_colors_are_converted(snapshot):
    raw = copy.deepcopy(snapshot)
    raw["document"]["colorMode"] = "CMYK"
    notes = ai.manifest_data(raw)["notes"]
    assert notes[0].startswith("This document is in CMYK.")


def test_color_words():
    assert ai.color_text({"type": "rgb", "values": [1, 2, 3], "hex": "#010203"}) == "#010203"
    assert ai.color_text({"type": "cmyk", "values": [85.53, 72.33, 0, 0], "hex": "#3D59A8"}) == "CMYK 85.5/72.3/0/0 (#3D59A8)"
    assert ai.color_text({"type": "global", "swatch": "G", "tint": 50, "hex": "#16A34A"}) == "global swatch G (#16A34A, 50% tint)"
    assert ai.color_text(None) == "none"
    assert ai.same_color({"type": "rgb", "values": [1, 2, 3], "hex": "#010203"}, {"type": "rgb", "values": [1, 2, 3.001], "hex": "#010203"})
    assert not ai.same_color({"type": "rgb", "values": [1, 2, 3], "hex": "#010203"}, {"type": "cmyk", "values": [1, 2, 3, 0], "hex": "#010203"})


# ---- applying: what should change ---------------------------------------------------------------


def test_eligibility_reasons(snapshot):
    assert ai.eligible(_item(snapshot, "Card"), **RULES) is None
    assert ai.eligible(_item(snapshot, "Locked box"), **RULES).startswith("locked")
    assert ai.eligible(_item(snapshot, "Frozen box"), **RULES).startswith("locked")
    assert ai.eligible(_item(snapshot, "Hidden box"), **RULES).startswith("hidden")
    assert ai.eligible(_item(snapshot, "Photo"), **RULES).startswith("linked picture")
    assert ai.eligible(_item(snapshot, "Locked box"), **dict(RULES, change_locked=True)) is None
    text = dict(_item(snapshot, "Headline"), missingFonts=["Gone-Regular"])
    assert "font is not installed" in ai.eligible(text, **RULES)
    assert ai.eligible(text, **dict(RULES, change_missing_fonts=True)) is None


def test_predicted_changes_follow_swatches_and_gradients(snapshot):
    mapping = base.normalize_mapping(MAPPING)
    wanted = ai.predicted_changes(snapshot, mapping, ai.rgb_targets(mapping), **RULES)
    manifest = ai.manifest_from(snapshot, KIT / "x.colorroom-manifest.json")
    names = {}
    for change in wanted:
        names.setdefault(manifest.layer(change["id"]).name, []).append((change["key"], change["to"]))
    assert names["Card"] == [("fill", "#E63946"), ("stroke", "#334155")]
    assert names["Headline"] == [("text/0-6/fill", "#334155"), ("text/6-11/fill", "#0D9488")]
    assert names["Sky"] == [("fill/gradient/0", "#FACC15")]
    assert names["Global tint"] == [("fill", "#7C3AED")]
    assert names["Spot rect"] == [("fill", "#22D3EE")]
    for left_alone in ("Locked box", "Hidden box", "Frozen box", "Photo", "Paper", "Star", "Badge back"):
        assert left_alone not in names
    everything = ai.predicted_changes(snapshot, mapping, ai.rgb_targets(mapping), **dict(RULES, change_locked=True, change_hidden=True))
    assert {manifest.layer(change["id"]).name for change in everything} >= {"Locked box", "Hidden box", "Frozen box"}


def test_a_swap_changes_each_color_once(snapshot):
    mapping = base.normalize_mapping({"#1D4ED8": "#111827", "#111827": "#1D4ED8"})
    expected = ai.expected_colors(snapshot, mapping, ai.rgb_targets(mapping), change_swatches=True, **RULES)
    card = expected["items"][_item(snapshot, "Card")["id"]]
    assert (card["fill"]["hex"], card["stroke"]["hex"]) == ("#111827", "#1D4ED8")


def _simulated_report(snapshot, after, mapping=MAPPING, **rules):
    rules = dict(RULES, **rules)
    mapping = base.normalize_mapping(mapping)
    targets = ai.rgb_targets(mapping)
    expected = ai.expected_colors(snapshot, mapping, targets, change_swatches=True, **rules)
    wanted = ai.predicted_changes(snapshot, mapping, targets, change_swatches=True, **rules)
    report = base.ApplyReport("Illustrator", "x.ai", "x - Colorroom A.ai", dict(mapping))
    path = KIT / "x.ai"
    base.verify(ai.manifest_from(snapshot, path), ai.manifest_from(after, path, runs_from=snapshot), wanted, report)
    ai.IllustratorBridge._check_values(snapshot, after, expected, report)
    ai.check_definitions(snapshot, after, expected, report)
    ai.check_structure(snapshot, after, report)
    return report, expected


def test_the_check_passes_when_the_saved_file_is_exactly_as_expected(snapshot):
    mapping = base.normalize_mapping(MAPPING)
    expected = ai.expected_colors(snapshot, mapping, ai.rgb_targets(mapping), change_swatches=True, **RULES)
    after = ai.future_snapshot(snapshot, expected)
    report, _ = _simulated_report(snapshot, after)
    assert report.ok, report.summary()
    assert len(report.changed) == 11
    assert all(report.checks.values())


def test_the_check_catches_a_wrong_color(snapshot):
    mapping = base.normalize_mapping(MAPPING)
    expected = ai.expected_colors(snapshot, mapping, ai.rgb_targets(mapping), change_swatches=True, **RULES)
    after = ai.future_snapshot(snapshot, expected)
    _item(after, "Card")["fill"] = {"type": "rgb", "values": [230, 57, 71], "hex": "#E63947"}
    report, _ = _simulated_report(snapshot, after)
    assert not report.ok
    assert "should be #E63946 but is #E63947" in report.summary()


def test_the_check_catches_a_color_that_moved_without_being_asked(snapshot):
    mapping = base.normalize_mapping(MAPPING)
    expected = ai.expected_colors(snapshot, mapping, ai.rgb_targets(mapping), change_swatches=True, **RULES)
    after = ai.future_snapshot(snapshot, expected)
    _item(after, "Locked box")["fill"] = {"type": "rgb", "values": [230, 57, 70], "hex": "#E63946"}
    report, _ = _simulated_report(snapshot, after)
    assert not report.ok
    assert any("Locked box" in problem for problem in report.problems)


def test_the_check_catches_a_broken_swatch_link_and_structure(snapshot):
    mapping = base.normalize_mapping(MAPPING)
    expected = ai.expected_colors(snapshot, mapping, ai.rgb_targets(mapping), change_swatches=True, **RULES)
    after = copy.deepcopy(ai.future_snapshot(snapshot, expected))
    _item(after, "Global A")["fill"] = {"type": "rgb", "values": [124, 58, 237], "hex": "#7C3AED"}  # same hex, link lost
    after["layers"][0]["name"] = "Renamed"
    after["document"]["artboards"][0]["rect"] = [0, 600, 801, 0]
    after["counts"]["rasterItems"] = 1
    report, _ = _simulated_report(snapshot, after)
    assert not report.ok
    failed = {name for name, passed in report.checks.items() if not passed}
    assert "every layer and sublayer is still there, with the same names, visibility and locks" in failed
    assert "every artboard has the same name, size and position" in failed
    assert "nothing was rasterized, and the pictures are untouched" in failed
    assert "only colors changed inside the layers" in failed  # the swatch link is part of the signature


def test_the_check_catches_a_gradient_that_did_not_change(snapshot):
    mapping = base.normalize_mapping(MAPPING)
    expected = ai.expected_colors(snapshot, mapping, ai.rgb_targets(mapping), change_swatches=True, **RULES)
    after = ai.future_snapshot(snapshot, expected)
    sky = next(g for g in after["gradients"] if g["name"] == "Test Sky")
    sky["stops"][0]["color"] = {"type": "rgb", "values": [14, 165, 233], "hex": "#0EA5E9"}
    report, _ = _simulated_report(snapshot, after)
    assert not report.ok
    assert any("Gradient “Test Sky” does not hold the expected stop colors" in problem for problem in report.problems)


# ---- applying: the steps, with Illustrator replaced by recorded answers -----------------------------


class FakeIllustrator(ai.IllustratorBridge):
    """Answers like Illustrator would, from the recorded snapshot. Records every script sent."""

    def __init__(self, before: dict, after: dict, open_documents=(), fail_on: str = ""):
        super().__init__()
        self.before, self.after, self.sent = before, after, []
        self.open_documents, self.fail_on = list(open_documents), fail_on
        self.reads = 0

    def _open_documents(self):
        return self.open_documents

    def run_script(self, script, args=None, *, start=None):
        name = Path(script).name
        self.sent.append((name, dict(args or {})))
        if name == self.fail_on:
            raise BridgeError("Illustrator reported a problem: test", code="script")
        if name == "illustrator_read.jsx":
            self.reads += 1
            return copy.deepcopy(self.before if self.reads == 1 else self.after)
        if name == "illustrator_apply.jsx":
            return {"ok": True, "targets": ai.rgb_targets(args["mapping"]), "changes": [], "skipped": [], "colorMode": "RGB"}
        return {"ok": True}


def _source(work: Path) -> Path:
    source = work / "poster.ai"
    source.write_bytes(b"%PDF-1.6 test file, not a real document\n")
    return source


def test_apply_works_on_a_copy_and_reports(snapshot, work):
    mapping = base.normalize_mapping(MAPPING)
    after = ai.future_snapshot(snapshot, ai.expected_colors(snapshot, mapping, ai.rgb_targets(mapping), change_swatches=True, **RULES))
    source = _source(work)
    original = hashlib.sha256(source.read_bytes()).hexdigest()
    bridge = FakeIllustrator(snapshot, after)
    report = bridge.apply(source, MAPPING, report_path=work / "report.json")
    assert report.ok, report.summary()
    assert Path(report.saved).name == "poster - Colorroom A.ai"
    assert hashlib.sha256(source.read_bytes()).hexdigest() == original
    sent = [name for name, _ in bridge.sent]
    assert sent == ["illustrator_open.jsx", "illustrator_read.jsx", "illustrator_apply.jsx", "illustrator_save.jsx", "illustrator_read.jsx"]
    for name, args in bridge.sent:
        assert args["path"].endswith("poster - Colorroom A.ai"), name  # every script works on the copy
    skipped = {item.name: item.reason for item in report.skipped}
    assert skipped["Locked box"].startswith("locked") and skipped["Hidden box"].startswith("hidden")
    assert skipped["Photo"].startswith("linked picture")
    assert any("Brand Green" in note for note in report.notes)
    assert json.loads((work / "report.json").read_text(encoding="utf-8"))["ok"] is True


def test_apply_refuses_to_save_over_the_original_or_change_type(snapshot, work):
    source = _source(work)
    bridge = FakeIllustrator(snapshot, snapshot)
    with pytest.raises(BridgeError) as error:
        bridge.apply(source, MAPPING, save_as=source)
    assert error.value.code == "same-file"
    with pytest.raises(BridgeError) as error:
        bridge.apply(source, MAPPING, save_as=work / "poster.pdf")
    assert error.value.code == "type"
    with pytest.raises(BridgeError) as error:
        bridge.apply(source, {"#123456": "#123456"})
    assert error.value.code == "no-changes"
    assert bridge.sent == []


def test_apply_refuses_a_copy_that_is_open_and_notes_unsaved_changes(snapshot, work):
    source = _source(work)
    target = work / "poster - Colorroom A.ai"
    bridge = FakeIllustrator(snapshot, snapshot, open_documents=[{"path": str(target), "saved": True}])
    with pytest.raises(BridgeError) as error:
        bridge.apply(source, MAPPING, save_as=target)
    assert error.value.code == "already-open"
    assert not target.exists()


def test_a_failed_apply_closes_and_deletes_only_the_copy(snapshot, work):
    source = _source(work)
    bridge = FakeIllustrator(snapshot, snapshot, fail_on="illustrator_apply.jsx")
    with pytest.raises(BridgeError):
        bridge.apply(source, MAPPING)
    assert source.exists()
    assert not (work / "poster - Colorroom A.ai").exists()
    assert bridge.sent[-1][0] == "illustrator_close.jsx"


def test_script_errors_become_plain_messages():
    assert ai.script_message({"error": "That file is already open in Illustrator."}) == "That file is already open in Illustrator."
    assert ai.script_message({"error": "Target layer cannot be modified"}) == "Illustrator reported a problem: Target layer cannot be modified"


# ---- live: Illustrator on this computer --------------------------------------------------------


def _live_bridge() -> ai.IllustratorBridge:
    if os.environ.get("COLORROOM_LIVE") != "1":
        pytest.skip("live Illustrator tests run only with COLORROOM_LIVE=1")
    bridge = ai.IllustratorBridge()
    if not bridge.available():
        pytest.skip("Illustrator or pywin32 is not available on this computer")
    try:
        bridge.state()
    except BridgeError as error:
        pytest.skip(f"Illustrator cannot be reached: {error}")
    return bridge


def _call(function, *args, **options):
    """Run one bridge call; a failure shows Illustrator's own words and the COM details."""
    try:
        return function(*args, **options)
    except BridgeError as error:
        pytest.fail(f"{error.message} [code {error.code}] {error.detail}")


def _make_document(bridge, folder: Path, mode: str) -> Path:
    from PIL import Image

    photo = folder / "photo.png"
    Image.new("RGB", (40, 30), (90, 120, 60)).save(photo)
    path = folder / f"test-{mode.lower()}.ai"
    _call(bridge.run_script, MAKE_DOC, {"path": path.as_posix(), "mode": mode, "image": photo.as_posix()})
    return path


def _render(path: Path, points: dict) -> dict:
    """Colors drawn by the file's PDF part (PDFium, independent of Illustrator's scripting)."""
    import pypdfium2 as pdfium

    document = pdfium.PdfDocument(str(path))
    try:
        image = document[0].render(scale=1).to_pil().convert("RGB")
    finally:
        document.close()
    return {name: "#%02X%02X%02X" % image.getpixel(point) for name, point in points.items()}


@pytest.mark.illustrator
def test_live_rgb_round_trip(work):
    bridge = _live_bridge()
    source = _make_document(bridge, work, "RGB")
    original = hashlib.sha256(source.read_bytes()).hexdigest()
    manifest = _call(bridge.read, source, work_dir=work / "read")
    assert manifest.preview is not None and manifest.preview.is_file()
    assert {color.hex for color in manifest.colors(include_hidden=True)} >= set(MAPPING)
    report = _call(bridge.apply, source, MAPPING)
    assert report.ok, report.summary()
    assert all(report.checks.values()), report.checks
    assert hashlib.sha256(source.read_bytes()).hexdigest() == original
    kinds = {(layer.name, part.key): part.now for layer in report.changed for part in layer.parts}
    assert kinds[("Global A", "fill")] == "#7C3AED" and kinds[("Global tint", "fill")] == "#7C3AED"
    assert kinds[("Sky", "fill/gradient/0")] == "#FACC15"
    assert kinds[("Headline", "text/6-11/fill")] == "#0D9488"
    assert kinds[("Ring", "fill")] == "#0D9488" and kinds[("Detail", "stroke")] == "#E63946"
    assert {item.name for item in report.skipped} == {"Locked box", "Hidden box", "Frozen box", "Photo"}
    drawn = _render(Path(report.saved), {"card": (190, 130), "global": (290, 300), "spot": (290, 390), "locked": (630, 170), "badge back": (720, 90)})
    assert drawn == {"card": "#E63946", "global": "#7C3AED", "spot": "#22D3EE", "locked": "#1D4ED8", "badge back": "#F59E0B"}


@pytest.mark.illustrator
def test_live_locked_and_hidden_on_request_keep_their_locks(work):
    bridge = _live_bridge()
    source = _make_document(bridge, work, "RGB")
    report = _call(bridge.apply, source, {"#1D4ED8": "#E63946"}, change_locked=True, change_hidden=True)
    assert report.ok, report.summary()
    changed = {layer.name for layer in report.changed}
    assert {"Locked box", "Hidden box", "Frozen box"} <= changed
    after = bridge.read_raw(Path(report.saved))
    assert _item(after, "Locked box")["locked"] and _item(after, "Hidden box")["hidden"]
    assert next(layer for layer in after["layers"] if layer["name"] == "Frozen")["locked"]


@pytest.mark.illustrator
def test_live_cmyk_document_stays_cmyk(work):
    bridge = _live_bridge()
    source = _make_document(bridge, work, "CMYK")
    manifest = _call(bridge.read, source, work_dir=work / "read", preview=False)
    assert manifest.color_mode == "CMYK"
    card = {part.key: part.hex for part in _entry(manifest, "Card").parts}
    report = _call(bridge.apply, source, {card["fill"]: "#E63946"})
    assert report.ok, report.summary()
    assert any("This document is CMYK" in note for note in report.notes)
    after = bridge.read_raw(Path(report.saved))
    assert after["document"]["colorMode"] == "CMYK"
    assert _item(after, "Card")["fill"]["type"] == "cmyk"


@pytest.mark.illustrator
def test_live_read_leaves_the_users_open_document_open(work):
    bridge = _live_bridge()
    source = _make_document(bridge, work, "RGB")
    bridge.run_script("illustrator_open.jsx", {"path": source.as_posix()})
    try:
        bridge.read(source, work_dir=work / "read", preview=False)
        assert any(doc.get("path") and Path(doc["path"]) == source for doc in bridge.state()["documents"])
        with pytest.raises(BridgeError):
            bridge.run_script("illustrator_open.jsx", {"path": source.as_posix()})  # never opened twice
    finally:
        bridge.run_script("illustrator_close.jsx", {"path": source.as_posix()})


@pytest.mark.illustrator
def test_live_real_file_copy(work):
    """The user's own file, if it is in test-kit/real: always on a copy, never on the original."""
    real = KIT / "real" / "illustrator-real.ai"
    if not real.is_file():
        pytest.skip("test-kit/real/illustrator-real.ai is not here")
    bridge = _live_bridge()
    original = hashlib.sha256(real.read_bytes()).hexdigest()
    copy_path = work / "illustrator-real.ai"
    shutil.copyfile(real, copy_path)
    manifest = _call(bridge.read, copy_path, work_dir=work / "read", preview=False)
    mapping = {color.hex: ("#FACC15" if index == 0 else "#1D4ED8") for index, color in enumerate(manifest.colors(include_hidden=True)[:2])}
    report = _call(bridge.apply, copy_path, mapping)
    assert report.ok, report.summary()
    assert hashlib.sha256(real.read_bytes()).hexdigest() == original
