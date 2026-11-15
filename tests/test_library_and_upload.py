"""Tests for the library (folders, favorites), opening a file on Your project and
recoloring it, hand-set roles, contrast export and the dark mode button."""

from __future__ import annotations

import json
import os
import time
import xml.etree.ElementTree as ET

import numpy as np
import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")  # no window appears while testing

pytest.importorskip("PySide6")

from PIL import Image  # noqa: E402
from PySide6.QtCore import QSettings  # noqa: E402
from PySide6.QtGui import QGuiApplication  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from colorroom.core import designer  # noqa: E402
from colorroom.core.colorspace import contrast_ratio, oklab_distance  # noqa: E402
from colorroom.core.designer import Library, NamedPalette, PaletteColor  # noqa: E402
from colorroom.core.sample import sample_image  # noqa: E402
from colorroom.ui import theme  # noqa: E402
from colorroom.ui.main_window import TAB_LIBRARY, TAB_PALETTE, TAB_PROJECT, MainWindow  # noqa: E402
from colorroom.ui.theme import apply_theme  # noqa: E402

BRAND = ["#0B132B", "#F4F1DE", "#E07A5F", "#3D405B", "#81B29A", "#F2CC8F"]


def flat_image(colors, size=40) -> np.ndarray:
    """Vertical stripes of flat colors, one per entry, as RGBA."""
    rgba = np.zeros((size, size * len(colors), 4), dtype=np.uint8)
    for position, color in enumerate(colors):
        rgba[:, position * size : (position + 1) * size] = (*[int(color[i : i + 2], 16) for i in (1, 3, 5)], 255)
    return rgba


def palette(name, *colors) -> NamedPalette:
    return NamedPalette(name, [PaletteColor(color) for color in colors])


# ======================================================================================
# Engine
# ======================================================================================


def test_library_folders_favorites_and_search():
    library = Library()
    work = library.add_folder("Client work")
    assert library.add_folder("Client work") == "Client work 2" and library.add_folder("  ") == "New folder"
    first = library.add(palette("Autumn", "#B47A3C", "#1E2A3B"), work)
    second = library.add(palette("Autumn", "#FFFFFF"), favorite=True)  # names are made unique
    third = library.add(palette("Ice", "#A0C4FF"), "No such folder")  # unknown folder: not filed
    assert [e.palette.name for e in library.entries] == ["Autumn", "Autumn 2", "Ice"]
    assert first.folder == work and third.folder == ""
    assert library.in_folder(work) == [first] and library.favorites() == [second]
    assert library.search("ice") == [third] and library.search("#b47a") == [first]
    assert library.search("client") == [first] and len(library.search("")) == 3
    library.move(third, work)
    assert library.rename_folder(work, "Brands") == "Brands" and third.folder == "Brands" and first.folder == "Brands"
    assert library.rename(second, "Autumn") == "Autumn 2"  # the name is taken by another palette
    assert library.rename(second, "Snow") == "Snow"
    assert library.delete_folder("Brands") == 2
    assert first.folder == "" and "Brands" not in library.folders and len(library.entries) == 3
    library.move(first, "Client work 2")
    assert library.delete_folder("Client work 2", with_palettes=True) == 1 and first not in library.entries
    library.remove(third)
    assert library.entries == [second]


def test_library_is_a_copy_and_round_trips():
    source = NamedPalette("Brand", [PaletteColor("#B47A3C", "Clay"), PaletteColor("#1E2A3B")])
    library = Library()
    folder = library.add_folder("Logos")
    entry = library.add(source, folder, favorite=True)
    source.colors.clear()  # changing the working palette must not change the saved one
    assert entry.palette.hexes() == ["#B47A3C", "#1E2A3B"]
    back = Library.from_json(library.to_json())
    assert back.folders == ["Logos"] and len(back.entries) == 1
    saved = back.entries[0]
    assert (saved.palette.name, saved.folder, saved.favorite) == ("Brand", "Logos", True)
    assert saved.palette.colors[0].name == "Clay"
    for text in ("", "nope", "[]", '{"format": "colorroom-palettes"}'):
        with pytest.raises(ValueError, match="not a Colorroom library file"):
            Library.from_json(text)
    messy = json.dumps({"format": "colorroom-library", "folders": ["A", 5, "A", " "], "palettes": [{"name": "X", "colors": ["zz", "#abc"]}, 3, {"name": "Empty", "colors": []}]})
    cleaned = Library.from_json(messy)
    assert cleaned.folders == ["A"] and [e.palette.hexes() for e in cleaned.entries] == [["#AABBCC"]]


def test_roles_can_be_set_by_hand():
    auto = designer.assign_roles(BRAND)
    roles = designer.with_overrides(auto, {"Primary": "#81b29a", "Background": "#0B132B", "Nonsense": "#FFFFFF", "Accent": "zz"})
    assert roles.primary == "#81B29A" and roles.background == "#0B132B"
    assert roles.accent == auto.accent and roles.text == auto.text  # untouched jobs stay automatic
    assert contrast_ratio(roles.on_primary, roles.primary) >= 4.5
    assert designer.with_overrides(None, {"Primary": "#000000"}) is None
    assert designer.with_overrides(auto, {}) == auto


def test_find_layers_finds_the_flat_colors_of_an_image():
    colors = ["#FFFFFF", "#102A54", "#E0452F", "#F2B705"]
    layers = designer.find_layers(flat_image(colors))
    assert len(layers.colors) == 4
    for color in colors:
        assert min(oklab_distance(color, found) for found in layers.colors) < 0.01
    assert sum(layers.shares) == pytest.approx(1.0) and all(share == pytest.approx(0.25, abs=0.01) for share in layers.shares)
    assert layers.weights.shape == (40 * 160, 4)
    assert np.allclose(layers.weights.sum(axis=1), 1.0, atol=1e-4)


def test_recolor_replaces_exactly_the_chosen_colors():
    colors = ["#FFFFFF", "#102A54", "#E0452F", "#F2B705"]
    rgba = flat_image(colors)
    layers = designer.find_layers(rgba)
    assert np.array_equal(designer.recolor(layers, [None] * 4), rgba)  # keeping everything changes nothing
    assert np.array_equal(designer.recolor(layers, ["", "", "", ""]), rgba)
    red = next(i for i, color in enumerate(layers.colors) if oklab_distance(color, "#E0452F") < 0.01)
    targets = [None] * 4
    targets[red] = "#2E8B57"
    out = designer.recolor(layers, targets)
    stripe = out[20, 2 * 40 + 20]
    assert oklab_distance("#{:02X}{:02X}{:02X}".format(*stripe[:3]), "#2E8B57") < 0.01
    for position in (0, 1, 3):  # the other stripes are untouched
        assert tuple(out[20, position * 40 + 20]) == tuple(rgba[20, position * 40 + 20])
    assert np.array_equal(out[..., 3], rgba[..., 3])


def test_recolor_keeps_shading_and_transparency():
    rgba = np.zeros((20, 256, 4), dtype=np.uint8)
    rgba[..., 2] = np.linspace(60, 255, 256).astype(np.uint8)  # a blue running dark to light
    rgba[..., 3] = 255
    rgba[:5, :, 3] = 0  # a transparent band
    layers = designer.find_layers(rgba, count=1)
    out = designer.recolor(layers, ["#C0392B"] + [None] * (len(layers.colors) - 1))
    row = out[12].astype(int)
    brightness = row[:, 0] * 54 + row[:, 1] * 183 + row[:, 2] * 19
    assert np.all(np.diff(brightness) >= -300)  # still runs dark to light
    assert brightness[-1] > brightness[0] + 2000
    assert row[128, 0] > row[128, 2]  # and it is red now, not blue
    assert np.array_equal(out[..., 3], rgba[..., 3])


def test_suggested_roles_make_sense():
    layers = designer.find_layers(flat_image(["#FFFFFF", "#FFFFFF", "#FFFFFF", "#111111", "#E0452F"]))
    jobs = designer.suggest_targets(layers, designer.assign_roles(BRAND))
    by_color = dict(zip(layers.colors, jobs))
    white = min(layers.colors, key=lambda color: oklab_distance(color, "#FFFFFF"))
    black = min(layers.colors, key=lambda color: oklab_distance(color, "#111111"))
    red = min(layers.colors, key=lambda color: oklab_distance(color, "#E0452F"))
    assert by_color[white] == "Background" and by_color[black] == "Text" and by_color[red] == "Primary"
    assert designer.suggest_targets(layers, None) == [""] * len(layers.colors)


def test_contrast_results_as_text_and_pictures():
    colors, names = ["#000000", "#FFFFFF", "#777777"], ["Ink", "", 'Gray, "mid"']
    pairs = designer.contrast_pairs(colors, names)
    assert len(pairs) == 3 and pairs[0]["ratio"] == 21.0 and pairs[0]["level"] == "AAA"
    assert [pair["ratio"] for pair in pairs] == sorted((pair["ratio"] for pair in pairs), reverse=True)
    tsv = designer.contrast_text(colors, names).splitlines()
    assert tsv[0].split("\t") == ["Color", "Name", "With", "Name", "Contrast", "WCAG level"]
    assert tsv[1].split("\t") == ["#000000", "Ink", "#FFFFFF", "", "21.00", "AAA"]
    csv = designer.contrast_text(colors, names, "csv")
    assert '"Gray, ""mid"""' in csv and csv.endswith("\r\n")
    assert json.loads(designer.contrast_text(colors, names, "json"))["pairs"][0]["with"] == "#FFFFFF"
    markdown = designer.contrast_text(colors, names, "md").splitlines()
    assert markdown[0].startswith("| Color |") and markdown[1].count("---") == 6 and len(markdown) == 5
    assert "21.00  AAA" in designer.contrast_text(colors, names, "txt")
    css = designer.contrast_text(colors, names, "css")
    assert "--palette-1: #000000;" in css and "--palette-1 with --palette-2: 21.00 (AAA)" in css
    root = ET.fromstring(designer.contrast_svg(colors))
    cells = [text.text for text in root.iter("{http://www.w3.org/2000/svg}text") if text.text == "Aa"]
    assert len(cells) == 6  # every color on every other color


# ======================================================================================
# The window
# ======================================================================================


@pytest.fixture(scope="module")
def app():
    application = QApplication.instance() or QApplication([])
    apply_theme(application, "light")
    return application


def settle(app, seconds: float = 0.05) -> None:
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        app.processEvents()
        time.sleep(0.005)


@pytest.fixture
def window(app):
    window = MainWindow(persist=False)  # never touch the user's saved settings
    window.resize(1460, 940)
    window.show()
    window.open_sample()
    deadline = time.monotonic() + 30
    while window.index is None or window.is_busy():
        app.processEvents()
        assert time.monotonic() < deadline
    yield window
    window.close()
    apply_theme(app, "light")
    settle(app)


# --- opening a file on Your project and recoloring it -------------------------------------


def test_open_a_file_and_recolor_it(app, window, tmp_path):
    studio, project = window.studio, window.project
    studio.add_colors(BRAND, quiet=True)
    window.show_tab(TAB_PROJECT)
    settle(app)
    assert not project.has_upload() and project.stack.currentWidget() is project.empty_page
    assert not project.copy_preview_action.isEnabled()
    assert not project.grab().isNull()  # the empty state draws

    path = tmp_path / "my logo.png"
    Image.fromarray(flat_image(["#FFFFFF", "#102A54", "#E0452F", "#F2B705"], 30), "RGBA").save(path)
    assert project.load_upload(path)
    settle(app)
    assert project.has_upload() and project.stack.currentWidget() is project.work_page and project.mapping_box.isVisible()
    assert len(project.map_combos) == 4 and len(project.jobs()) == 4
    assert "Background" in project.jobs() and "Text" in project.jobs()
    assert project.palette_colors() == BRAND  # My palette has colors: the file starts with it
    roles = project.roles().as_dict()
    out = project.recolored()
    layers = project._layers
    for position, job in enumerate(project.jobs()):  # every stripe took the color of its role
        original = layers.colors[position]
        stripe = min(range(4), key=lambda i: oklab_distance(original, ["#FFFFFF", "#102A54", "#E0452F", "#F2B705"][i]))
        pixel = "#{:02X}{:02X}{:02X}".format(*out[15, stripe * 30 + 15][:3])
        assert oklab_distance(pixel, roles[job]) < 0.012, (job, pixel, roles[job])

    project.set_job(0, "")  # keep the first color as it is
    assert project.map_combos[0].currentText() == "Keep"
    kept = project.recolored()
    first = min(range(4), key=lambda i: oklab_distance(layers.colors[0], ["#FFFFFF", "#102A54", "#E0452F", "#F2B705"][i]))
    assert np.array_equal(kept[15, first * 30 + 15], layers.rgba[15, first * 30 + 15])
    project.keep_all()
    assert np.array_equal(project.recolored(), layers.rgba)  # More > Keep every original color
    assert all(combo.currentText() == "Keep" for combo in project.map_combos)
    project.suggest_jobs()
    assert "Background" in project.jobs()
    project.map_combos[1].setCurrentIndex(project.map_combos[1].findData("Accent"))  # using the control itself
    assert project.jobs()[1] == "Accent"
    assert not project.canvas.grab().isNull()

    assert project.copy_preview_action.isEnabled()
    project.copy_preview()
    copied = QGuiApplication.clipboard().image()
    assert (copied.width(), copied.height()) == (120, 30)  # the picture at its real size
    assert project.save_preview(tmp_path / "result")
    assert Image.open(tmp_path / "result.png").size == (120, 30)

    bad = tmp_path / "notes.png"
    bad.write_text("not an image")
    assert not project.load_upload(bad) and window.status_label.property("error")
    assert project.has_upload()  # the file opened before is still there


def test_changing_a_role_changes_the_recolored_image(app, window):
    studio, project, previews = window.studio, window.project, window.studio.mockups
    studio.add_colors(BRAND, quiet=True)
    project.set_upload(flat_image(["#FFFFFF", "#102A54"], 30), "test")
    background = project.jobs().index("Background")
    before = project.recolored()[15, background * 0 + 15].copy()
    project.set_role("Background", "#81B29A")
    assert project.overrides() == {"Background": "#81B29A"} and project.roles().background == "#81B29A"
    assert project.role_chooser.combos["Background"].currentData() == "#81B29A" and project.role_chooser.auto_button.isEnabled()
    layers = project._layers
    stripe = 0 if oklab_distance(layers.colors[background], "#FFFFFF") < 0.02 else 1
    after = project.recolored()[15, stripe * 30 + 15]
    assert oklab_distance("#{:02X}{:02X}{:02X}".format(*after[:3]), "#81B29A") < 0.012
    assert tuple(before) != tuple(after) or stripe != 0
    project.role_chooser.combos["Primary"].setCurrentIndex(project.role_chooser.combos["Primary"].findData("#F2CC8F"))
    assert project.roles().primary == "#F2CC8F"
    assert previews.overrides() == {}  # Previews keeps its own roles

    # In Previews, hand-set roles reach every preview.
    previews.set_role("Background", "#81B29A")
    previews.role_combos["Primary"].setCurrentIndex(previews.role_combos["Primary"].findData("#F2CC8F"))
    assert previews.roles().primary == "#F2CC8F" and previews.auto_button.isEnabled()
    for kind in ("web", "app", "game", "logo", "poster"):
        previews.set_kind(kind)
        assert previews.canvas._roles.primary == "#F2CC8F" and previews.canvas._roles.background == "#81B29A"
        assert not previews.canvas.grab().isNull()
    studio.list.clearSelection()
    studio.list.item(studio.colors().index("#F2CC8F")).setSelected(True)
    studio.remove_selected()  # a role whose color was deleted goes back to automatic, on both tabs
    assert "Primary" not in previews.overrides() and previews.roles().primary != "#F2CC8F"
    assert "Primary" not in project.overrides() and project.roles().primary != "#F2CC8F"
    assert "#F2CC8F" not in project.palette_colors()
    previews.clear_overrides()
    project.clear_overrides()
    assert previews.overrides() == {} and not previews.auto_button.isEnabled() and project.overrides() == {}


def test_large_pictures_are_shrunk_and_the_sample_can_be_used(app, window):
    project = window.project
    window.studio.add_colors(BRAND, quiet=True)
    big = np.zeros((900, 2600, 4), dtype=np.uint8)
    big[..., 0], big[..., 3] = 200, 255
    big[:, 1300:, 1] = 180
    project.set_upload(big, "big")
    height, width = project._layers.rgba.shape[:2]
    assert max(width, height) == 1100 and height == round(900 * 1100 / 2600)
    assert project.facts_label.text().startswith("2,600 × 900")  # the facts are about the real picture
    project.set_upload(None)
    assert not project.has_upload() and project.recolored() is None
    assert project.stack.currentWidget() is project.empty_page
    project.sample_button.click()  # "No file handy? Try the sample"
    assert project.has_upload() and "sample" in project.file_name()
    window._reset()  # emptying the Image tab leaves Your project alone
    assert project.has_upload()


# --- contrast copy and export ----------------------------------------------------------


def test_contrast_copy_and_export(app, window, tmp_path):
    studio = window.studio
    contrast = studio.contrast
    assert not contrast.copy_table_button.isEnabled()
    studio.add_colors(["#000000", "#FFFFFF", "#777777"], quiet=True)
    assert contrast.copy_table_button.isEnabled() and contrast.export_button.isEnabled()
    contrast.copy_table_button.click()
    rows = QGuiApplication.clipboard().text().splitlines()
    assert rows[0].startswith("Color\t") and rows[1].split("\t")[-2:] == ["21.00", "AAA"] and len(rows) == 4
    contrast.copy_image_button.click()
    image = QGuiApplication.clipboard().image()
    assert not image.isNull() and image.width() > 250
    assert image.pixelColor(2, 2).name().upper() == "#FFFEFA"  # light background even in the dark theme
    window.set_theme("dark")
    assert contrast.grid_image().pixelColor(2, 2).name().upper() == "#FFFEFA"
    window.set_theme("light")
    for kind, (_, extension) in designer.CONTRAST_FORMATS.items():
        target = tmp_path / f"contrast{extension}"
        assert contrast.export_to(target, kind), kind
        assert target.stat().st_size > 60
    assert (tmp_path / "contrast.csv").read_text(encoding="utf-8").splitlines()[0] == "Color,Name,With,Name,Contrast,WCAG level"
    assert Image.open(tmp_path / "contrast.png").size[0] > 250
    ET.parse(tmp_path / "contrast.svg")
    assert not contrast.export_to(tmp_path / "nowhere" / "x.csv", "csv") and window.status_label.property("error")


# --- library ---------------------------------------------------------------------------


def test_saving_to_the_library_and_opening_it_again(app, window):
    studio, page = window.studio, window.library_page
    assert window.tab_bar.count() == 4 and window.tab_bar.tabText(TAB_LIBRARY) == "Library"
    assert studio.save_to_library() is None  # nothing to save yet
    studio.add_colors(["#B47A3C", "#1E2A3B"], quiet=True)
    studio.name_color(0, "Clay")
    studio.rename_palette("Autumn brand")
    first = studio.save_to_library()
    second = studio.save_to_library(favorite=True)
    assert (first.palette.name, second.palette.name) == ("Autumn brand", "Autumn brand 2")
    assert window.tab_bar.tabText(TAB_LIBRARY) == "Library (2)"
    studio.clear_colors()  # the saved palette is a copy
    assert first.palette.hexes() == ["#B47A3C", "#1E2A3B"] and first.palette.colors[0].name == "Clay"
    labels = [action.text() for action in studio.presets_menu.actions()]
    assert "FAVORITES" in labels and "★ Autumn brand 2   (2)" in labels and "Autumn brand   (2)" in labels

    window.show_tab(TAB_LIBRARY)
    settle(app)
    assert not window.grab().isNull()
    assert page.tree.topLevelItemCount() == 1 and page.tree.topLevelItem(0).childCount() == 2
    page.select_entry(first)
    assert page.detail.title.text() == "Autumn brand" and page.codes.text() == "#B47A3C, #1E2A3B"
    page.copy_button.click()
    assert QGuiApplication.clipboard().text() == "#B47A3C, #1E2A3B"
    page.grid._buttons[1].click()  # a swatch copies its own code
    assert QGuiApplication.clipboard().text() == "#1E2A3B"
    studio.add_colors(["#FFFFFF"], quiet=True)
    page.open_button.click()
    assert window.pages.currentIndex() == TAB_PALETTE and studio.colors() == ["#B47A3C", "#1E2A3B"]
    assert studio.palette.name.startswith("Autumn brand") and studio.project.palettes[0].hexes() == ["#FFFFFF"]


def test_library_folders_favorites_search_and_delete(app, window):
    studio, page = window.studio, window.library_page
    studio.add_colors(["#111111", "#EEEEEE"], quiet=True)
    one = studio.save_to_library("Mono")
    studio.add_colors(["#E0452F"], quiet=True)
    two = studio.save_to_library("Signal")
    three = studio.save_to_library("Third")
    window.show_tab(TAB_LIBRARY)
    logos = page.new_folder("Logos")
    web = page.new_folder("Web projects")
    assert page.library.folders == ["Logos", "Web projects"] and page.detail.title.text() == "Web projects"
    assert page.rename_button.text() == "Rename folder…" and page.delete_button.text() == "Delete folder"
    page.move_entries([one, two], logos)
    assert one.folder == two.folder == "Logos" and three.folder == ""
    assert [page.tree.topLevelItem(i).text(0) for i in range(3)] == ["Logos   (2)", "Web projects   (0)", "Not in a folder   (1)"]
    page.select_entry(two)
    page.favorite_button.click()
    assert two.favorite and page.favorite_button.text() == "★ Favorite"
    assert any("★ Signal" in page.tree.topLevelItem(0).child(i).text(0) for i in range(2))
    page.favorites_button.setChecked(True)
    assert page.shown_entries() == [two] and page.tree.topLevelItemCount() == 1
    page.favorites_button.setChecked(False)
    page.search.setText("e0452")
    assert set(page.shown_entries()) == {two, three}
    page.search.setText("")
    page.select_entry(two)
    page.rename("Signal red")
    assert two.palette.name == "Signal red" and page.detail.title.text() == "Signal red"
    page.duplicate_button.click()
    assert [e.palette.name for e in page.library.in_folder("Logos")] == ["Mono", "Signal red", "Signal red copy"]
    page.select_folder(logos)
    page.rename("Brand marks")
    assert one.folder == "Brand marks" and page.library.folders[0] == "Brand marks"
    page.select_folder("Brand marks")
    page.delete_button.click()  # deleting a folder keeps its palettes
    assert "Brand marks" not in page.library.folders and len(page.library.entries) == 4 and one.folder == ""
    page.select_entry(three)
    page.delete_button.click()
    assert three not in page.library.entries and window.tab_bar.tabText(TAB_LIBRARY) == "Library (3)"
    assert not window.grab().isNull()


def test_the_library_survives_closing_the_app_and_old_presets_come_along(app, tmp_path):
    QSettings.setPath(QSettings.Format.IniFormat, QSettings.Scope.UserScope, str(tmp_path))  # a scratch settings folder
    try:
        first = MainWindow(persist=True)
        first.studio.add_colors(["#5A3CB0", "#FFD291"], quiet=True)
        folder = first.library_page.new_folder("Keepers")
        first.studio.save_to_library("Saved look", folder, favorite=True)
        first.close()
        settle(app)
        second = MainWindow(persist=True)
        entry = second.studio.library.entries[0]
        assert second.studio.library.folders == ["Keepers"]
        assert (entry.palette.name, entry.folder, entry.favorite) == ("Saved look", "Keepers", True)
        assert entry.palette.hexes() == ["#5A3CB0", "#FFD291"]
        assert second.library_page.library is second.studio.library and second.tab_bar.tabText(TAB_LIBRARY) == "Library (1)"
        second.close()
        settle(app)
        # A settings file from version 2.1, which called them presets:
        old = designer.Project([NamedPalette("Old preset", [PaletteColor("#123456")])]).to_json()
        settings = QSettings(QSettings.Format.IniFormat, QSettings.Scope.UserScope, "ColorroomFABLE", "ColorroomFABLE")
        settings.remove("studio/library")
        settings.setValue("studio/presets", old)
        settings.sync()
        third = MainWindow(persist=True)
        assert [e.palette.name for e in third.studio.library.entries] == ["Old preset"]
        third.close()
        settle(app)
    finally:
        apply_theme(app, "light")


# --- dark mode button ------------------------------------------------------------------


def test_dark_mode_button(app, window):
    assert window.theme_button.text() == "Dark mode" and theme.MODE == "light"
    window.theme_button.click()
    settle(app)
    assert theme.MODE == "dark" and window.theme_button.text() == "Light mode" and window.theme_actions["dark"].isChecked()
    for tab in range(4):
        window.show_tab(tab)
        settle(app)
        assert window.grab().toImage().pixelColor(4, 300).lightness() < 60
    window.theme_button.click()
    assert theme.MODE == "light" and window.theme_button.text() == "Dark mode"


def test_favorite_button_in_palette_tools(app, window):
    studio, page = window.studio, window.library_page
    star = studio.favorite_button
    assert not star.isEnabled() and star.text() == "☆ Favorite"  # nothing to favorite yet
    studio.add_colors(["#B47A3C", "#1E2A3B"], quiet=True)
    studio.rename_palette("Autumn brand")
    assert star.isEnabled() and not star.isChecked()
    star.click()  # not in the library yet: saved there as a favorite
    assert len(studio.library.entries) == 1
    entry = studio.library.entries[0]
    assert (entry.palette.name, entry.favorite, entry.palette.hexes()) == ("Autumn brand", True, ["#B47A3C", "#1E2A3B"])
    assert star.isChecked() and star.text() == "★ Favorite" and "Autumn brand" in star.toolTip()
    assert window.tab_bar.tabText(TAB_LIBRARY) == "Library (1)" and page.library.favorites() == [entry]
    star.click()  # clicking again removes the star, it does not save a second copy
    assert len(studio.library.entries) == 1 and not entry.favorite and star.text() == "☆ Favorite"
    assert "Removed from favorites" in window.toast.text()
    star.click()
    assert entry.favorite and len(studio.library.entries) == 1
    studio.add_colors(["#FFFFFF"], quiet=True)  # a changed palette is a different palette
    assert not star.isChecked() and star.text() == "☆ Favorite"
    star.click()
    assert len(studio.library.entries) == 2 and studio.library.entries[1].palette.name == "Autumn brand 2"
    page.select_entry(studio.library.entries[1])
    page.favorite_button.click()  # un-starring on the Library tab reaches the button here
    assert not star.isChecked() and star.text() == "☆ Favorite"
    studio.clear_colors()
    assert not star.isEnabled() and not star.isChecked()
    studio.undo()
    assert star.isEnabled()
