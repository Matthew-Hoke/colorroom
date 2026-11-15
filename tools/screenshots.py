"""Render the app off-screen and save screenshots to docs/screenshots.

Run from the project folder:   .venv\\Scripts\\python tools\\screenshots.py

No window appears. It is a quick way to check the look after a change, and it
exercises most of the interface (Your project, open, palette styles, picking, inspecting,
filtering). The window opens on Your project, so 01-empty is that tab's empty state.
"""

from __future__ import annotations

import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "tests"))

from PySide6.QtCore import QPointF, Qt  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from colorroom.ui.main_window import TAB_IMAGE, TAB_LIBRARY, TAB_PALETTE, TAB_PROJECT, MainWindow  # noqa: E402
from colorroom.ui.theme import apply_theme  # noqa: E402

OUT = ROOT / "docs" / "screenshots"


def main() -> int:
    OUT.mkdir(parents=True, exist_ok=True)
    app = QApplication(sys.argv[:1])
    apply_theme(app)
    window = MainWindow(persist=False)
    window.setAttribute(Qt.WidgetAttribute.WA_DontShowOnScreen, True)
    window.resize(1460, 940)
    window.show()

    def settle(seconds: float = 0.15) -> None:
        deadline = time.monotonic() + seconds
        while time.monotonic() < deadline:
            app.processEvents()
            time.sleep(0.01)

    def wait_for(condition, timeout: float = 60) -> None:
        deadline = time.monotonic() + timeout
        while not condition():
            app.processEvents()
            time.sleep(0.01)
            if time.monotonic() > deadline:
                raise TimeoutError("The app did not get ready in time.")

    def shot(name: str) -> None:
        settle()
        window.grab().save(str(OUT / f"{name}.png"))
        print(f"saved {name}.png")

    shot("01-empty")

    window.open_sample()
    wait_for(lambda: window.index is not None)
    shot("02-sample-balanced")

    # Inspect the third swatch of the balanced palette.
    entry = window.palette.entries[2]
    window._swatch_clicked(entry)
    shot("03-inspect-swatch")

    # A color-wheel style built on a picked base.
    window.select_color(window.index.hex(int(window.index.variety_order[1])))
    window.select_color(window.index.hex(int(window.index.variety_order[3])))
    window.select_color(window.index.hex(int(window.index.variety_order[5])))
    shot("04-custom-brand")
    window.palette_panel.set_mode("triadic", notify=True)
    window._swatch_clicked(window.palette.entries[-1])
    shot("05-triadic")
    window.palette_panel.set_mode("seasonal", notify=True)
    window.palette_panel.theme.setCurrentIndex(window.palette_panel.theme.findData("Autumn"))
    window.palette_panel.size.setCurrentIndex(window.palette_panel.size.findData(8))
    shot("06-seasonal-autumn-8")
    window.palette_panel.set_mode("monochromatic", notify=True)
    window.palette_panel.size.setCurrentIndex(window.palette_panel.size.findData(12))
    shot("07-monochromatic-12")

    # Filters: most frequent, HEX prefix, tone filter and a tight shade range.
    window.sort_combo.setCurrentIndex(window.sort_combo.findData("frequency"))
    window.filter_edit.setText("#F")
    window.inspect(int(window.index.frequency_order[0]))
    window.inspector.range_slider.setValue(80)
    shot("08-filters-and-range")

    # The eyedropper loupe.
    window.clear_filters()
    window.clear_picks()
    window.palette_panel.set_mode("balanced", notify=True)
    window.pick_button.setChecked(True)
    rect = window.image_view.image_rect()
    window.image_view._hover = QPointF(rect.x() + rect.width() * 0.69, rect.y() + rect.height() * 0.38)
    window.image_view.update()
    shot("09-eyedropper")
    window.pick_button.setChecked(False)

    for name in ("blobs", "gradient", "tiny"):
        window.open_path(str(ROOT / "tests" / "fixtures" / "images" / f"{name}.png"))
        wait_for(lambda: window.index is not None)
        window.inspect(int(window.index.variety_order[0]))
        shot(f"10-{name}")

    window.resize(1100, 720)
    shot("11-small-window")
    window.resize(1460, 940)

    # Several rows selected in the table, the highlight overlay and a zoomed image.
    window.open_sample()
    wait_for(lambda: window.index is not None)
    window.table.selectRow(0)
    selection = window.table.selectionModel()
    for row in (1, 3, 4):
        selection.select(
            window.table.inventory.index(row, 0),
            selection.SelectionFlag.Select | selection.SelectionFlag.Rows,
        )
    window.inspect(int(window.index.frequency_order[1]), reveal=False)
    window.highlight_button.setChecked(True)
    shot("13-multi-select-and-highlight")
    window.highlight_button.setChecked(False)
    window.image_view.set_zoom(4.0, window.image_view.rect().center().toPointF())
    shot("14-zoomed-in")
    window.image_view.reset_zoom()

    # The My palette tab and its five tools.
    window.add_selected_to_studio()
    window.add_palette_to_studio()
    studio = window.studio
    studio.rename_palette("Mesa Valley brand")
    for position, name in enumerate(("Night", "Dusk violet", "Ridge", "Sun")):
        if position < len(studio.palette.colors):
            studio.name_color(position, name)
    studio.toggle_favorite()
    studio.list.clearSelection()
    studio.list.item(4).setSelected(True)
    studio.list.item(6).setSelected(True)
    window.show_tab(TAB_PALETTE)
    studio.mockups.set_kind("web")
    pages = (studio.contrast, studio.vision, studio.ramp, studio.mockups, studio.values)
    for position, name in enumerate(("contrast", "color-vision", "tints-and-shades", "previews", "values")):
        studio.tabs.setCurrentWidget(pages[position])
        shot(f"{15 + position}-my-palette-{name}")

    studio.tabs.setCurrentWidget(studio.mockups)
    for position, kind in enumerate(("app", "game", "logo", "poster", "gradient")):
        studio.mockups.set_kind(kind)
        shot(f"{22 + position}-preview-{kind}")
    studio.mockups.set_kind("game")

    # The library: folders, favorites and a selected palette.
    page = window.library_page
    brands = page.new_folder("Brand identities")
    page.new_folder("Game art")
    page.new_folder("Web projects")
    kept = studio.save_to_library("Mesa Valley brand", brands, favorite=True)
    for name, folder, colors in (
        ("Coastal morning", brands, ["#0B132B", "#1C2541", "#3A506B", "#5BC0BE", "#F4F1DE"]),
        ("Arcade night", "Game art", ["#10002B", "#3C096C", "#7B2CBF", "#FF6D00", "#FFD60A"]),
        ("Forest quest", "Game art", ["#081C15", "#2D6A4F", "#74C69D", "#D8F3DC", "#E9C46A"]),
        ("Editorial", "Web projects", ["#FAF9F6", "#1B1B1F", "#C8553D", "#588B8B"]),
        ("Scratch ideas", "", ["#264653", "#2A9D8F", "#E9C46A", "#F4A261", "#E76F51"]),
    ):
        studio.new_palette(name)
        studio.add_colors(colors, quiet=True)
        studio.save_to_library(name, folder, favorite=name == "Arcade night")
        studio.delete_palette()
    window.show_tab(TAB_LIBRARY)
    page.select_entry(kept)
    shot("28-library")

    # Your project: a layered Photoshop file, recolored, then exported.
    import tempfile

    import numpy as np
    from PIL import Image
    from psd_tools import PSDImage
    from test_photos_and_psd import PHOTO_BOX, web_page

    from colorroom.core import file_export
    from colorroom.core import project as rules

    project = window.project
    work = tempfile.TemporaryDirectory()
    picture = web_page()
    psd = PSDImage.new("RGB", (picture.shape[1], picture.shape[0]), color=(255, 255, 255))
    psd.append(psd.create_pixel_layer(Image.fromarray(picture[..., :3], "RGB"), name="Page"))
    top, bottom = round(PHOTO_BOX[1] * picture.shape[0]), round(PHOTO_BOX[3] * picture.shape[0])
    psd.append(psd.create_pixel_layer(Image.fromarray(picture[top:bottom, :, :3], "RGB"), name="Hero photo", top=top, left=0))
    file_export._set_psd_dpi(psd, 300)
    poster = Path(work.name) / "Mesa Valley poster.psd"
    psd.save(str(poster))
    window.show_tab(TAB_PROJECT)
    window.toast.hide()
    project.load_upload(poster)  # My palette has colors, so the file starts on it
    project.choose_palette("idea:triadic")  # the starter palette, made from the file
    shot("32-your-project-file")
    project.export_file(Path(work.name) / "Mesa Valley poster - Colorroom.psd")
    shot("33-your-project-exported")
    project.hide_report()
    project.choose_palette(rules.library_key(kept))  # a Library favorite, roles set by hand
    project.roles_toggle.setChecked(True)
    project.set_role("Surface", "#211D38")
    shot("27-your-project-roles")
    project.roles_toggle.setChecked(False)

    # A web page picture with a photo: the photo is left out of the recoloring.
    project.set_upload(np.ascontiguousarray(picture), "a web page")
    shot("31-your-project-photos-left-out")
    project.load_upload(poster)

    # Small screens: 1280 × 720 with the taskbar and the title bar is about 1280 × 640.
    window.resize(1280, 640)
    shot("34-your-project-1280x640")
    window.resize(1460, 940)
    window.show_tab(TAB_PALETTE)

    # A multi-page document: the page strip and the whole-document view.
    from make_pdf import BRAND_GUIDE, write_pdf

    with tempfile.TemporaryDirectory() as folder:
        guide = write_pdf(Path(folder) / "Brand guide.pdf", BRAND_GUIDE * 2)
        window.open_path(str(guide))
        wait_for(lambda: window.index is not None and not window.is_busy())
        window.show_page(2)
        wait_for(lambda: window.index is not None and not window.is_busy())
        window.inspect(int(window.index.frequency_order[1]))
        shot("29-document-pages")
        window.show_whole_document()
        settle(0.3)
        window._document_dialog.grab().save(str(OUT / "30-whole-document.png"))
        print("saved 30-whole-document.png")
        window._document_dialog.hide()
        window.open_sample()
        wait_for(lambda: window.index is not None and not window.is_busy())
    window.show_tab(TAB_PALETTE)

    # The dark theme, on every main tab.
    window.set_theme("dark")
    studio.tabs.setCurrentWidget(studio.mockups)
    shot("20-dark-my-palette")
    window.show_tab(TAB_IMAGE)
    shot("21-dark-image")
    window.show_tab(TAB_PROJECT)
    shot("35-dark-your-project")
    window.set_theme("light")
    window.close()
    work.cleanup()
    return 0


if __name__ == "__main__":
    sys.exit(main())
