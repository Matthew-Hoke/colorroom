"""Tests for documents with pages: PDF, Illustrator files, images with several frames,
the page strip and the whole-document view."""

from __future__ import annotations

import os
import time

import numpy as np
import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")  # no window appears while testing

pytest.importorskip("pypdfium2")
pytest.importorskip("PySide6")

from PIL import Image  # noqa: E402
from PySide6.QtGui import QGuiApplication  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from colorroom.core import documents  # noqa: E402
from colorroom.core.colorspace import oklab_distance  # noqa: E402
from colorroom.core.documents import DocumentError  # noqa: E402
from colorroom.core.index import MAX_PIXELS, ImageError  # noqa: E402
from colorroom.ui.document_view import page_list  # noqa: E402
from colorroom.ui.main_window import TAB_PALETTE, MainWindow  # noqa: E402
from colorroom.ui.theme import apply_theme  # noqa: E402

from make_pdf import BRAND_GUIDE, build_pdf, write_pdf  # noqa: E402

NAVY, ORANGE, NAVY_OFF, GREEN = "#102A54", "#E0452F", "#183260", "#2E8B57"


@pytest.fixture
def guide(tmp_path):
    return write_pdf(tmp_path / "brand guide.pdf", BRAND_GUIDE)


# ======================================================================================
# Engine
# ======================================================================================


def test_pdf_pages_render_at_the_requested_size(guide):
    document = documents.open_document(guide)
    try:
        assert (document.kind, document.page_count) == ("PDF", 4)
        assert document.page_names == ["Page 1", "Page 2", "Page 3", "Page 4"]
        page = document.render(0)
        assert page.shape == (1334, 2000, 4) and page.dtype == np.uint8
        assert int(page[..., 3].min()) == 255
        assert tuple(page[660, 460][:3]) == (0x10, 0x2A, 0x54)  # inside the navy block
        assert tuple(page[660, 1460][:3]) == (0xE0, 0x45, 0x2F)  # inside the orange block
        thumbnail = document.thumbnail(1)
        assert max(thumbnail.shape[:2]) == documents.THUMBNAIL_LONGEST
    finally:
        document.close()


def test_exact_colors_are_read_from_the_file(tmp_path, guide):
    document = documents.open_document(guide)
    try:
        assert document.vector_colors(0) == ["#FFFFFF", NAVY, ORANGE]
        assert document.vector_colors(2) == ["#FFFFFF", NAVY_OFF]
    finally:
        document.close()
    mixed = write_pdf(tmp_path / "mixed.pdf", [[("stroke", 0, 0.5, 1, 20, 20, 100, 100), ("cmyk", 0, 1, 1, 0, 150, 20, 100, 100)]])
    document = documents.open_document(mixed)
    try:
        colors = document.vector_colors(0)
        assert "#0080FF" in colors  # a stroke counts too
        red = next(color for color in colors if color != "#0080FF")
        assert oklab_distance(red, "#E8212A") < 0.12  # CMYK red arrives converted, so only roughly
    finally:
        document.close()


def test_page_labels_become_page_names(tmp_path):
    document = documents.open_document(write_pdf(tmp_path / "named.pdf", BRAND_GUIDE[:2], labels=True))
    try:
        assert document.page_names == ["Page 1 · A", "Page 2 · B"]
    finally:
        document.close()


def test_illustrator_files_open_through_their_pdf_part(tmp_path):
    art = tmp_path / "logo.ai"
    art.write_bytes(build_pdf(BRAND_GUIDE[:3]))
    document = documents.open_document(art)
    try:
        assert document.kind == "Illustrator" and document.page_names == ["Artboard 1", "Artboard 2", "Artboard 3"]
        assert document.vector_colors(0) == ["#FFFFFF", NAVY, ORANGE]
    finally:
        document.close()
    old = tmp_path / "old.ai"
    old.write_bytes(b"%!PS-Adobe-3.0\n%%Creator: Adobe Illustrator(R) 8.0\n")
    with pytest.raises(DocumentError, match="saved without PDF compatibility"):
        documents.open_document(old)


def test_broken_documents_give_plain_messages(tmp_path):
    with pytest.raises(DocumentError, match="could not be found"):
        documents.open_document(tmp_path / "missing.pdf")
    junk = tmp_path / "junk.pdf"
    junk.write_bytes(b"this is not a pdf at all")
    with pytest.raises(DocumentError, match="could not be opened as a PDF"):
        documents.open_document(junk)
    cut = tmp_path / "cut.pdf"
    cut.write_bytes(build_pdf(BRAND_GUIDE)[:120])
    with pytest.raises(DocumentError):
        documents.open_document(cut)
    with pytest.raises(DocumentError, match="opens PDF, Illustrator and Photoshop"):
        documents.open_document(__file__)
    assert issubclass(DocumentError, ImageError)  # the window shows these like any other image problem
    assert documents.is_document("Guide.PDF") and documents.is_document("x.ai") and not documents.is_document("x.png")


def test_huge_pages_stay_under_the_pixel_limit(tmp_path):
    document = documents.open_document(write_pdf(tmp_path / "banner.pdf", [[(1, 0, 0, 0, 0, 14000, 90)]], size=(14000, 100)))
    try:
        page = document.render(0, longest=20000)  # asking for more than is allowed
        assert page.shape[0] * page.shape[1] <= MAX_PIXELS
    finally:
        document.close()


def test_page_summaries_and_the_whole_document_palette(guide):
    document = documents.open_document(guide)
    try:
        seen = []
        summaries = documents.summarize(document, each=lambda page, summary: seen.append(page))
    finally:
        document.close()
    assert seen == [0, 1, 2, 3] and len(summaries) == 4
    assert summaries[0].colors[0] == "#FFFFFF" and set(summaries[0].colors) == {"#FFFFFF", NAVY, ORANGE}
    assert sum(summaries[0].shares) == pytest.approx(1.0)
    assert summaries[2].defined == ["#FFFFFF", NAVY_OFF]

    merged = documents.merge_document_colors(summaries, threshold=0.03)
    by_color = {item.color: item for item in merged}
    assert merged[0].color == "#FFFFFF" and merged[0].pages == [0, 1, 2, 3]
    assert by_color[NAVY].pages == [0, 1, 3] and by_color[ORANGE].pages == [0, 1]
    assert by_color[NAVY_OFF].pages == [2] and by_color[GREEN].pages == [3]
    assert all(item.defined for item in merged)
    close = documents.near_duplicates(merged, 0.03)
    assert [(first, second) for first, second, _ in close] == [(NAVY, NAVY_OFF)]  # the slip on page 3

    loose = documents.merge_document_colors(summaries, threshold=0.05)  # now the two navies count as one
    navy = next(item for item in loose if NAVY in item.variants)
    assert navy.pages == [0, 1, 2, 3] and set(navy.variants) == {NAVY, NAVY_OFF} and navy.color == NAVY
    assert documents.near_duplicates(loose, 0.05) == []
    strict = documents.merge_document_colors(summaries, threshold=0.0)
    assert len(strict) == 5


def test_cancelling_the_page_pass_stops_it(guide):
    from colorroom.core.index import AnalysisCancelled

    document = documents.open_document(guide)
    try:
        done = []
        with pytest.raises(AnalysisCancelled):
            documents.summarize(document, cancelled=lambda: len(done) >= 2, each=lambda page, summary: done.append(page))
        assert done == [0, 1]
    finally:
        document.close()


def test_frames_of_an_image_are_pages(tmp_path):
    path = tmp_path / "anim.gif"
    frames = [Image.new("RGB", (40, 30), color) for color in ("#FF0000", "#00FF00", "#0000FF")]
    frames[0].save(path, save_all=True, append_images=frames[1:], duration=80)
    document = documents.FramesDocument(str(path), 3)
    assert document.page_names == ["Frame 1", "Frame 2", "Frame 3"]
    assert tuple(document.render(1)[10, 10][:3]) == (0, 255, 0)
    assert documents.FramesDocument("scan.tiff", 2).page_names == ["Page 1", "Page 2"]


def test_page_list_reads_like_page_numbers():
    assert page_list([0, 1, 2, 5]) == "1-3, 6"
    assert page_list([3]) == "4" and page_list([]) == ""
    assert page_list([0, 2, 4, 5]) == "1, 3, 5-6"


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
    yield window
    window.close()
    settle(app)


def test_opening_a_pdf_shows_page_one_and_fills_the_strip(app, window, guide):
    assert not window.page_strip.isVisible()
    window.open_path(str(guide))
    wait_idle(app, window)
    assert window.document is not None and window.document.page_count == 4 and window.page == 0
    assert window.page_strip.isVisible() and window.page_strip.page_count() == 4 and window.page_strip.current() == 0
    assert window.windowTitle().startswith("brand guide.pdf · Page 1")
    subtitle = window.source_panel.subtitle.text()
    assert "Page 1 of 4" in subtitle and f"exact colors in the file: #FFFFFF, {NAVY}, {ORANGE}" in subtitle
    assert window.index.distinct >= 3 and window.stem == "brand-guide-page-1"
    assert all(summary is not None for summary in window._summaries)  # every page was looked at
    assert window.page_strip.summary(2).colors[0] in ("#FFFFFF", NAVY_OFF) and window.page_strip.note.text() == ""
    assert not window.page_strip.previous_button.isEnabled() and window.page_strip.next_button.isEnabled()
    assert not window.grab().isNull()
    assert not window.status_label.property("error")


def test_moving_between_pages(app, window, guide):
    window.open_path(str(guide))
    wait_idle(app, window)
    window.page_strip.next_button.click()
    wait_idle(app, window)
    assert window.page == 1 and "Page 2 of 4" in window.source_panel.subtitle.text()
    window.page_strip.jump.setValue(4)  # the jump-to-page box
    wait_idle(app, window)
    assert window.page == 3 and window.stem == "brand-guide-page-4"
    hexes = {window.index.hex(i) for i in range(window.index.distinct)}
    assert GREEN in hexes and ORANGE not in hexes
    assert not window.page_strip.next_button.isEnabled()
    window.step_page(1)  # Page Down on the last page stays put
    assert window.page == 3
    window.step_page(-1)
    wait_idle(app, window)
    assert window.page == 2
    first_index = window.index
    window.page_strip.list.setCurrentRow(3)  # clicking a thumbnail
    wait_idle(app, window)
    assert window.page == 3
    window.show_page(2)  # a page seen a moment ago comes back instantly, from memory
    assert window.index is first_index and not window.is_busy()
    window.show_tab(TAB_PALETTE)
    window.step_page(1)  # Page Down does nothing while another tab is showing
    assert window.page == 2
    assert window.studio.vision._rgba is not None  # the other tabs follow the page being shown


def test_whole_document_window(app, window, guide):
    window.show_whole_document()  # nothing open yet: a hint, no window
    assert window._document_dialog is None and "Open a PDF" in window.toast.text()
    assert not window.whole_document_action.isEnabled()
    window.open_path(str(guide))
    wait_idle(app, window)
    assert window.whole_document_action.isEnabled()
    window.page_strip.overview_button.click()
    dialog = window._document_dialog
    settle(app)
    assert dialog.isVisible() and "brand guide.pdf" in dialog.windowTitle()
    assert "5 colors across 4 pages: 1 on every page, 2 on one page only." in dialog.summary.text()
    assert f"Check: {NAVY} and {NAVY_OFF}" in dialog.warning.text()
    rows = {dialog.table.item(row, 1).text(): row for row in range(dialog.table.rowCount())}
    assert dialog.table.item(rows["#FFFFFF"], 2).text() == "every page"
    assert dialog.table.item(rows[NAVY], 3).text() == "1-2, 4"
    assert "One page only" in dialog.table.item(rows[GREEN], 4).text()
    assert "Defined in the file" in dialog.table.item(rows[NAVY], 4).text()
    assert f"Very close to {NAVY_OFF}" in dialog.table.item(rows[NAVY], 4).text()
    dialog.slider.setValue(50)  # merge near-matches
    assert dialog.table.rowCount() == 4 and "No near-duplicate" in dialog.warning.text()
    dialog.slider.setValue(30)
    dialog.table.selectRow(rows[GREEN])
    dialog.go_button.click()
    wait_idle(app, window)
    assert window.page == 3
    dialog.add_selected_button.click()
    assert window.studio.colors() == [GREEN]
    dialog.add_all_button.click()
    assert set(window.studio.colors()) == {"#FFFFFF", NAVY, ORANGE, NAVY_OFF, GREEN}
    dialog.table.clearSelection()
    dialog.copy_button.click()
    assert QGuiApplication.clipboard().text().count("#") == 5
    assert not dialog.grab().isNull()


def test_opening_something_else_closes_the_document(app, window, guide, tmp_path):
    window.open_path(str(guide))
    wait_idle(app, window)
    window.show_whole_document()
    window.open_sample()
    wait_idle(app, window)
    assert window.document is None and not window.page_strip.isVisible()
    assert not window._document_dialog.isVisible() and not window.whole_document_action.isEnabled()
    assert "exact colors" not in window.source_panel.subtitle.text()
    single = write_pdf(tmp_path / "one.pdf", BRAND_GUIDE[:1])
    window.open_path(str(single))
    wait_idle(app, window)
    assert window.document.page_count == 1 and not window.page_strip.isVisible()  # one page: no strip
    assert window.stem == "one" and "of 1" not in window.source_panel.subtitle.text()


def test_document_problems_are_explained(app, window, tmp_path):
    old = tmp_path / "old.ai"
    old.write_bytes(b"%!PS-Adobe-3.0\n")
    window.open_path(str(old))
    deadline = time.monotonic() + 20
    while window.is_busy():
        app.processEvents()
        assert time.monotonic() < deadline
    assert window.status_label.property("error") and "PDF compatibility" in window.status_label.text()
    assert window.document is None and window.index is None and not window.page_strip.isVisible()


def test_an_animated_image_gets_a_frame_strip(app, window, tmp_path):
    path = tmp_path / "anim.gif"
    frames = [Image.new("RGB", (60, 40), color) for color in ("#FF0000", "#00FF00", "#0000FF")]
    frames[0].save(path, save_all=True, append_images=frames[1:], duration=80)
    window.open_path(str(path))
    wait_idle(app, window)
    assert window.document is not None and window.document.page_count == 3
    assert window.page_strip.isVisible() and window.page_strip.title.text() == "Frames"
    assert window.index.hex(0) == "#FF0000"
    window.page_strip.next_button.click()
    wait_idle(app, window)
    assert window.page == 1 and window.index.hex(0) == "#00FF00"
    assert window.document is not None and window.page_strip.page_count() == 3  # the strip survives the page change
