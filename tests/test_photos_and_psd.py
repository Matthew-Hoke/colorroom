"""Tests for Photoshop files, design files on the Your project tab, and leaving photos out
of recoloring."""

from __future__ import annotations

import os
import time

import numpy as np
import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")  # no window appears while testing

pytest.importorskip("psd_tools")
pytest.importorskip("pypdfium2")
pytest.importorskip("PySide6")

from PIL import Image, ImageDraw, ImageFont  # noqa: E402
from psd_tools import PSDImage  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from colorroom.core import designer, documents  # noqa: E402
from colorroom.core.colorspace import oklab_distance  # noqa: E402
from colorroom.core.documents import DocumentError  # noqa: E402
from colorroom.ui.main_window import TAB_PROJECT, MainWindow  # noqa: E402
from colorroom.ui.theme import apply_theme  # noqa: E402

from make_pdf import BRAND_GUIDE, write_pdf  # noqa: E402

BRAND = ["#0B132B", "#F4F1DE", "#E07A5F", "#3D405B", "#81B29A", "#F2CC8F"]
PHOTO_BOX = (0.0, 70 / 640, 1.0, 310 / 640)  # where web_page() puts its photo


def photo(width: int, height: int, seed: int = 7) -> np.ndarray:
    """Something with the statistics of a photograph: smooth shapes plus fine grain."""
    rng = np.random.default_rng(seed)
    yy, xx = np.mgrid[0:height, 0:width]
    layers = [120 + 80 * np.sin(xx / 37 + yy / 19), 110 + 70 * np.cos(xx / 23), 90 + 90 * np.sin(yy / 13 + xx / 51)]
    return np.clip(np.stack(layers, axis=2) + rng.normal(0, 22, (height, width, 3)), 0, 255).astype(np.uint8)


def web_page(width: int = 1000, height: int = 640) -> np.ndarray:
    """A made-up web page: flat header with text, a photo, headings, a smooth gradient, a card."""
    page = Image.new("RGB", (width, height), "#FFFFFF")
    draw = ImageDraw.Draw(page)
    draw.rectangle([0, 0, width, 70], fill="#2B4F8C")
    font = ImageFont.load_default(size=26)
    draw.text((30, 20), "MESA VALLEY   Shop   About   Journal", fill="#FFFFFF", font=font)
    page.paste(Image.fromarray(photo(width, 240), "RGB"), (0, 70))
    draw.text((330, 350), "We are Mesa Valley", fill="#222222", font=ImageFont.load_default(size=44))
    draw.text((160, 420), "A made-up studio for testing Colorroom, with a long line.", fill="#333333", font=font)
    gradient = np.zeros((150, 400, 3), dtype=np.uint8)
    gradient[..., 0] = np.linspace(255, 200, 400)[None, :]
    gradient[..., 1] = np.linspace(210, 40, 150)[:, None]
    page.paste(Image.fromarray(gradient, "RGB"), (60, 480))
    draw.rectangle([520, 480, 960, 630], fill="#F0F0F0")
    draw.text((560, 520), "What we do", fill="#222222", font=ImageFont.load_default(size=34))
    return np.array(page.convert("RGBA"))


def layered_psd(path):
    """White canvas, a flat navy raster block, and a photo layer."""
    psd = PSDImage.new("RGB", (400, 300), color=(255, 255, 255))
    psd.append(psd.create_pixel_layer(Image.new("RGB", (120, 90), (16, 42, 84)), name="Flat block", top=20, left=20))
    psd.append(psd.create_pixel_layer(Image.fromarray(photo(200, 150, 3), "RGB"), name="Photo", top=130, left=180))
    psd.save(str(path))
    return path


def close_to(found, wanted, tolerance=0.03) -> bool:
    return all(abs(a - b) <= tolerance for a, b in zip(found, wanted))


# ======================================================================================
# Engine
# ======================================================================================


def test_busy_squares_are_photos_not_flat_art_text_or_gradients():
    flat = np.zeros((120, 240, 4), dtype=np.uint8)
    flat[..., :3], flat[..., 3] = (43, 79, 140), 255
    assert not designer.busy_grid(flat).any()
    rgb = photo(240, 120)
    noisy = np.dstack([rgb, np.full(rgb.shape[:2], 255, dtype=np.uint8)])
    assert designer.busy_grid(noisy).mean() > 0.95
    page = web_page()
    grid = designer.busy_grid(page, 12)
    rows = grid.shape[0]
    inside = grid[int(PHOTO_BOX[1] * rows) + 1 : int(PHOTO_BOX[3] * rows) - 1]
    outside = np.concatenate([grid[: int(PHOTO_BOX[1] * rows) - 1].ravel(), grid[int(PHOTO_BOX[3] * rows) + 1 :].ravel()])
    assert inside.mean() > 0.95  # the photo
    assert outside.mean() < 0.02  # header text, headings, the gradient and the card are not photos
    assert designer.busy_grid(np.zeros((5, 5, 4), dtype=np.uint8)).shape == (1, 1)


def test_photo_areas_are_guessed_on_a_flat_picture():
    boxes = designer.photo_boxes(web_page())
    assert len(boxes) == 1 and close_to(boxes[0], PHOTO_BOX)
    assert designer.photo_boxes(web_page()[:60]) == []  # just the header: nothing to leave out
    speck = web_page()
    speck[330:650, :] = 255
    speck[400:420, 500:520, :3] = photo(20, 20)  # a tiny textured icon is not a photo
    assert len(designer.photo_boxes(speck)) == 1


def test_known_picture_boxes_are_kept_only_if_they_look_like_photos():
    canvas = np.full((300, 400, 4), 255, dtype=np.uint8)
    canvas[20:110, 20:140, :3] = (16, 42, 84)  # a flat raster shape
    canvas[130:280, 180:380, :3] = photo(200, 150, 3)
    flat_box, photo_box = (0.05, 0.07, 0.35, 0.37), (0.45, 0.43, 0.95, 0.93)
    assert designer.photo_boxes(canvas, [flat_box, photo_box]) == [photo_box]
    assert designer.photo_boxes(canvas, []) == []  # the file says there are no pictures: trust it
    mask = designer.boxes_mask((300, 400), [photo_box])
    assert mask[200, 300] and not mask[50, 50] and mask.mean() == pytest.approx(0.25, abs=0.01)
    assert not designer.boxes_mask((10, 10), []).any()
    assert designer.boxes_mask((10, 10), [(0.9, 0.9, 0.1, 0.1)]).mean() == pytest.approx(0.64)  # corners in any order


def test_left_out_pixels_do_not_count_and_do_not_change():
    page = web_page()
    mask = designer.boxes_mask(page.shape[:2], [PHOTO_BOX])
    layers = designer.find_layers(page, exclude=mask)
    everything = designer.find_layers(page)
    assert layers.excluded is not None and everything.excluded is None
    for color in layers.colors[:3]:  # the page's real colors lead once the photo is gone
        assert min(oklab_distance(color, known) for known in ("#FFFFFF", "#2B4F8C", "#F0F0F0", "#222222", "#E08A2A", "#E24D06", "#E2BA12")) < 0.08
    assert sum(layers.shares) == pytest.approx(1.0)
    out = designer.recolor(layers, ["#101010", "#C0392B"] + [None] * (len(layers.colors) - 2))
    assert np.array_equal(out[mask], page[mask])  # every photo pixel is exactly as it was
    assert not np.array_equal(out[~mask], page[~mask])
    assert designer.find_layers(page, exclude=np.zeros(page.shape[:2], dtype=bool)).excluded is None
    assert designer.find_layers(page, exclude=np.ones(page.shape[:2], dtype=bool)).excluded is None  # nothing left: ignore


def test_photoshop_files_open_with_their_layers(tmp_path):
    document = documents.open_document(layered_psd(tmp_path / "poster.psd"))
    try:
        assert (document.kind, document.page_names, document.note) == ("Photoshop", ["Whole image"], "")
        page = document.render(0)
        assert page.shape == (300, 400, 4) and tuple(page[60, 60][:3]) == (16, 42, 84) and tuple(page[5, 5][:3]) == (255, 255, 255)
        assert max(document.thumbnail(0).shape[:2]) == documents.THUMBNAIL_LONGEST
        assert document.layer_names() == [("Photo", "pixel"), ("Flat block", "pixel")]
        boxes = document.image_boxes(0)  # the flat block is artwork, not a picture
        assert len(boxes) == 1 and close_to(boxes[0], (0.45, 0.433, 0.95, 0.933))
        assert designer.photo_boxes(page, boxes) == boxes
        with pytest.raises(DocumentError):
            document.render(5)
    finally:
        document.close()
    assert documents.is_document("Layout.PSD") and documents.is_document("big.psb")
    junk = tmp_path / "junk.psd"
    junk.write_bytes(b"8BPS but not really a photoshop file")
    with pytest.raises(DocumentError, match="could not be opened as a Photoshop file"):
        documents.open_document(junk)


def test_pdf_reports_where_its_placed_pictures_are(tmp_path):
    scan = tmp_path / "scan.pdf"
    Image.fromarray(photo(300, 200), "RGB").save(scan, "PDF")  # one page that is one picture
    document = documents.open_document(scan)
    try:
        boxes = document.image_boxes(0)
        assert len(boxes) == 1 and close_to(boxes[0], (0, 0, 1, 1), 0.01)
    finally:
        document.close()
    document = documents.open_document(write_pdf(tmp_path / "vector.pdf", BRAND_GUIDE[:1]))
    try:
        assert document.image_boxes(0) == []  # shapes only: nothing to leave out
    finally:
        document.close()


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


def wait_idle(app, window, timeout: float = 40) -> None:
    deadline = time.monotonic() + timeout
    while window.index is None or window.is_busy():
        app.processEvents()
        time.sleep(0.005)
        assert time.monotonic() < deadline, "the window did not get ready in time"
    settle(app)


@pytest.fixture
def window(app):
    window = MainWindow(persist=False)  # never touch the user's saved settings
    window.resize(1460, 940)
    window.show()
    window.studio.add_colors(BRAND, quiet=True)
    window.show_tab(TAB_PROJECT)
    yield window
    window.close()
    settle(app)


def test_photos_are_left_out_by_default_and_the_box_turns_them_on(app, window, tmp_path):
    project = window.project
    path = tmp_path / "site.png"
    Image.fromarray(web_page(), "RGBA").save(path)
    assert not project.change_photos.isChecked() and not project.change_photos.isVisible()
    assert project.load_upload(path)
    settle(app)
    assert project.change_photos.isEnabled() and not project.change_photos.isChecked()
    assert len(project.left_out()) == 1 and close_to(project.left_out()[0], PHOTO_BOX)
    note = project.photo_note.text()
    assert note.startswith("Photos: 1 area left alone (")
    assert "best guess" in note and project.photo_note.isVisibleTo(window)
    source = project._source
    mask = designer.boxes_mask(source.shape[:2], project.left_out())
    out = project.recolored()
    assert np.array_equal(out[mask], source[mask]) and not np.array_equal(out[~mask], source[~mask])
    assert not project.canvas.grab().isNull()

    project.change_photos.setChecked(True)  # now the photo is recolored too
    assert project.left_out() == [] and "Photos are recolored too" in project.photo_note.text()
    assert not np.array_equal(project.recolored()[mask], source[mask])
    project.change_photos.setChecked(False)
    assert len(project.left_out()) == 1


def test_dragging_a_box_leaves_that_area_alone(app, window, tmp_path):
    project = window.project
    flat = np.full((200, 300, 4), 255, dtype=np.uint8)
    flat[:, :150, :3] = (16, 42, 84)
    flat[:, 150:, :3] = (224, 69, 47)
    project.set_upload(flat, "flat")
    settle(app)
    assert project.left_out() == [] and "No photos found" in project.photo_note.text()
    assert not project.forget_boxes_action.isEnabled()
    changed = project.recolored()
    assert not np.array_equal(changed[:, 200:], flat[:, 200:])
    project.set_job(0, "Accent")  # a choice the user made by hand
    chosen = dict(zip(project._layers.colors, project.jobs()))
    project.canvas.boxDrawn.emit(0.5, 0.0, 1.0, 1.0)  # drag over the right half
    assert project.left_out() == [(0.5, 0.0, 1.0, 1.0)] and project.forget_boxes_action.isEnabled()
    assert np.array_equal(project.recolored()[:, 150:], flat[:, 150:])  # exactly as it was
    for color, job in zip(project._layers.colors, project.jobs()):  # earlier choices survive
        assert chosen[color] == job
    assert "Photos: 1 area left alone (50%)" in project.photo_note.text()
    project.change_photos.setChecked(True)  # a hand-drawn box is still respected
    assert project.left_out() == [(0.5, 0.0, 1.0, 1.0)]
    project.forget_boxes_action.trigger()  # More > Forget my boxes
    assert project.left_out() == [] and not np.array_equal(project.recolored()[:, 200:], flat[:, 200:])
    project.set_upload(None)
    assert project.left_out() == [] and project.stack.currentWidget() is project.empty_page
    project.leave_out((0, 0, 1, 1))  # nothing open: nothing happens
    assert project.left_out() == []


def test_design_files_work_on_your_project(app, window, tmp_path):
    project = window.project
    assert project.load_upload(layered_psd(tmp_path / "poster.psd"))
    settle(app)
    assert project.has_upload() and not window.status_label.property("error")
    assert len(project.left_out()) == 1 and close_to(project.left_out()[0], (0.45, 0.433, 0.95, 0.933))
    assert "the file's own pictures" in project.photo_note.text()
    assert not project.page_spin.isVisibleTo(window)
    assert project.facts_label.text().startswith("400 × 300") and "layers" in project.facts_label.text()
    source = project._source
    out = project.recolored()
    assert np.array_equal(out[150:260, 200:360], source[150:260, 200:360])  # the photo layer
    assert not np.array_equal(out[30:100, 30:130], source[30:100, 30:130])  # the flat block changed

    assert project.load_upload(write_pdf(tmp_path / "guide.pdf", BRAND_GUIDE))
    settle(app)
    assert project.page_spin.isVisibleTo(window) and project.page_spin.maximum() == 4 and project.left_out() == []
    first = project._layers.colors
    project.page_spin.setValue(4)
    assert project._layers.colors != first and "Page 4" in project.file_name()
    art = tmp_path / "logo.ai"
    art.write_bytes((tmp_path / "guide.pdf").read_bytes())
    assert project.load_upload(art) and "logo.ai" in project.file_name()
    assert project.export_button.text() == "Export as PDF…"

    old = tmp_path / "old.ai"
    old.write_bytes(b"%!PS-Adobe-3.0\n")
    assert not project.load_upload(old)
    assert window.status_label.property("error") and "PDF compatibility" in window.status_label.text()
    assert "logo.ai" in project.file_name()  # the file opened before is still there

    png = tmp_path / "flat.png"
    Image.new("RGB", (60, 40), "#102A54").save(png)
    assert project.load_upload(png)
    assert not project.page_spin.isVisibleTo(window) and project._document is None


def test_a_photoshop_file_opens_on_the_image_tab(app, window, tmp_path):
    window.open_path(str(layered_psd(tmp_path / "poster.psd")))
    wait_idle(app, window)
    assert window.document is not None and window.document.kind == "Photoshop"
    assert not window.page_strip.isVisible()  # one page
    assert window.windowTitle().startswith("poster.psd") and not window.status_label.property("error")
    hexes = {window.index.hex(i) for i in range(window.index.distinct)}
    assert "#102A54" in hexes and "#FFFFFF" in hexes
    junk = tmp_path / "junk.psd"
    junk.write_bytes(b"nope")
    window.open_path(str(junk))
    deadline = time.monotonic() + 20
    while window.is_busy():
        app.processEvents()
        assert time.monotonic() < deadline
    assert window.status_label.property("error") and "Photoshop file" in window.status_label.text()


def test_a_full_page_paper_layer_is_not_a_photo(tmp_path):
    """A normal poster: flat paper over the whole page, a photo on it, a flat title block.

    Only the photo is left alone; the paper and the title are recolored (bug found by the
    regular-user review: the paper's box covered the photo, so everything counted as photo,
    and then nothing was left out at all).
    """
    path = tmp_path / "poster.psd"
    psd = PSDImage.new("RGB", (400, 300), color=(255, 255, 255))
    psd.append(psd.create_pixel_layer(Image.new("RGB", (400, 300), (244, 241, 222)), name="Paper"))
    psd.append(psd.create_pixel_layer(Image.fromarray(photo(200, 150, 5), "RGB"), name="Photo", top=100, left=150))
    psd.append(psd.create_pixel_layer(Image.new("RGB", (300, 40), (11, 19, 43)), name="Title", top=20, left=50))
    psd.save(str(path))
    document = documents.open_document(path)
    try:
        boxes = document.image_boxes(0)
        assert len(boxes) == 1 and close_to(boxes[0], (150 / 400, 100 / 300, 350 / 400, 250 / 300))
        page = document.render(0)
        left_out = designer.photo_boxes(page, boxes)
        assert left_out == boxes
        mask = designer.boxes_mask(page.shape[:2], left_out)
        assert 0.2 < mask.mean() < 0.3  # the photo, not the whole page
    finally:
        document.close()
