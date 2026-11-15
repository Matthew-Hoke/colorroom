"""Tests for the rules of the Your project tab, without any window (made by Opus 5.5)."""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest
from PIL import Image

from colorroom.core import file_export
from colorroom.core import project as rules
from colorroom.core.colorspace import contrast_ratio, hex_to_key, key_to_oklab, oklab_distance
from colorroom.core.designer import Library, NamedPalette, PaletteColor, Project


def palette(name, *colors) -> NamedPalette:
    return NamedPalette(name, [PaletteColor(color) for color in colors])


def lightness(color: str) -> float:
    return key_to_oklab(hex_to_key(color))[0]


# --- what Export does ------------------------------------------------------------------


@pytest.mark.parametrize(
    "source, app, label",
    [
        ("psd", True, "Export to Photoshop"),
        ("psd", False, "Export as PSD…"),
        ("psb", True, "Export to Photoshop"),
        ("psb", False, "Export as PSB…"),  # the button writes a .psb, so it says so
        ("ai", True, "Export to Illustrator"),
        ("ai", False, "Export as PDF…"),
        ("indd", True, "Export to InDesign"),
        ("idml", True, "Export to InDesign"),
        ("idml", False, "Export as IDML…"),
        ("png", False, "Export as PNG…"),
        ("png", True, "Export as PNG…"),  # no program writes PNGs for Colorroom
        ("jpg", False, "Export as JPEG…"),
        ("tiff", False, "Export as TIFF…"),
        ("pdf", False, "Export as PDF…"),
        ("", False, "Export as PNG…"),  # a picture that is not a file (the sample, a paste)
    ],
)
def test_the_export_button_label(source, app, label):
    assert rules.export_label(source, app) == label


def test_accuracy_line_and_formats():
    assert rules.accuracy_line("psd", False) == "The exported file matches this preview."
    assert rules.accuracy_line("png", True) == "The exported file matches this preview."
    assert rules.accuracy_line("psd", True) == "The preview is close. The exported file is exact."
    assert rules.export_formats("psd")[0] == "psd" and rules.export_formats("ai")[0] == "pdf"
    assert rules.export_formats("indd")[0] == "png"
    assert rules.source_format("C:/x/Poster.PSD") == "psd" and rules.source_format("a.indd") == "indd"
    assert rules.source_format("a.idml") == "idml" and rules.source_format("") == ""


def test_indd_and_idml_messages_never_promise_what_cannot_be_opened():
    assert "File > Export > InDesign Markup (IDML)" in rules.indd_message(True)
    assert "IDML" not in rules.indd_message(False) and "Adobe PDF" in rules.indd_message(False)
    assert "Adobe PDF" in rules.IDML_NOT_YET
    assert ".indd" not in rules.OPEN_EXTENSIONS and ".idml" not in rules.OPEN_EXTENSIONS


def test_suggested_target_never_replaces_a_file(tmp_path):
    source = tmp_path / "Poster.psd"
    source.write_bytes(b"x")
    first = rules.suggested_target(source)
    assert first.name == "Poster - Colorroom.psd"
    first.write_bytes(b"y")
    assert rules.suggested_target(source).name == "Poster - Colorroom 2.psd"


# --- palettes to try -------------------------------------------------------------------


def test_ideas_turn_the_files_colors_and_keep_its_light_and_dark():
    colors = ["#FFFFFF", "#2B4F8C", "#E0452F", "#222222"]
    jobs = ["Background", "Primary", "Secondary", "Text"]
    ideas = rules.ideas_from_file(colors, jobs)
    assert [idea.key for idea in ideas] == [f"idea:{style}" for style in rules.IDEA_STEPS]
    starter = ideas[0]
    assert starter.label == rules.STARTER_LABEL and starter.group == rules.IDEAS and starter.from_file
    assert starter.colors[0] == "#FFFFFF" and starter.colors[-1] == "#222222"  # grays keep their color
    for before, after in zip(colors[1:3], starter.colors[1:3]):
        assert oklab_distance(before, after) > 0.08  # the colors really change...
        assert abs(lightness(before) - lightness(after)) < 0.01  # ...at the same lightness
    roles = dict(starter.roles)
    assert roles == {"Background": "#FFFFFF", "Primary": starter.colors[1], "Secondary": starter.colors[2], "Text": "#222222"}
    assert len({idea.colors for idea in ideas}) == len(ideas)  # every idea is different
    assert rules.ideas_from_file([]) == []
    gray = rules.ideas_from_file(["#FFFFFF", "#888888", "#000000"], ["Background", "Surface", "Text"])[0]
    assert gray.colors[0] == "#FFFFFF" and gray.colors[2] == "#000000" and gray.colors[1] != "#888888"


def test_a_turned_color_keeps_its_contrast():
    for color, steps in (("#E6C828", 4), ("#102A54", 6), ("#E0452F", 1)):
        turned = rules.turn(color, steps)
        assert turned != color
        assert abs(contrast_ratio(turned, "#000000") - contrast_ratio(color, "#000000")) < 1.0


def test_the_starter_palette():
    ideas = rules.ideas_from_file(["#FFFFFF", "#2B4F8C"], ["Background", "Text"])
    project, library = Project(), Library()
    assert rules.choose_starter(project, library, ideas) == "idea:triadic"  # nothing of your own yet
    assert rules.choose_starter(None, None, []) == ""
    library.add(palette("Old favorite", "#111111"), favorite=True)
    library.add(palette("Not a favorite", "#222222"))
    newest = library.add(palette("New favorite", "#333333", "#444444"), favorite=True)
    assert rules.choose_starter(project, library, ideas) == rules.library_key(newest)
    project.current.add(["#B47A3C"])
    assert rules.choose_starter(project, library, ideas) == rules.mine_key(project.current)


def test_the_picker_lists_my_palettes_then_the_library_then_the_ideas():
    project = Project([palette("Brand", "#0B132B", "#F4F1DE"), palette("Empty"), palette("Spare", "#E07A5F")])
    library = Library()
    plain = library.add(palette("Plain", "#123456"))
    starred = library.add(palette("Starred", "#654321"), favorite=True)
    ideas = rules.ideas_from_file(["#FFFFFF", "#2B4F8C"], ["Background", "Text"])
    choices = rules.palette_choices(project, library, ideas)
    assert [choice.group for choice in choices] == [rules.MINE] * 2 + [rules.LIBRARY] * 2 + [rules.IDEAS] * len(ideas)
    assert [choice.label for choice in choices[:4]] == ["Brand", "Spare", "★ Starred", "Plain"]  # favorites first
    assert choices[0].colors == ("#0B132B", "#F4F1DE") and choices[2].key == rules.library_key(starred)
    assert choices[3].key == rules.library_key(plain) and not choices[3].favorite
    assert rules.palette_choices(None, None) == []


def test_starting_variant_and_small_details():
    assert rules.starting_variant(["#111111", "#FFFFFF"]) == 1  # a dark file keeps a dark background
    assert rules.starting_variant(["#FFFFFF", "#111111"]) == 0
    assert rules.starting_variant([]) == 0
    assert rules.small_details([0.5, 0.3, 0.1, 0.02, 0.01, 0.001]) == [3, 4, 5]
    assert rules.small_details([0.98, 0.01, 0.01]) == [2]  # the two biggest always show


# --- photos left alone -----------------------------------------------------------------


def test_photo_areas_and_the_whole_picture():
    shape = (100, 200)
    photo = (0.1, 0.1, 0.5, 0.5)
    assert rules.leave_out(shape, [photo], [], False) == ([photo], False)
    assert rules.leave_out(shape, [photo], [], True) == ([], False)  # Change photos too
    mine = (0.6, 0.6, 0.9, 0.9)
    assert rules.leave_out(shape, [photo], [mine], True) == ([mine], False)  # a hand-drawn box always stays
    everything = (0.0, 0.0, 1.0, 0.95)
    assert rules.leave_out(shape, [everything], [], False) == ([], True)  # one picture layer: recolor it
    assert rules.leave_out(shape, [], [], False, known=[(0.0, 0.0, 1.0, 1.0)]) == ([], True)
    assert rules.leave_out(shape, [], [], False, known=[photo]) == ([], False)
    assert rules.leave_out(shape, [everything], [mine], False) == ([mine], True)
    # A paper layer over everything is recolored; a real photo on it is still left alone.
    assert rules.leave_out(shape, [everything, photo], [], False) == ([photo], True)


def test_photo_line_wording():
    whole = rules.photo_line(0.0, 0, False, False, False, True)
    assert whole == "Your file is one picture layer, so everything is recolored. Drag a box over any photo to leave it alone."
    assert rules.photo_line(0.38, 1, False, False, False, False).startswith("Photos: 1 area left alone (38%). A best guess")
    assert "the file's own pictures" in rules.photo_line(0.2, 2, True, False, False, False)
    assert rules.photo_line(0.2, 2, True, False, False, False).startswith("Photos: 2 areas left alone (20%)")
    assert rules.photo_line(0.0, 0, False, False, False, False).startswith("No photos found")
    assert rules.photo_line(0.0, 0, False, False, True, False).startswith("Photos are recolored too")
    assert "Forget my boxes" in rules.photo_line(1.0, 1, False, True, False, False)
    assert rules.photo_line(0.2, 1, True, False, False, True).startswith("Photos: 1 area left alone (20%)")
    assert rules.photo_line(0.0, 0, False, True, False, True).startswith("Your file is one picture layer")


def test_contrast_line():
    assert rules.contrast_line("#000000", "#FFFFFF") == "Text on background: easy to read (21.0:1)"
    assert "readable (" in rules.contrast_line("#767676", "#FFFFFF")
    assert "only at large sizes" in rules.contrast_line("#949494", "#FFFFFF")
    assert "hard to read" in rules.contrast_line("#DDDDDD", "#FFFFFF")


# --- what the file is ------------------------------------------------------------------


def test_file_facts(tmp_path):
    assert rules.FileFacts("Poster.psd", 1000, 640, "", 24, "RGB", 300.0).line() == "1,000 × 640 · 24 layers · RGB · 300 ppi"
    assert rules.FileFacts("a.pdf", 595, 842, "pt", pages=4).line() == "595 × 842 pt · 4 pages"
    assert rules.FileFacts("x").line() == ""
    png = tmp_path / "logo.png"
    Image.new("RGB", (60, 40), "#102A54").save(png, dpi=(144, 144))
    facts = rules.read_facts(png)
    assert (facts.name, facts.width, facts.height, facts.mode, facts.ppi) == ("logo.png", 60, 40, "RGB", 144)
    assert rules.read_facts(tmp_path / "missing.png").width == 0  # never raises
    picture = np.zeros((30, 50, 4), dtype=np.uint8)
    assert rules.read_facts("", None, 0, picture, "Pasted image").line() == "50 × 30 · RGB"


def test_file_facts_of_a_photoshop_file(tmp_path):
    pytest.importorskip("psd_tools")
    from psd_tools import PSDImage

    from colorroom.core import documents

    psd = PSDImage.new("RGB", (300, 200), color=(255, 255, 255))
    psd.append(psd.create_pixel_layer(Image.new("RGB", (300, 200), (0, 0, 0)), name="Paper"))
    psd.append(psd.create_pixel_layer(Image.new("RGB", (40, 40), (230, 200, 40)), name="Block"))
    file_export._set_psd_dpi(psd, 300)
    psd.save(str(tmp_path / "poster.psd"))
    document = documents.open_document(tmp_path / "poster.psd")
    try:
        facts = rules.read_facts(tmp_path / "poster.psd", document)
    finally:
        document.close()
    assert facts.line() == "300 × 200 · 2 layers · RGB · 300 ppi"


# --- the app links ---------------------------------------------------------------------


def request(tmp_path) -> rules.ExportRequest:
    recipe = file_export.Recipe(["#000000", "#e6c828", "#FFFFFF"], ["#FFFFFF", None, "#ffffff"], [(0, 0, 0.5, 0.5)])
    return rules.ExportRequest(str(tmp_path / "Poster.psd"), "psd", 0, recipe, tmp_path / "Poster - Colorroom.psd")


def test_the_request_lists_only_the_colors_that_change(tmp_path):
    assert request(tmp_path).mapping == {"#000000": "#FFFFFF"}


def test_every_kind_of_app_link_is_called_the_documented_way(tmp_path):
    asked = []

    def plain(req, progress, cancelled):
        asked.append(("callable", req.source_format))
        return "done"

    class Exporter:
        def available(self):
            return True

        def export(self, req, progress, cancelled):
            asked.append(("export", req.page))
            return "exported"

    class Bridge:
        def available(self):
            return False

        def apply(self, source_path, mapping, save_as=None):
            asked.append(("apply", Path(source_path).name, mapping, save_as))
            return "applied"

    wanted = request(tmp_path)
    assert rules.run_app_link(plain, wanted) == "done"
    assert rules.run_app_link(Exporter(), wanted) == "exported"
    assert rules.run_app_link(Bridge(), wanted) == "applied"
    assert asked == [("callable", "psd"), ("export", 0), ("apply", "Poster.psd", {"#000000": "#FFFFFF"}, None)]
    with pytest.raises(TypeError):
        rules.run_app_link(object(), wanted)
    assert rules.link_available(plain) and rules.link_available(Exporter())
    assert not rules.link_available(Bridge()) and not rules.link_available(None)

    class Broken:
        def available(self):
            raise OSError("registry")

    assert not rules.link_available(Broken())


def test_report_parts_read_both_kinds_of_report(tmp_path):
    report = file_export.ExportReport(str(tmp_path / "out.psd"), "psd", "layers", changed=["Block"])
    summary, details, saved, ok = rules.report_parts(report)
    assert summary.startswith("Saved out.psd") and "Recolored in place" in details and saved.endswith("out.psd") and ok

    class ApplyReport:  # the shape of core.bridges.base.ApplyReport
        saved = str(tmp_path / "Poster - Colorroom A.psd")
        ok = False

        def summary(self):
            return "2 layers changed in Poster - Colorroom A.psd.\nCheck failed: size."

    summary, details, saved, ok = rules.report_parts(ApplyReport())
    assert summary.splitlines()[1] == "Check failed: size." and details == "" and saved.endswith("A.psd") and not ok
