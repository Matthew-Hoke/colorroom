"""Drive the Your project tab off-screen: open, try palettes, compare, export (made by Opus 5.5)."""

from __future__ import annotations

import os
import time

import numpy as np
import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")  # no window appears while testing

pytest.importorskip("PySide6")
pytest.importorskip("psd_tools")

from PIL import Image  # noqa: E402
from psd_tools import PSDImage  # noqa: E402
from PySide6.QtCore import QMimeData, QUrl  # noqa: E402
from PySide6.QtGui import QDesktopServices, QGuiApplication, QShortcut  # noqa: E402
from PySide6.QtWidgets import QApplication, QFileDialog, QPushButton  # noqa: E402

from colorroom.core import file_export  # noqa: E402
from colorroom.core import project as rules  # noqa: E402
from colorroom.core.designer import NamedPalette, PaletteColor  # noqa: E402
from colorroom.core.index import ImageError  # noqa: E402
from colorroom.ui import theme  # noqa: E402
from colorroom.ui.main_window import MAX_MIN_HEIGHT, TAB_IMAGE, TAB_LIBRARY, TAB_PALETTE, TAB_PROJECT, MainWindow  # noqa: E402
from colorroom.ui.theme import apply_theme  # noqa: E402

BRAND = ["#0B132B", "#F4F1DE", "#E07A5F", "#3D405B", "#81B29A", "#F2CC8F"]


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


def wait_for(app, condition, timeout: float = 30) -> None:
    deadline = time.monotonic() + timeout
    while not condition():
        app.processEvents()
        time.sleep(0.005)
        assert time.monotonic() < deadline, "the window did not get ready in time"


@pytest.fixture
def window(app):
    window = MainWindow(persist=False)  # never touch the user's saved settings
    window.resize(1280, 640)
    window.show()
    settle(app)
    yield window
    window.close()
    apply_theme(app, "light")
    settle(app)


def poster_psd(path, size=(300, 200)):
    """Black paper with a yellow block, as two pixel layers."""
    psd = PSDImage.new("RGB", size, color=(255, 255, 255))
    psd.append(psd.create_pixel_layer(Image.new("RGB", size, (0, 0, 0)), name="Paper"))
    psd.append(psd.create_pixel_layer(Image.new("RGB", (140, 80), (230, 200, 40)), name="Block", top=40, left=60))
    psd.save(str(path))
    return path


def stripes_png(path, colors=("#FFFFFF", "#102A54", "#E0452F", "#F2B705"), size=30):
    rgba = np.zeros((size, size * len(colors), 4), dtype=np.uint8)
    for position, color in enumerate(colors):
        rgba[:, position * size : (position + 1) * size] = (*[int(color[i : i + 2], 16) for i in (1, 3, 5)], 255)
    Image.fromarray(rgba, "RGBA").save(path)
    return path


def visible_primaries(window) -> list:
    return [button for button in window.findChildren(QPushButton) if button.property("primary") and button.isVisible()]


# --- the window and its tabs -------------------------------------------------------------


def test_the_window_opens_on_your_project(app, window):
    assert [window.tab_bar.tabText(i) for i in range(4)] == ["Your project", "Image", "My palette", "Library"]
    assert window.pages.currentIndex() == TAB_PROJECT and window.pages.currentWidget() is window.project
    assert window.minimumSizeHint().height() <= MAX_MIN_HEIGHT  # fits a 1280 × 720 screen
    project = window.project
    assert project.stack.currentWidget() is project.empty_page
    texts = [label.text() for label in project.empty_page.findChildren(type(project.photo_note))]
    assert "Recolor your own design and get the same file back" in texts
    assert "Drop a file here: PSD, AI, PDF, PNG, JPG" in texts
    assert "Photoshop files come back as Photoshop files, with every layer." in texts
    assert {"Open your file", "Try palettes on it", "Export the same file type"} <= set(texts)
    assert project.sample_button.text() == "No file handy? Try the sample"
    assert visible_primaries(window) == [project.open_file_button]  # one primary button: Open file…
    assert project.open_file_button.text() == "Open file…" and window.open_button.text() == "Open file…"
    assert not any("Upload" in button.text() for button in project.findChildren(QPushButton))
    window.show_tab(TAB_IMAGE)
    assert window.open_button.property("primary") and window.open_button.text() == "Open…"
    for tab, name in ((TAB_PALETTE, "My palette"), (TAB_LIBRARY, "Library"), (TAB_PROJECT, "Your project")):
        window.show_tab(tab)
        assert window.tab_bar.tabText(tab).startswith(name)
    shortcuts = {item.key().toString() for item in project.findChildren(QShortcut)}
    assert {"Ctrl+E", "\\"} <= shortcuts  # export and before/after, only while this tab shows


def test_the_header_open_button_and_drops_follow_the_tab(app, window, tmp_path, monkeypatch):
    png = stripes_png(tmp_path / "logo.png")
    monkeypatch.setattr(QFileDialog, "getOpenFileName", staticmethod(lambda *args, **kwargs: (str(png), "")))
    window.open_button.click()  # on Your project: the file opens here, in the background
    wait_for(app, lambda: window.project.has_upload() and not window.project.is_busy())
    assert window.pages.currentIndex() == TAB_PROJECT and window.index is None
    assert window.project.file_name() == "logo.png"

    mime = QMimeData()
    mime.setUrls([QUrl.fromLocalFile(str(poster_psd(tmp_path / "poster.psd")))])
    window.drop(mime)  # a dropped file goes to Your project too
    wait_for(app, lambda: window.project.file_name() == "poster.psd" and not window.project.is_busy())
    assert window.project.export_button.text() == "Export as PSD…" and window.index is None

    window.show_tab(TAB_IMAGE)
    window.open_button.click()  # on the Image tab it behaves as before
    wait_for(app, lambda: window.index is not None and not window.is_busy())
    assert window.file_label.text() == "logo.png" and window.project.file_name() == "poster.psd"

    window.show_tab(TAB_PROJECT)
    window.sample_clicked()  # Try sample follows the tab as well
    assert "sample" in window.project.file_name()


def test_opening_an_image_from_the_menu_shows_the_image_tab(app, window, tmp_path):
    window.open_path(str(stripes_png(tmp_path / "logo.png")))  # File > Open Recent, for example
    assert window.pages.currentIndex() == TAB_IMAGE
    wait_for(app, lambda: window.index is not None and not window.is_busy())


# --- the starter palette and the picker ------------------------------------------------


def test_the_first_result_needs_no_settings(app, window, tmp_path):
    project = window.project
    assert project.load_upload(poster_psd(tmp_path / "poster.psd"))
    assert project.palette_label() == rules.STARTER_LABEL and project.palette_key() == "idea:triadic"
    assert not np.array_equal(project.recolored(), project._source)  # the starter changes the file
    black = project._layers.colors.index(min(project._layers.colors, key=lambda c: int(c[1:3], 16)))
    assert project.targets()[black] in (None, project._layers.colors[black])  # the black paper stays black
    assert project.contrast_label.text().startswith("Text on background: ")
    assert project.accuracy_label.text() == "The exported file matches this preview."

    groups = [project.palette_combo.itemText(i) for i in range(project.palette_combo.count()) if project.palette_combo.itemData(i) is None]
    assert groups == ["IDEAS FROM THIS FILE"]  # nothing of your own yet
    window.studio.library.add(NamedPalette("Older favorite", [PaletteColor("#112233")]), favorite=True)
    favorite = window.studio.library.add(NamedPalette("Night", [PaletteColor(c) for c in ("#0B132B", "#F2CC8F", "#E07A5F")]), favorite=True)
    window._library_changed()
    window.studio.add_colors(BRAND, quiet=True)
    assert project.palette_key() == "idea:triadic"  # changing the palettes does not move the choice
    groups = [project.palette_combo.itemText(i) for i in range(project.palette_combo.count()) if project.palette_combo.itemData(i) is None]
    assert groups == ["MY PALETTES", "LIBRARY", "IDEAS FROM THIS FILE"]

    jobs = project.jobs()
    assert project.choose_palette(rules.library_key(favorite))
    assert project.palette_colors() == ["#0B132B", "#F2CC8F", "#E07A5F"] and project.jobs() == jobs  # roles stay
    assert len(project.swatches.flow._items) == 3
    index = project.palette_combo.findData(rules.mine_key(window.studio.palette))
    project.palette_combo.activated.emit(index)  # picking with the control itself
    assert project.palette_colors() == BRAND
    assert not project.choose_palette("idea:nothing")


def test_a_new_file_starts_on_the_newest_favorite_when_my_palette_is_empty(app, window, tmp_path):
    library = window.studio.library
    library.add(NamedPalette("Old", [PaletteColor("#112233"), PaletteColor("#FFEEDD")]), favorite=True)
    newest = library.add(NamedPalette("New", [PaletteColor("#0B132B"), PaletteColor("#F2CC8F")]), favorite=True)
    window._library_changed()
    assert window.project.load_upload(stripes_png(tmp_path / "logo.png"))
    assert window.project.palette_key() == rules.library_key(newest)


def test_the_roles_and_small_details_are_behind_links(app, window, tmp_path):
    project = window.project
    colors = ["#FFFFFF"] * 30 + ["#102A54"] * 12 + ["#E0452F", "#F2B705"]
    assert project.load_upload(stripes_png(tmp_path / "many.png", colors, 10))
    window.show_tab(TAB_PROJECT)
    settle(app)
    assert not project.role_chooser.isVisible()
    assert project.roles_toggle.text() == "Change which color plays each role…"
    project.roles_toggle.click()
    assert project.role_chooser.isVisible() and project.roles_toggle.text() == "Hide the roles"
    small = rules.small_details(project._layers.shares)
    assert small and project.small_toggle.text() == f"+ {len(small)} small detail{'s' if len(small) > 1 else ''}"
    assert not project.map_combos[small[0]].isVisible()
    project.small_toggle.click()
    assert project.map_combos[small[0]].isVisible()
    for (_, code), color in zip(project._results, project._layers.colors):  # no HEX code is ever cut short
        assert code.width() >= code.fontMetrics().horizontalAdvance(code.text())
        assert code.text().startswith("#") and len(code.text()) == 7


# --- before and after, and the More menu -----------------------------------------------


def test_before_and_after_change_no_choice(app, window, tmp_path):
    project = window.project
    window.studio.add_colors(BRAND, quiet=True)
    assert project.load_upload(stripes_png(tmp_path / "logo.png"))
    project.set_job(1, "Accent")
    jobs, overrides = project.jobs(), project.overrides()
    project.toggle_before_after()  # the \\ key
    assert project.showing_before() and project.before_button.isChecked()
    assert project.canvas._image == project._before_image
    assert project.jobs() == jobs and project.overrides() == overrides
    project.after_button.click()
    assert not project.showing_before() and project.jobs() == jobs
    menu = [action.text() for action in project.more_menu.actions() if action.text()]
    assert menu == ["Leave an area alone", "Forget my boxes", "Keep every original color", "Suggest roles again", "Copy preview", "Save preview as PNG…"]
    project.leave_area_action.trigger()
    assert "Drag a box" in window.toast.text()


def test_one_picture_layer_is_recolored_and_says_so(app, window, tmp_path):
    from test_photos_and_psd import photo

    psd = PSDImage.new("RGB", (240, 160), color=(255, 255, 255))
    psd.append(psd.create_pixel_layer(Image.fromarray(photo(240, 160), "RGB"), name="Everything"))
    psd.save(str(tmp_path / "flat.psd"))
    project = window.project
    assert project.load_upload(tmp_path / "flat.psd")
    assert project.left_out() == [] and project._whole
    assert project.photo_note.text().startswith("Your file is one picture layer, so everything is recolored.")
    project.leave_out((0.0, 0.0, 1.0, 1.0))  # a box over everything: no "Not recolored" chip on it
    assert "Forget my boxes" in project.photo_note.text()
    assert np.array_equal(project.recolored(), project._source) and not project.canvas.grab().isNull()


# --- exporting -------------------------------------------------------------------------


def test_the_arrow_menu_and_the_report_card(app, window, tmp_path, monkeypatch):
    project = window.project
    assert project.load_upload(poster_psd(tmp_path / "poster.psd"))
    entries = [action.text() for action in project.export_menu.actions() if action.text()]
    assert entries[-1] == "Export palette swatches (.ase)…"
    assert all(text.startswith("Export as ") for text in entries[:-1]) and not any("PSD…" in text for text in entries)
    assert len(entries) - 1 == len(file_export.choices("psd")) - 1  # every other format Colorroom writes

    assert project.export_swatches(tmp_path / "starter")
    assert (tmp_path / "starter.ase").read_bytes()[:4] == b"ASEF" and "Import Swatches" in window.status_label.text()

    report = project.export_file(tmp_path / "poster - Colorroom.psd")
    assert report is not None and project.report_card.isVisibleTo(window)
    assert project.report_title.text().startswith("Saved poster - Colorroom.psd")
    project.report_details_button.click()
    assert project.report_scroll.isVisibleTo(window) and "Recolored in place" in project.report_details.text()
    project.copy_report_button.click()
    assert QGuiApplication.clipboard().text() == report.text()
    opened = []
    monkeypatch.setattr(QDesktopServices, "openUrl", staticmethod(lambda url: opened.append(url.toLocalFile()) or True))
    project.show_folder_button.click()
    assert opened and opened[0].rstrip("/").endswith(tmp_path.name)
    project.report_done_button.click()
    assert not project.report_card.isVisibleTo(window)


def test_app_links_take_over_the_export_button(app, window, tmp_path):
    project = window.project
    received = []

    class FakePhotoshop:
        def __init__(self, ready=True):
            self.ready = ready

        def available(self):
            return self.ready

        def export(self, request, progress, cancelled):
            progress(0.5, "Photoshop is busy")
            received.append(request)
            return file_export.ExportReport(str(request.target), "psd", "layers", changed=["Block"])

    project.set_app_links({"psd": FakePhotoshop(ready=False)})
    assert project.load_upload(poster_psd(tmp_path / "poster.psd"))
    assert project.export_button.text() == "Export as PSD…" and not project.app_found()  # not installed: not used
    project.set_app_links({".PSD": FakePhotoshop()})
    assert project.app_found() and project.export_button.text() == "Export to Photoshop"
    assert project.accuracy_label.text() == "The preview is close. The exported file is exact."
    assert any(text.startswith("Export as PSD…") for text in (a.text() for a in project.export_menu.actions()))
    project.export_button.click()
    wait_for(app, lambda: not project.is_busy())
    request = received[0]
    assert request.source_path == str(tmp_path / "poster.psd") and request.source_format == "psd" and request.page == 0
    assert request.target.name == "poster - Colorroom.psd" and not request.target.exists()
    assert request.mapping and set(request.mapping) <= set(project._layers.colors)
    assert request.palette == tuple(project.palette_colors()) and request.roles == project.roles().as_dict()
    assert project.report_card.isVisibleTo(window) and "1 layer recolored" in project.report_title.text()

    def failing(request, progress, cancelled):
        raise ImageError("Photoshop has a dialog box open. Close it and try again.")

    project.set_app_links({"psd": failing})
    project.export_primary()
    wait_for(app, lambda: not project.is_busy())
    assert window.status_label.property("error") and "dialog box open" in window.status_label.text()
    project.set_app_links(None)
    assert project.export_button.text() == "Export as PSD…"


def test_indesign_files_get_a_plain_answer(app, window, tmp_path):
    project = window.project
    indd = tmp_path / "brochure.indd"
    indd.write_bytes(b"not really")
    assert not project.load_upload(indd)
    assert project.empty_message.isVisibleTo(window) and project.empty_message.text() == rules.indd_message(False)
    assert window.status_label.property("error")
    idml = tmp_path / "brochure.idml"
    idml.write_bytes(b"PK")
    assert not project.load_upload(idml) and project.empty_message.text() == rules.IDML_NOT_YET
    assert "*.indd" in project.open_patterns() and "*.idml" not in project.open_patterns()

    class FakeInDesign:
        def export(self, request, progress, cancelled):
            raise AssertionError("not called here")

        def preview(self, path, progress, cancelled):
            picture = np.zeros((40, 60, 4), dtype=np.uint8)
            picture[..., 3] = 255
            picture[:, 30:, 0] = 200
            return picture

    project.set_app_links({"indd": FakeInDesign(), "idml": FakeInDesign()})
    assert "*.idml" in project.open_patterns()
    assert project.load_upload(indd) and project.file_name() == "brochure.indd"
    assert project.export_button.text() == "Export to InDesign" and project.export_button.isEnabled()


def test_the_tab_follows_the_theme(app, window, tmp_path):
    assert window.project.load_upload(poster_psd(tmp_path / "poster.psd"))
    settle(app)
    window.set_theme("dark")
    settle(app)
    assert theme.MODE == "dark" and window.grab().toImage().pixelColor(4, 300).lightness() < 60
    assert not window.project.canvas.grab().isNull()
    window.set_theme("light")
    settle(app)
    assert window.grab().toImage().pixelColor(4, 300).lightness() > 200
