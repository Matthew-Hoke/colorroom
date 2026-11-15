"""Tests for exporting the recolored upload in the format it came in (made by Opus 5.5)."""

from __future__ import annotations

import os

import numpy as np
import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")  # no window appears while testing

pytest.importorskip("psd_tools")
pytest.importorskip("pypdfium2")
pytest.importorskip("PySide6")

from PIL import Image  # noqa: E402
from psd_tools import PSDImage  # noqa: E402
from psd_tools.constants import BlendMode  # noqa: E402
from PySide6.QtWidgets import QApplication, QPushButton  # noqa: E402

from colorroom.core import designer, file_export  # noqa: E402
from colorroom.core.file_export import ExportError, Recipe  # noqa: E402
from colorroom.ui.main_window import TAB_PROJECT, MainWindow  # noqa: E402
from colorroom.ui.theme import apply_theme  # noqa: E402

from make_pdf import BRAND_GUIDE, write_pdf  # noqa: E402

BLACK, YELLOW, WHITE, NAVY = "#000000", "#E6C828", "#FFFFFF", "#0B295F"
SWAP = Recipe([BLACK, YELLOW], [WHITE, NAVY])


def poster(width: int = 300, height: int = 200) -> np.ndarray:
    """Black paper with a yellow block: the shape of a simple two-color drawing."""
    rgba = np.zeros((height, width, 4), dtype=np.uint8)
    rgba[..., 3] = 255
    rgba[40:120, 60:200, :3] = (230, 200, 40)
    return rgba


def layered_psd(path, extra=None):
    """A black Background layer and a yellow pixel layer; `extra(psd)` may add more."""
    psd = PSDImage.new("RGB", (300, 200), color=(255, 255, 255))
    psd.append(psd.create_pixel_layer(Image.new("RGB", (300, 200), (0, 0, 0)), name="Paper"))
    psd.append(psd.create_pixel_layer(Image.new("RGB", (140, 80), (230, 200, 40)), name="Block", top=40, left=60))
    if extra is not None:
        extra(psd)
    psd.save(str(path))
    return path


def flat_psd(path):
    PSDImage.frompil(Image.fromarray(poster()[..., :3], "RGB")).save(str(path))
    return path


def shown(path) -> np.ndarray:
    """What Photoshop would show when opening the file: the picture stored in it."""
    return np.array(PSDImage.open(str(path)).topil(apply_icc=False).convert("RGBA"))


def near(pixel, color: str, tolerance: int = 3) -> bool:
    wanted = tuple(int(color[i : i + 2], 16) for i in (1, 3, 5))
    return all(abs(int(a) - b) <= tolerance for a, b in zip(pixel[:3], wanted))


# ======================================================================================
# Formats
# ======================================================================================


def test_the_format_that_came_in_is_offered_first():
    for name, first in (("a.psd", "psd"), ("a.PSB", "psb"), ("a.png", "png"), ("a.jpeg", "jpg"), ("a.tif", "tiff"), ("a.webp", "webp"), ("a.gif", "gif"), ("a.pdf", "pdf")):
        offered = file_export.choices(file_export.source_format(name))
        assert offered[0] == first and len(offered) == len(set(offered)) and "png" in offered
    assert file_export.choices(file_export.source_format("logo.ai"))[0] == "pdf"  # Illustrator's format cannot be written
    assert file_export.choices("")[0] == "png"
    assert "layers kept" in file_export.label("psd", "psd") and "one layer" in file_export.label("psd", "png")
    assert "Illustrator" in file_export.label("pdf", "ai")


def test_the_suggested_name_sits_beside_the_original_and_never_on_a_file(tmp_path):
    source = tmp_path / "poster.psd"
    source.write_bytes(b"x")
    first = file_export.default_target(source, "psd")
    assert first == tmp_path / "poster - Colorroom.psd"
    first.write_bytes(b"x")
    assert file_export.default_target(source, "psd") == tmp_path / "poster - Colorroom 2.psd"


# ======================================================================================
# The color moves at full size
# ======================================================================================


def test_full_size_recoloring_matches_the_preview():
    rgba = poster()
    rgba[60:100, 80:180, :3] = np.linspace(120, 230, 100).astype(np.uint8)[None, :, None]  # some shading
    layers = designer.find_layers(rgba)
    targets = [WHITE, NAVY] + [None] * (len(layers.colors) - 2)
    preview = designer.recolor(layers, targets)
    full = file_export.recolor_pixels(rgba, Recipe(layers.colors, targets, [], layers.blends, layers.flat), chunk=7_000)
    assert np.abs(preview.astype(int) - full.astype(int)).max() <= 3
    assert np.array_equal(full[..., 3], rgba[..., 3])


def test_left_out_areas_do_not_change():
    rgba = poster()
    recipe = Recipe([BLACK, YELLOW], [WHITE, NAVY], [(0.0, 0.0, 0.5, 1.0)])
    out = file_export.recolor_pixels(rgba, recipe, file_export.left_out_mask(rgba.shape, recipe.boxes))
    assert np.array_equal(out[:, :150], rgba[:, :150])
    assert near(out[10, 290], WHITE) and near(out[80, 190], NAVY)
    assert file_export.left_out_mask(rgba.shape, [(0, 0, 1, 1)]) is None  # like the preview


# ======================================================================================
# Pictures
# ======================================================================================


@pytest.mark.parametrize("key", ["png", "jpg", "tiff", "webp", "gif", "bmp", "pdf"])
def test_a_picture_is_saved_at_full_size_in_each_format(tmp_path, key):
    big = np.kron(poster(), np.ones((8, 8, 1), dtype=np.uint8))  # 2400 x 1600: bigger than the preview
    source = tmp_path / "poster.png"
    Image.fromarray(big, "RGBA").save(source)
    target = tmp_path / f"out{file_export.FORMATS[key][1]}"
    report = file_export.export_upload(source, SWAP, target, key)
    assert report.how == "picture" and (report.width, report.height) == (2400, 1600) and target.stat().st_size > 0
    assert not list(tmp_path.glob("*.colorroom-part"))
    if key != "pdf":
        saved = Image.open(target)
        assert saved.size == (2400, 1600)
        pixels = np.array(saved.convert("RGB"))
        assert near(pixels[40, 40], WHITE, 6) and near(pixels[600, 1000], NAVY, 6)


def test_a_picture_can_become_a_one_layer_photoshop_file(tmp_path):
    source = tmp_path / "logo.png"
    Image.fromarray(poster(), "RGBA").save(source)
    file_export.export_upload(source, SWAP, tmp_path / "logo.psd", "psd")
    psd = PSDImage.open(str(tmp_path / "logo.psd"))
    assert [layer.name for layer in psd] == ["logo"] and psd.size == (300, 200)
    assert near(shown(tmp_path / "logo.psd")[80, 100], NAVY)


def test_the_original_is_never_written_over(tmp_path):
    source = tmp_path / "poster.png"
    Image.fromarray(poster(), "RGBA").save(source)
    before = source.read_bytes()
    with pytest.raises(ExportError, match="never writes over"):
        file_export.export_upload(source, SWAP, source, "png")
    psd = layered_psd(tmp_path / "poster.psd")
    with pytest.raises(ExportError, match="never writes over"):
        file_export.export_upload(psd, SWAP, tmp_path / "POSTER.psd", "psd")
    assert source.read_bytes() == before


def test_a_pdf_page_is_saved_as_a_picture(tmp_path):
    source = write_pdf(tmp_path / "guide.pdf", BRAND_GUIDE)
    report = file_export.export_upload(source, Recipe(["#FFFFFF"], ["#FFF4D6"]), tmp_path / "guide - Colorroom.pdf", "pdf")
    assert max(report.width, report.height) == file_export.PRINT_LONGEST and "one picture" in report.text()
    assert (tmp_path / "guide - Colorroom.pdf").read_bytes().startswith(b"%PDF")


# ======================================================================================
# Photoshop files
# ======================================================================================


def test_pixel_layers_are_recolored_in_place_and_everything_else_is_kept(tmp_path):
    source = layered_psd(tmp_path / "poster.psd")
    original = source.read_bytes()
    target = tmp_path / "poster - Colorroom.psd"
    report = file_export.export_upload(source, SWAP, target, "psd")
    assert report.how == "layers" and report.changed == ["Paper", "Block"] and "2 layers" in report.summary()
    assert source.read_bytes() == original
    was, now = PSDImage.open(str(source)), PSDImage.open(str(target))
    assert [(l.name, l.kind, l.bbox, l.opacity) for l in now] == [(l.name, l.kind, l.bbox, l.opacity) for l in was]
    paper, block = (np.array(layer.topil(apply_icc=False).convert("RGB")) for layer in now)
    assert near(paper[5, 5], WHITE) and near(block[5, 5], NAVY)
    picture = shown(target)  # the picture stored in the file was redrawn too
    assert near(picture[5, 5], WHITE) and near(picture[80, 100], NAVY)


def test_a_boxed_area_of_a_layer_is_left_alone(tmp_path):
    source = layered_psd(tmp_path / "poster.psd")
    recipe = Recipe([BLACK, YELLOW], [WHITE, NAVY], [(0.0, 0.0, 0.5, 1.0)])
    report = file_export.export_upload(source, recipe, tmp_path / "out.psd", "psd")
    picture = shown(tmp_path / "out.psd")
    assert report.how == "layers"
    assert near(picture[80, 100], YELLOW) and near(picture[5, 5], BLACK)  # inside the box: untouched
    assert near(picture[80, 190], NAVY) and near(picture[5, 290], WHITE)


def test_a_flat_photoshop_file_stays_flat(tmp_path):
    source = flat_psd(tmp_path / "db.psd")
    report = file_export.export_upload(source, SWAP, tmp_path / "db - Colorroom.psd", "psd")
    saved = PSDImage.open(str(tmp_path / "db - Colorroom.psd"))
    assert report.how == "flat" and len(list(saved.descendants())) == 0 and saved.size == (300, 200)
    picture = shown(tmp_path / "db - Colorroom.psd")
    assert near(picture[5, 5], WHITE) and near(picture[80, 100], NAVY)


def test_colors_that_layers_cannot_carry_go_on_a_new_top_layer(tmp_path):
    def see_through(psd):
        # A half-transparent blue sheet: the color seen (a mix) is on no layer by itself.
        psd.append(psd.create_pixel_layer(Image.new("RGB", (150, 200), (20, 60, 230)), name="Sheet", left=150, opacity=128))

    source = layered_psd(tmp_path / "poster.psd", see_through)
    mixed = "#%02X%02X%02X" % tuple(shown(source)[150, 250][:3])
    recipe = Recipe([BLACK, YELLOW, mixed], [BLACK, YELLOW, "#D94F30"])
    target = tmp_path / "out.psd"
    report = file_export.export_upload(source, recipe, target, "psd")
    assert report.how == "on top" and "Why a layer on top" in report.text()
    was, now = PSDImage.open(str(source)), PSDImage.open(str(target))
    assert [layer.name for layer in now] == [layer.name for layer in was] + [file_export.LAYER_NAME]
    for old, new in zip(was, now):  # the original layers are byte for byte the same
        assert [bytes(data.data) for data in old._channels] == [bytes(data.data) for data in new._channels]
    assert near(shown(target)[150, 250], "#D94F30", 8)


def test_files_colorroom_cannot_write_say_so_plainly(tmp_path):
    deep = tmp_path / "deep.psd"
    PSDImage.new("RGB", (40, 30), color=(255, 255, 255), depth=16).save(str(deep))
    with pytest.raises(ExportError, match="16-bit"):
        file_export.export_upload(deep, SWAP, tmp_path / "out.psd", "psd")
    assert not (tmp_path / "out.psd").exists()
    with pytest.raises(ExportError, match="moved or renamed"):
        file_export.export_psd(tmp_path / "gone.psd", SWAP, tmp_path / "out.psd")


def test_blend_modes_and_hidden_layers_survive(tmp_path):
    def more(psd):
        psd.append(psd.create_pixel_layer(Image.new("RGB", (30, 30), (230, 200, 40)), name="Hidden", top=150, left=10))
        psd[-1].visible = False
        psd.append(psd.create_pixel_layer(Image.new("RGB", (40, 40), (230, 200, 40)), name="Glow", top=150, left=240, blend_mode=BlendMode.SCREEN))

    source = layered_psd(tmp_path / "poster.psd", more)
    report = file_export.export_upload(source, SWAP, tmp_path / "out.psd", "psd")
    now = PSDImage.open(str(tmp_path / "out.psd"))
    # Whichever way it was saved, the four original layers are all there, as they were.
    assert [(layer.name, layer.visible, layer.blend_mode) for layer in now][:4] == [
        (layer.name, layer.visible, layer.blend_mode) for layer in PSDImage.open(str(source))
    ]
    assert report.how in ("layers", "on top")
    if report.how == "layers":
        assert ("Hidden", "hidden") in report.kept and "Hidden" not in report.changed
        assert near(np.array(now[2].topil(apply_icc=False).convert("RGB"))[5, 5], YELLOW)  # hidden layers are not touched


# ======================================================================================
# In the window
# ======================================================================================


@pytest.fixture(scope="module")
def app():
    application = QApplication.instance() or QApplication([])
    apply_theme(application)
    return application


@pytest.fixture
def window(app):
    main = MainWindow(persist=False)  # never touch the user's saved settings
    main.resize(1500, 950)
    main.show()
    main.studio.add_colors([WHITE, NAVY, "#FFCC00", "#0072BB"], quiet=True)
    main.show_tab(TAB_PROJECT)
    app.processEvents()
    yield main
    main.close()
    for _ in range(5):
        app.processEvents()


def test_the_export_button_follows_the_file_and_saves_its_format(app, window, tmp_path):
    project = window.project
    assert not project.export_button.isVisible() or not project.export_button.isEnabled()
    assert project.open_file_button.property("primary") and project.open_file_button.isVisible()
    assert project.load_upload(flat_psd(tmp_path / "db cooper.psd"))
    app.processEvents()
    assert project.export_button.isVisible() and project.export_button.isEnabled()
    assert project.export_button.text() == "Export as PSD…" and project.export_formats()[0] == "psd"
    assert not project.open_file_button.isVisible() and not window.open_button.property("primary")  # one main button
    shown_primaries = [b for b in window.findChildren(QPushButton) if b.property("primary") and b.isVisible()]
    assert shown_primaries == [project.export_button]

    report = project.export_file(tmp_path / "db cooper - Colorroom.psd")
    assert report is not None and report.how == "flat" and not window.status_label.property("error")
    assert "db cooper - Colorroom.psd" in window.status_label.text()
    saved = shown(tmp_path / "db cooper - Colorroom.psd")
    assert not np.array_equal(saved, shown(tmp_path / "db cooper.psd"))
    preview = project.recolored()
    assert np.abs(saved[..., :3].astype(int) - preview[..., :3].astype(int)).max() <= 3  # what you see is what is saved

    # Another format on request, and a name typed without an ending gets one.
    assert project.export_file(tmp_path / "db cooper as picture.png").how == "picture"
    assert project.export_file(tmp_path / "typed", "jpg") is not None and (tmp_path / "typed.jpg").exists()
    # Never over the original.
    assert project.export_file(tmp_path / "db cooper.psd") is None and window.status_label.property("error")

    png = tmp_path / "logo.png"
    Image.fromarray(poster(), "RGBA").save(png)
    assert project.load_upload(png)
    app.processEvents()
    assert project.export_button.text() == "Export as PNG…"
    assert window.studio.export_button.text() == "Export palette…"


def test_swatch_files_say_how_to_load_them(app, window, tmp_path):
    assert window.studio.export_to(tmp_path / "palette.ase", "ase")
    assert "Import Swatches" in window.status_label.text()


def test_the_export_window_starts_on_the_original_format_and_saves_in_the_background(app, window, tmp_path, monkeypatch):
    import time

    from PySide6.QtWidgets import QFileDialog, QMessageBox

    project = window.project
    assert project.load_upload(layered_psd(tmp_path / "poster.psd"))
    project.change_photos.setChecked(True)
    app.processEvents()
    asked, told = {}, []

    def choose(parent, title, start, filters, chosen):
        asked.update(start=start, filters=filters, chosen=chosen)
        return start, chosen

    monkeypatch.setattr(QFileDialog, "getSaveFileName", staticmethod(choose))
    monkeypatch.setattr(QMessageBox, "information", staticmethod(lambda parent, title, text: told.append(text)))
    project.export_button.click()  # the one primary button
    assert not project.export_button.isEnabled()  # no second export while one is running
    deadline = time.monotonic() + 30
    while project._export_tasks and time.monotonic() < deadline:
        app.processEvents()
        time.sleep(0.01)
    assert asked["start"].endswith("poster - Colorroom.psd") and asked["chosen"].startswith("Photoshop file, layers kept")
    assert asked["filters"].split(";;")[0] == asked["chosen"] and "PNG picture" in asked["filters"]
    assert (tmp_path / "poster - Colorroom.psd").exists() and project.last_report is not None
    assert not told  # the report is a card on the tab now, not a message box
    assert project.report_card.isVisibleTo(window) and "poster - Colorroom.psd" in project.report_title.text()
    assert project.export_button.isEnabled()


def test_resolution_is_kept_in_every_format(tmp_path):
    """A 300 ppi file stays 300 ppi, whatever it is saved as (made by Opus 5.5)."""
    source = tmp_path / "print.png"
    Image.fromarray(poster(), "RGBA").save(source, dpi=(300, 300))
    for key in ("png", "jpg", "tiff"):
        target = tmp_path / f"out{file_export.FORMATS[key][1]}"
        file_export.export_upload(source, SWAP, target, key)
        assert round(Image.open(target).info["dpi"][0]) == 300
    file_export.export_upload(source, SWAP, tmp_path / "out.psd", "psd")
    as_psd = PSDImage.open(str(tmp_path / "out.psd"))
    assert file_export.psd_dpi(as_psd) == 300 and as_psd.size == (300, 200)
    # From a Photoshop file: kept in place, and carried into pictures made from it.
    file_export.export_upload(tmp_path / "out.psd", SWAP, tmp_path / "again.psd", "psd")
    assert file_export.psd_dpi(PSDImage.open(str(tmp_path / "again.psd"))) == 300
    file_export.export_upload(tmp_path / "out.psd", SWAP, tmp_path / "from psd.png", "png")
    assert round(Image.open(tmp_path / "from psd.png").info["dpi"][0]) == 300


def test_the_stored_picture_shows_the_new_colors_when_a_layer_goes_on_top(tmp_path, monkeypatch):
    """Thumbnails and file previews must not keep the old colors (found on a real file with gradient maps)."""
    import psd_tools.composite as compositing

    def see_through(psd):
        psd.append(psd.create_pixel_layer(Image.new("RGB", (150, 200), (20, 60, 230)), name="Sheet", left=150, opacity=128))

    source = layered_psd(tmp_path / "poster.psd", see_through)
    mixed = "#%02X%02X%02X" % tuple(shown(source)[150, 250][:3])
    recipe = Recipe([BLACK, YELLOW, mixed], [WHITE, YELLOW, "#D94F30"])

    def cannot_draw(*args, **kwargs):
        raise ImportError("Gradient fills require: scipy")

    monkeypatch.setattr(compositing, "composite", cannot_draw)
    monkeypatch.setattr(compositing, "composite_pil", cannot_draw)
    report = file_export.export_upload(source, recipe, tmp_path / "out.psd", "psd")
    assert report.how == "on top"
    picture = shown(tmp_path / "out.psd")
    assert near(picture[5, 5], WHITE) and near(picture[150, 250], "#D94F30", 8)
