"""Drive the real window without showing it, to catch wiring mistakes in the interface."""

from __future__ import annotations

import os
import time

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")  # no window appears while testing

pytest.importorskip("PySide6")

from PySide6.QtGui import QGuiApplication, QImage  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from colorroom.core import palettes  # noqa: E402
from colorroom.ui.image_view import qimage_to_rgba, rgba_to_qimage  # noqa: E402
from colorroom.ui.inventory import HSL, RGB, format_share  # noqa: E402
from colorroom.ui.main_window import MainWindow  # noqa: E402
from colorroom.ui.theme import apply_theme  # noqa: E402

from conftest import FIXTURES, load_rgba  # noqa: E402


@pytest.fixture(scope="module")
def app():
    application = QApplication.instance() or QApplication([])
    apply_theme(application)
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
    settle(app)


def test_sample_loads_and_fills_every_panel(app, window):
    assert window.index.distinct == 649
    assert window.windowTitle().startswith("Dusk at Mesa Valley")
    assert "840 × 390" in window.source_panel.subtitle.text()
    assert window.palette.mode == "balanced" and len(window.palette.entries) == 6
    assert window.table.inventory.rowCount() == 649
    assert window.limit_spin.value() == 649
    assert window.detail_slider.value() == window.detail_slider.maximum()
    assert "649 distinct shades" in window.inventory_panel.subtitle.text()
    assert not window.status_label.property("error")
    assert window.export_csv_button.isEnabled() and window.palette_panel.copy_button.isEnabled()


def test_picking_colors_builds_a_brand_palette(app, window):
    colors = [window.index.hex(int(position)) for position in window.index.variety_order[:3]]
    for color in colors:
        window.select_color(color)
    assert window.palette_panel.current_mode() == "custom"  # picking leaves the image styles
    assert window.palette.colors == colors
    assert [entry.role for entry in window.palette.entries] == list(palettes.BRAND_ROLES)
    assert window.table.inventory.is_picked(0)
    window.select_color(colors[0], toggle=True)
    assert window.picks == colors[1:]
    window.select_color(colors[1])  # already picked: nothing changes, a note is shown
    assert window.picks == colors[1:]
    assert window.toast.isVisible() and "already selected" in window.toast.text()
    window.clear_picks()
    assert window.picks == [] and window.palette.entries == ()


def test_pick_limit(app, window):
    for position in window.index.variety_order[: palettes.MAX_PICKS + 2]:
        window.select_color(window.index.hex(int(position)))
    assert len(window.picks) == palettes.MAX_PICKS


@pytest.mark.parametrize("mode", palettes.MODES)
def test_every_style_can_be_shown(app, window, mode):
    window.select_color(window.index.base_color)
    window.palette_panel.set_mode(mode, notify=True)
    assert window.palette.mode == mode
    assert len(window.palette_panel.grid._buttons) == len(window.palette.entries) >= 1
    assert window.palette_panel.size.isVisibleTo(window) == palettes.uses_size(mode)
    assert window.palette_panel.theme.isVisibleTo(window) == (mode == "seasonal")


def test_inspecting_a_swatch_marks_the_image_and_selects_the_row(app, window):
    window.palette_panel.set_mode("triadic", notify=True)
    entry = window.palette.entries[-1]
    assert not entry.exact  # a suggested color that the image does not contain
    window._swatch_clicked(entry)
    assert window.inspected.color == entry.source_color
    assert window.inspected.palette_color == entry.color
    assert window.inspector.name.text() == f"{entry.color} → {entry.source_color}"
    assert window.inspector.copy_palette_button.isVisibleTo(window)
    assert window.readout.text() == f"Palette {entry.color} → nearby image pixel {entry.source_color}"
    row = window.table.currentIndex().row()
    assert window.table.inventory.color(row) == entry.source_color
    # Choosing a related shade keeps the link to the palette swatch.
    shade = window.inspector._related.entry(0)
    window._shade_chosen(shade.index)
    assert window.inspected.color == shade.color and window.inspected.palette_color == entry.color
    # Changing style removes the swatch; the inspected color stays but loses the link.
    window.palette_panel.set_mode("balanced", notify=True)
    assert window.inspected.color == shade.color and window.inspected.palette_color is None


def test_hovering_moves_the_highlight(app, window):
    entry = window.palette.entries[1]
    window._swatch_hovered(entry)
    assert window.highlighted.color == entry.source_color
    assert window.image_view._highlighted.color == entry.source_color
    window._swatch_hovered(None)
    assert window.highlighted is None
    window._row_hovered(5)
    assert window.readout.text().startswith(f"Image pixel {window.index.hex(5)}")
    window._row_hovered(-1)
    assert window.highlighted is None and window.readout.text() == ""


def test_filters_narrow_the_table(app, window):
    model = window.table.inventory
    window.filter_edit.setText("#F")
    assert 0 < model.rowCount() < 649
    assert all(model.color(row).startswith("#F") for row in range(model.rowCount()))
    assert "shades match" in window.inventory_panel.subtitle.text()
    window.filter_edit.setText("#G")
    assert model.rowCount() == 0 and window.filter_edit.property("invalid")
    window.filter_edit.setText("")
    assert model.rowCount() == 649 and not window.filter_edit.property("invalid")

    window.sort_combo.setCurrentIndex(window.sort_combo.findData("frequency"))
    assert model.color(0) == window.index.base_color
    window.sort_combo.setCurrentIndex(window.sort_combo.findData("hex"))
    assert [model.color(row) for row in range(3)] == sorted(model.color(row) for row in range(3))

    window.detail_slider.setValue(5)
    limit = window.detail_steps[5]
    assert window.limit_spin.value() == limit and model.rowCount() == limit
    window.limit_spin.setValue(100)
    assert model.rowCount() == 100
    nearest_stop = min(window.detail_steps, key=lambda stop: abs(stop - 100))
    assert window.detail_steps[window.detail_slider.value()] == nearest_stop
    window.clear_filters()
    assert model.rowCount() == 649 and window.limit_spin.value() == 649


def test_eyedropper_respects_the_shade_limit(app, window):
    window.pick_button.setChecked(True)
    assert window.picking and window.pick_action.isChecked()
    window._pixel_clicked(0.69, 0.37)  # the sun
    assert window.inspected.color == "#FFD291" and window.picks == ["#FFD291"]
    assert window.inspected.point.x == pytest.approx(0.69, abs=0.01)
    window.limit_spin.setValue(1)
    window._pixel_clicked(0.01, 0.01)  # a sky color outside a one-color list
    assert len(window.picks) == 1 and "outside the current list" in window.toast.text()
    window.pick_button.setChecked(False)
    assert not window.picking


def test_inspector_filters(app, window):
    window.inspect(int(window.index.frequency_order[0]))
    inspector = window.inspector
    total = len(inspector._related)
    assert total > inspector.PAGE_SIZE
    assert inspector.shades.flow.count() == inspector.PAGE_SIZE
    assert inspector.next_button.isVisibleTo(window) and not inspector.previous_button.isVisibleTo(window)
    inspector._turn(1)
    assert inspector.previous_button.isVisibleTo(window)
    inspector.range_slider.setValue(10)  # 1%
    assert inspector.maximum == 1.0
    assert inspector._related.count_within(1.0) < total
    tone = inspector._tone_chips[1][0]
    inspector._set_tone(tone)
    assert inspector._tone == tone and inspector._tone_chips[1][1].isChecked()
    copied = []
    inspector.copyRequested.connect(lambda text, message: copied.append((text, message)))
    inspector.range_slider.setValue(1000)
    inspector._copy_shades()
    assert copied and copied[0][0].count("#") == inspector._related.select(tone).size


def test_copy_and_export(app, window, tmp_path, monkeypatch):
    window.copy_palette()
    assert QGuiApplication.clipboard().text() == ", ".join(window.palette.colors)
    from PySide6.QtWidgets import QFileDialog

    target = tmp_path / "my palette"
    monkeypatch.setattr(QFileDialog, "getSaveFileName", lambda *args, **kwargs: (str(target), args[4]))
    window.export_palette("ase")
    assert target.with_suffix(".ase").read_bytes()[:4] == b"ASEF"
    csv_target = tmp_path / "shades.csv"
    monkeypatch.setattr(QFileDialog, "getSaveFileName", lambda *args, **kwargs: (str(csv_target), ""))
    window.export_csv()
    wait_for(app, lambda: not window.is_busy())
    assert csv_target.read_text().count("\n") == 650


def test_opening_files_and_errors(app, window, tmp_path):
    window.open_path(str(FIXTURES / "images" / "blobs.png"))
    wait_for(app, lambda: window.index is not None and not window.is_busy())
    assert window.index.distinct == 17823 and window.file_label.text() == "blobs.png"
    assert window.picks == [] and window.inspected is None

    bad = tmp_path / "broken.png"
    bad.write_text("not an image")
    window.open_path(str(bad))
    wait_for(app, lambda: not window.is_busy())
    assert window.index is None and window.status_label.property("error")
    assert "could not be decoded" in window.status_label.text()
    assert window.table.inventory.rowCount() == 0 and not window.export_csv_button.isEnabled()


def test_paste_from_clipboard(app, window):
    image = rgba_to_qimage(load_rgba("tiny"))
    QGuiApplication.clipboard().setImage(image)
    window.paste_image()
    wait_for(app, lambda: window.index is not None and not window.is_busy() and window.index.distinct == 7)
    assert window.file_label.text() == "Pasted image"


def test_table_hides_columns_when_narrow(app, window):
    table = window.table
    assert not table.isColumnHidden(HSL)
    window.resize(1020, 700)
    settle(app, 0.1)
    assert table.isColumnHidden(HSL) or table.viewport().width() >= 694
    window.resize(1700, 1000)
    settle(app, 0.1)
    assert not table.isColumnHidden(HSL) and not table.isColumnHidden(RGB)
    widths = sum(table.columnWidth(column) for column in range(7))
    assert widths == table.viewport().width()  # no sideways scrolling


def test_image_conversion_round_trip():
    rgba = load_rgba("blobs")
    image = rgba_to_qimage(rgba)
    assert isinstance(image, QImage) and (image.width(), image.height()) == (220, 160)
    back = qimage_to_rgba(image)
    assert back.shape == rgba.shape and (back == rgba).all()


def test_format_share():
    assert format_share(0.13016) == "13.016%"
    assert format_share(0.5) == "50%"
    assert format_share(0.000001) == "<0.001%"
