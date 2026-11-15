"""Drive the My palette tab, multi-select, zoom, highlight and the dark theme off-screen."""

from __future__ import annotations

import json
import os
import time
import xml.etree.ElementTree as ET

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")  # no window appears while testing

pytest.importorskip("PySide6")

from PySide6.QtCore import QItemSelectionModel, QPointF  # noqa: E402
from PySide6.QtGui import QGuiApplication  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from colorroom.core import designer  # noqa: E402
from colorroom.ui import theme  # noqa: E402
from colorroom.ui.main_window import TAB_PALETTE, TAB_PROJECT, MainWindow  # noqa: E402
from colorroom.ui.theme import apply_theme  # noqa: E402


@pytest.fixture(scope="module")
def app():
    application = QApplication.instance() or QApplication([])
    apply_theme(application, "light")
    return application


def wait_for(app, condition, timeout: float = 30) -> None:
    deadline = time.monotonic() + timeout
    while not condition():
        app.processEvents()
        time.sleep(0.005)
        assert time.monotonic() < deadline, "the window did not get ready in time"


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
    wait_for(app, lambda: window.index is not None and not window.is_busy())
    yield window
    window.close()
    apply_theme(app, "light")
    settle(app)


def select_rows(window, rows) -> None:
    table = window.table
    table.selectRow(rows[0])
    flags = QItemSelectionModel.SelectionFlag.Select | QItemSelectionModel.SelectionFlag.Rows
    for row in rows[1:]:
        table.selectionModel().select(table.inventory.index(row, 0), flags)


# --- getting colors into My palette ----------------------------------------------------


def test_starts_empty_with_tools_disabled(app, window):
    studio = window.studio
    assert studio.colors() == [] and window.tab_bar.tabText(TAB_PALETTE) == "My palette"
    assert not studio.copy_hex_button.isEnabled() and not studio.export_button.isEnabled()
    assert not window.add_selected_button.isEnabled()
    window.show_tab(TAB_PALETTE)
    settle(app)
    for position in range(studio.tabs.count()):  # every tool must cope with an empty palette
        studio.tabs.setCurrentIndex(position)
        settle(app)
        window.grab()
    assert studio.mockups.roles() is None


def test_multi_select_rows_are_added_together(app, window):
    select_rows(window, [0, 2, 5])
    expected = [window.table.inventory.color(row) for row in (0, 2, 5)]
    assert window.table.selected_colors() == expected
    assert window.add_selected_button.isEnabled() and "3 selected" in window.add_selected_button.text()
    window.add_selected_to_studio()
    assert window.studio.colors() == expected
    assert window.tab_bar.tabText(TAB_PALETTE) == "My palette (3)"
    window.add_selected_to_studio()  # adding again changes nothing
    assert window.studio.colors() == expected
    assert "already there" in window.toast.text()


def test_inspecting_does_not_break_a_multi_selection(app, window):
    select_rows(window, [1, 3, 4])
    distinct = window.table.inventory.distinct_index(3)
    window.inspect(distinct, reveal=False)
    assert window.table.selected_rows() == [1, 3, 4]
    assert window.inspected.index == distinct
    other = window.table.inventory.distinct_index(10)
    window.inspect(other)  # a color outside the selection replaces it
    assert window.table.selected_rows() == [10]


def test_design_palette_is_added_and_capped(app, window):
    window.add_palette_to_studio()
    assert window.studio.colors() == list(window.palette.colors)
    window.table.selectAll()
    window.add_selected_to_studio()
    assert len(window.studio.colors()) == designer.MAX_COLORS
    assert "left out" in window.toast.text()


def test_typing_and_pasting_hex_codes(app, window):
    studio = window.studio
    assert studio.add_text("brand #b47a3c and 1E2A3B, also #fff") == 3
    assert studio.colors() == ["#B47A3C", "#1E2A3B", "#FFFFFF"]
    QGuiApplication.clipboard().setText("#101010 #202020")
    studio.paste_colors()
    assert studio.colors()[-2:] == ["#101010", "#202020"]
    assert studio.add_text("no codes here") == 0 and "No HEX codes" in window.toast.text()


# --- editing ---------------------------------------------------------------------------


def test_name_lock_remove_and_sort(app, window):
    studio = window.studio
    studio.add_colors(["#FF0000", "#00FF00", "#0000FF", "#FFFFFF"], quiet=True)
    studio.name_color(0, "  Signal red ")
    assert studio.palette.colors[0].name == "Signal red"
    studio.list.clearSelection()
    studio.list.item(0).setSelected(True)
    studio.list.item(1).setSelected(True)
    assert studio.remove_button.isEnabled() and not studio.name_button.isEnabled()
    studio.list.item(1).setSelected(False)
    studio.toggle_lock()
    assert studio.palette.colors[0].locked and studio.lock_button.text() == "Unlock"
    studio.list.selectAll()
    studio.remove_selected()
    assert studio.colors() == ["#FF0000"] and "locked" in window.toast.text()
    studio.add_colors(["#FFFFFF", "#000000"], quiet=True)
    studio.sort_colors("lightness")
    assert studio.colors() == ["#FFFFFF", "#FF0000", "#000000"]
    studio.clear_colors()
    assert studio.colors() == ["#FF0000"]


def test_several_palettes(app, window):
    studio = window.studio
    studio.add_colors(["#111111", "#EEEEEE"], quiet=True)
    studio.rename_palette("Brand")
    assert studio.panel.title.text() == "Brand" and not studio.palette_combo.isVisible()
    studio.duplicate_palette()
    assert studio.palette.name == "Brand copy" and studio.colors() == ["#111111", "#EEEEEE"]
    studio.new_palette()
    assert studio.colors() == [] and len(studio.project.palettes) == 3
    studio.switch_palette(0)
    assert studio.palette.name == "Brand" and studio.palette_combo.count() == 3
    studio.delete_palette()
    assert [palette.name for palette in studio.project.palettes] == ["Brand copy", "Palette"]


def test_palette_file_round_trip(app, window, tmp_path):
    studio = window.studio
    studio.add_colors(["#B47A3C", "#1E2A3B"], quiet=True)
    studio.name_color(1, "Ink")
    assert studio.save_project(tmp_path / "work")
    saved = tmp_path / "work.colorroom"
    assert saved.exists()
    studio.delete_palette()
    assert studio.colors() == []
    assert studio.open_project(saved)
    assert studio.colors() == ["#B47A3C", "#1E2A3B"] and studio.palette.colors[1].name == "Ink"
    bad = tmp_path / "bad.colorroom"
    bad.write_text("not a palette")
    assert not studio.open_project(bad)
    assert window.status_label.property("error") and studio.colors() == ["#B47A3C", "#1E2A3B"]


# --- the tools -------------------------------------------------------------------------


def test_tools_follow_the_palette(app, window):
    studio = window.studio
    window.add_palette_to_studio()
    colors = studio.colors()
    window.show_tab(TAB_PALETTE)
    for position in range(studio.tabs.count()):
        studio.tabs.setCurrentIndex(position)
        settle(app)
        assert not window.grab().isNull()
    assert "pairs are readable as body text" in studio.contrast.summary.text()
    assert "Strongest pair: #211D38 with #FFD291" in studio.contrast.best.text()
    assert studio.ramp.base.count() == len(colors)
    studio.ramp.base.setCurrentIndex(1)
    assert colors[1] in [value for _, value in studio.ramp._ramp]
    assert studio.values.table.rowCount() == len(colors)
    assert studio.values.table.item(0, 2).text() == colors[0]
    assert studio.values.table.item(0, 5).text() == designer.cmyk_text(colors[0])
    roles = studio.mockups.roles()
    studio.mockups.next_variant()
    assert studio.mockups.roles().background != roles.background
    assert not studio.vision.picture.pixmap().isNull()  # the image preview follows the open image
    studio.vision.kind.setCurrentIndex(4)
    preview = studio.vision.picture.pixmap().toImage().pixelColor(5, 5)
    assert preview.red() == preview.green() == preview.blue()  # grayscale


def test_ramp_can_be_copied_and_saved_as_a_palette(app, window):
    studio = window.studio
    studio.add_colors(["#5A3CB0"], quiet=True)
    studio.name_color(0, "Brand violet")
    studio.ramp.css_button.click()
    css = QGuiApplication.clipboard().text()
    assert "--brand-violet-500:" in css and css.count("--brand-violet-") == 11
    studio.ramp.tailwind_button.click()
    assert QGuiApplication.clipboard().text().startswith("brand-violet: {")
    studio.ramp.palette_button.click()
    assert studio.palette.name == "brand-violet scale" and len(studio.colors()) == 11
    assert "#5A3CB0" in studio.colors()


# --- copy and export -------------------------------------------------------------------


def test_copy_as_text_swatches_and_image(app, window):
    studio = window.studio
    studio.add_colors(["#B47A3C", "#1E2A3B"], quiet=True)
    studio.name_color(0, "Clay")
    clipboard = QGuiApplication.clipboard()
    studio.copy_hex()
    assert clipboard.text() == "#B47A3C, #1E2A3B"
    studio.copy_swatches()
    mime = clipboard.mimeData()
    assert mime.hasFormat("image/svg+xml") and mime.hasText()
    root = ET.fromstring(bytes(mime.data("image/svg+xml")).decode("utf-8"))
    fills = [rect.get("fill") for rect in root.iter("{http://www.w3.org/2000/svg}rect")]
    assert fills == ["#B47A3C", "#1E2A3B"]
    studio.copy_image()
    image = clipboard.image()
    assert not image.isNull() and image.width() > image.height()
    assert image.pixelColor(100, 100).name().upper() == "#B47A3C"


def test_export_every_format(app, window, tmp_path):
    studio = window.studio
    studio.add_colors(["#B47A3C", "#1E2A3B", "#FFFFFF"], quiet=True)
    studio.rename_palette("Brand kit")
    studio.name_color(0, "Clay")
    for kind, extension in (("css", ".css"), ("json", ".json"), ("ase", ".ase"), ("gpl", ".gpl"), ("png", ".png"), ("txt", ".txt"), ("svg", ".svg"), ("tokens", ".tokens.json")):
        target = tmp_path / f"brand{extension}"
        assert studio.export_to(target, kind), kind
        assert target.stat().st_size > 20
    tokens = json.loads((tmp_path / "brand.tokens.json").read_text(encoding="utf-8"))
    assert tokens["brand-kit"]["clay"]["$value"] == "#B47A3C"
    assert (tmp_path / "brand.ase").read_bytes().startswith(b"ASEF")
    assert json.loads((tmp_path / "brand.json").read_text(encoding="utf-8"))["name"] == "Brand kit"
    assert not studio.export_to(tmp_path / "missing-folder" / "x.svg", "svg")
    assert window.status_label.property("error")


# --- image view: zoom and highlight ----------------------------------------------------


def test_zoom_keeps_the_point_under_the_pointer(app, window):
    view = window.image_view
    settle(app, 0.2)  # let the freshly loaded window finish laying itself out
    assert view.zoom() == 1.0 and not window.fit_button.isEnabled()
    rect = view.image_rect()
    anchor = QPointF(rect.x() + rect.width() * 0.25, rect.y() + rect.height() * 0.6)
    view.set_zoom(4.0, anchor)
    zoomed = view.image_rect()
    assert zoomed.width() == pytest.approx(rect.width() * 4)
    assert (anchor.x() - zoomed.x()) / zoomed.width() == pytest.approx(0.25, abs=0.01)
    assert (anchor.y() - zoomed.y()) / zoomed.height() == pytest.approx(0.6, abs=0.01)
    assert window.fit_button.isEnabled() and "400%" in window.status_label.text()
    settle(app)
    assert not window.grab().isNull()
    view.set_zoom(1000)
    assert view.zoom() == 40.0
    window.fit_button.click()
    assert view.zoom() == 1.0 and view.image_rect() == rect and not window.fit_button.isEnabled()


def test_picking_still_works_when_zoomed(app, window):
    view = window.image_view
    view.set_zoom(5.0, QPointF(view.width() / 2, view.height() / 2))
    window.pick_button.setChecked(True)
    fraction = view._fraction_at(QPointF(view.width() / 2, view.height() / 2))
    assert fraction is not None
    window._pixel_clicked(*fraction)
    column, row = int(fraction[0] * window.index.width), int(fraction[1] * window.index.height)
    red, green, blue = (int(value) for value in window.index.rgba[row, column][:3])
    assert window.inspected.color == f"#{red:02X}{green:02X}{blue:02X}"


def test_highlight_shows_only_the_inspected_color(app, window):
    view = window.image_view
    distinct = int(window.index.frequency_order[0])
    window.inspect(distinct)
    window.highlight_button.setChecked(True)
    assert view.highlight() == window.index.hex(distinct)
    assert view._highlight_count == window.index.count(distinct)
    overlay = view._overlay
    clear = sum(1 for x in range(0, overlay.width(), 7) for y in range(0, overlay.height(), 7) if overlay.pixelColor(x, y).alpha() == 0)
    assert clear > 0
    other = int(window.index.frequency_order[1])
    window.inspect(other)  # the highlight follows the inspected color
    assert view.highlight() == window.index.hex(other)
    window.highlight_button.setChecked(False)
    assert view.highlight() is None and view._overlay is None
    window.open_sample()
    wait_for(app, lambda: window.index is not None and not window.is_busy())
    assert not window.highlight_button.isChecked() and view.zoom() == 1.0


# --- theme -----------------------------------------------------------------------------


def test_dark_theme_switches_and_switches_back(app, window):
    window.add_palette_to_studio()
    light = window.grab().toImage().pixelColor(4, 300)
    window.set_theme("dark")
    settle(app)
    assert theme.MODE == "dark" and window.theme_actions["dark"].isChecked()
    dark = window.grab().toImage().pixelColor(4, 300)
    assert dark.lightness() < 60 < light.lightness()
    window.show_tab(TAB_PALETTE)
    for position in range(window.studio.tabs.count()):
        window.studio.tabs.setCurrentIndex(position)
        settle(app)
        assert not window.grab().isNull()
    window.set_theme("light")
    settle(app)
    assert theme.MODE == "light" and theme.PAPER == theme.LIGHT["PAPER"]
    assert window.grab().toImage().pixelColor(4, 300).lightness() > 200
    assert all(token not in theme.style_sheet() for token in ("{DISABLED}", "{FADED}", "{SCROLL}"))


# --- no stray windows ------------------------------------------------------------------


def visible_windows(app) -> list:
    return [widget for widget in app.topLevelWidgets() if widget.isVisible()]


def test_dragging_sliders_never_opens_extra_windows(app, window):
    """Rebuilding the chips quickly must not let any of them pop up as its own window."""
    window.inspect(int(window.index.frequency_order[0]))
    settle(app)
    assert visible_windows(app) == [window]
    seen = 0
    for value in list(range(1000, 0, -37)) + list(range(0, 1000, 53)):
        window.inspector.range_slider.setValue(value)  # several changes between screen updates,
        window.inspector.range_slider.setValue(max(1, value - 9))  # as a fast drag produces
        app.processEvents()
        seen = max(seen, len(visible_windows(app)))
    for position in range(0, window.detail_slider.maximum(), 3):
        window.detail_slider.setValue(position)
        window.select_color(window.index.hex(int(window.index.variety_order[position % 5])), toggle=True)
        app.processEvents()
        seen = max(seen, len(visible_windows(app)))
    for distinct in window.index.frequency_order[:25]:
        window.inspect(int(distinct))
        window.inspect(int(distinct))
        app.processEvents()
        seen = max(seen, len(visible_windows(app)))
    assert seen == 1, f"{seen - 1} extra window(s) appeared"


# --- previews --------------------------------------------------------------------------


def test_every_preview_draws_for_any_palette_size(app, window):
    from colorroom.ui.studio import PREVIEWS

    studio = window.studio
    window.show_tab(TAB_PALETTE)
    studio.tabs.setCurrentWidget(studio.mockups)
    assert list(PREVIEWS) == ["web", "app", "game", "logo", "poster", "gradient"]  # your own file is on Your project
    assert studio.tabs.indexOf(studio.mockups) == 0
    link = studio.mockups.project_link  # ...and the first thing in the row goes there
    assert link.text() == "Try it on your own file: Your project" and link.isVisible()
    link.click()
    assert window.pages.currentIndex() == TAB_PROJECT
    window.show_tab(TAB_PALETTE)
    for colors in ([], ["#5A3CB0"], ["#111111", "#EEEEEE"], list(window.palette.colors)):
        studio.clear_colors()
        studio.add_colors(colors, quiet=True)
        for kind in PREVIEWS:
            studio.mockups.set_kind(kind)
            settle(app, 0.02)
            assert studio.mockups.kind() == kind and studio.mockups.kind_buttons[kind].isChecked()
            assert sum(button.isChecked() for button in studio.mockups.kind_buttons.values()) == 1
            assert not studio.mockups.canvas.grab().isNull()
    artwork = studio.mockups.canvas.artwork_rect()
    assert artwork.width() <= studio.mockups.canvas.width() + 1 and artwork.height() <= studio.mockups.canvas.height() + 1


def test_gradient_map_uses_the_open_image(app, window):
    studio = window.studio
    window.add_palette_to_studio()
    studio.mockups.set_kind("gradient")
    window.show_tab(TAB_PALETTE)
    studio.tabs.setCurrentWidget(studio.mockups)
    settle(app)
    canvas = studio.mockups.canvas
    assert canvas._rgba is not None and "your image" in studio.mockups.roles_label.text()
    canvas.grab()
    mapped = canvas._mapped
    allowed = {tuple(int(v) for v in row) for row in designer.gradient_lut(studio.colors())}
    for x, y in ((3, 3), (mapped.width() // 2, mapped.height() // 2), (mapped.width() - 4, mapped.height() - 4)):
        pixel = mapped.pixelColor(x, y)
        assert (pixel.red(), pixel.green(), pixel.blue()) in allowed
    assert not studio.mockups.swap_button.isEnabled()
    window._reset()  # no image open: a sample scene stands in
    assert canvas._rgba is None and "sample scene" in studio.mockups.roles_label.text()
    assert not canvas.grab().isNull()


# --- deleting colors -------------------------------------------------------------------


def test_tick_boxes_and_the_x_on_each_row(app, window):
    from PySide6.QtCore import QPoint

    studio = window.studio
    studio.add_colors(["#111111", "#222222", "#333333", "#444444"], quiet=True)
    window.show_tab(TAB_PALETTE)
    settle(app)
    rows = studio.list
    rows.clearSelection()
    rect = rows.visualRect(rows.model().index(1, 0))
    assert rows.zone_at(QPoint(rect.x() + 12, rect.center().y())) == (1, "check")
    assert rows.zone_at(QPoint(rect.right() - 12, rect.center().y())) == (1, "delete")
    assert rows.zone_at(QPoint(rect.center().x(), rect.center().y())) == (1, "body")
    rows.click_zone(0, "check")
    rows.click_zone(2, "check")  # ticking a second box keeps the first
    assert rows.selected_rows() == [0, 2] and studio.remove_button.text() == "Delete 2 selected"
    rows.click_zone(0, "check")
    assert rows.selected_rows() == [2] and studio.remove_button.text() == "Delete selected"
    rows.click_zone(1, "delete")  # the x deletes that one color, whatever is ticked
    assert studio.colors() == ["#111111", "#333333", "#444444"]
    studio.palette.colors[0].locked = True
    studio.refresh()
    rows.click_zone(0, "delete")  # a locked color has no x
    assert studio.colors() == ["#111111", "#333333", "#444444"]
    assert not window.grab().isNull()


def test_delete_selected_delete_all_and_undo(app, window):
    studio = window.studio
    colors = ["#111111", "#222222", "#333333", "#444444", "#555555"]
    studio.add_colors(colors, quiet=True)
    assert studio.clear_button.isEnabled() and not studio.undo_button.isEnabled()
    studio.list.clearSelection()
    assert not studio.remove_button.isEnabled()
    studio.toggle_select_all()
    assert studio.list.selected_rows() == [0, 1, 2, 3, 4] and studio.select_all_button.text() == "Select none"
    studio.toggle_select_all()
    assert studio.list.selected_rows() == []
    for row in (1, 3):
        studio.list.click_zone(row, "check")
    studio.remove_selected()
    assert studio.colors() == ["#111111", "#333333", "#555555"] and "2 colors deleted" in window.toast.text()
    studio.palette.colors[1].locked = True
    studio.clear_colors()  # Delete all keeps the locked one
    assert studio.colors() == ["#333333"] and "1 locked color kept" in window.toast.text()
    assert studio.undo_button.isEnabled()
    studio.undo()
    assert studio.colors() == ["#111111", "#333333", "#555555"]
    studio.undo()
    assert studio.colors() == colors
    assert not studio.undo_button.isEnabled()
    studio.sort_colors("lightness")
    studio.undo()
    assert studio.colors() == colors
    studio.clear_colors()
    assert studio.colors() == [] and not studio.clear_button.isEnabled()
    studio.clear_colors()  # nothing left: no crash, nothing to undo twice
    studio.undo()
    assert studio.colors() == colors
