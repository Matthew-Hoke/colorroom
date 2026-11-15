"""Tests for the links between Your project and the design programs (made by Opus 5.5).

No design program is used here: a stand-in bridge records what it is asked. The live round
trips are in tests/test_bridge_*.py and tests/test_app_links_live.py.
"""

from __future__ import annotations

import os
import time
from pathlib import Path

import numpy as np
import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

pytest.importorskip("psd_tools")
pytest.importorskip("PySide6")

from PIL import Image  # noqa: E402
from psd_tools import PSDImage  # noqa: E402

from colorroom.core import app_links, file_export  # noqa: E402
from colorroom.core.bridges import ApplyReport, BridgeError  # noqa: E402
from colorroom.core.file_export import Recipe  # noqa: E402
from colorroom.core.manifest import Manifest, ManifestLayer  # noqa: E402
from colorroom.core.project import ExportRequest  # noqa: E402

NAVY, WHITE, BLACK, YELLOW = "#0B295F", "#FFFFFF", "#000000", "#E6C828"


def test_exact_colors_move_exactly_as_the_preview_moves_them():
    recipe = Recipe([BLACK, YELLOW], [WHITE, NAVY])
    mapping = app_links.exact_mapping(["#000000", "#E6C828", "#e6c828", "#E6C829"], recipe)
    assert mapping["#000000"] == WHITE and mapping["#E6C828"] == NAVY
    assert mapping["#E6C829"] == NAVY  # one level off the main color still lands on its target
    strip = np.array([[[0, 0, 0, 255], [230, 200, 40, 255]]], dtype=np.uint8)
    preview = file_export.recolor_pixels(strip, recipe)
    assert mapping["#000000"] == "#%02X%02X%02X" % tuple(preview[0, 0, :3])
    assert app_links.exact_mapping(["#123456"], Recipe([BLACK], [None])) == {}  # nothing moves
    assert app_links.exact_mapping([], recipe) == {}
    boxed = Recipe([BLACK, YELLOW], [WHITE, NAVY], [(0, 0, 1, 1)])  # boxes are about pictures only
    assert app_links.exact_mapping(["#000000"], boxed) == {"#000000": WHITE}


class FakeBridge:
    """Records what the program would be asked."""

    def __init__(self, colors, installed=True):
        self.colors, self.installed, self.applied = colors, installed, []

    def available(self):
        return self.installed

    def read(self, path, *, work_dir=None, preview=True, leave_open=False):
        layer = ManifestLayer(1, "Title", "Title", "text", True, False, None, [("text", color) for color in self.colors])
        return Manifest(Path(str(path)), "Photoshop", "27", os.path.basename(str(path)), "RGB", 10, 10, None, [layer], [])

    def apply(self, source_path, mapping, save_as=None, *, option="A", **options):
        self.applied.append((str(source_path), dict(mapping), option))
        report = ApplyReport("Photoshop", str(source_path), str(source_path).replace(".psd", " - Colorroom A.psd"), dict(mapping))
        return report


def request_for(path, recipe):
    return ExportRequest(str(path), "psd", 0, recipe, path.with_name("x - Colorroom.psd"), (WHITE, NAVY), {})


def test_a_link_reads_exact_colors_then_lets_the_program_write(tmp_path):
    bridge = FakeBridge(["#000000", "#E6C828", "#FF0000"])
    link = app_links.ProgramLink("Photoshop", lambda: bridge, work_dir=tmp_path)
    assert link.available()
    steps = []
    report = link.export(request_for(tmp_path / "poster.psd", Recipe([BLACK, YELLOW], [WHITE, NAVY])), lambda f, t: steps.append(t))
    (path, mapping, option), = bridge.applied
    assert path.endswith("poster.psd") and option == "A"
    assert mapping["#000000"] == WHITE and mapping["#E6C828"] == NAVY
    assert report.saved.endswith("poster - Colorroom A.psd")
    assert any("exact colors" in step for step in steps) and any("changing" in step for step in steps)


def test_nothing_to_change_says_so_and_asks_nothing(tmp_path):
    bridge = FakeBridge(["#336699"])
    link = app_links.ProgramLink("Photoshop", lambda: bridge, work_dir=tmp_path)
    with pytest.raises(BridgeError, match="None of the exact colors"):
        link.export(request_for(tmp_path / "poster.psd", Recipe([BLACK], [None])))
    assert bridge.applied == []


def test_a_program_that_is_not_installed_is_not_offered(tmp_path):
    assert not app_links.ProgramLink("Photoshop", lambda: FakeBridge([], installed=False)).available()

    def broken():
        raise RuntimeError("no COM here")

    assert not app_links.ProgramLink("Photoshop", broken).available()
    links = app_links.program_links(start=False)
    assert set(links) == {"psd", "psb", "ai", "indd", "idml"}
    assert links["psd"] is links["psb"] and links["indd"] is links["idml"]
    assert callable(getattr(links["indd"], "preview", None)) and not hasattr(links["psd"], "preview")


# ======================================================================================
# In the window: the button, the question before starting a program, the report
# ======================================================================================


@pytest.fixture(scope="module")
def app():
    from PySide6.QtWidgets import QApplication

    from colorroom.ui.theme import apply_theme

    application = QApplication.instance() or QApplication([])
    apply_theme(application)
    return application


@pytest.fixture
def window(app):
    from colorroom.ui.main_window import MainWindow

    main = MainWindow(persist=False)  # never touch the user's settings; no real programs
    main.resize(1400, 900)
    main.show()
    app.processEvents()
    yield main
    main.close()
    for _ in range(5):
        app.processEvents()


def poster_psd(path):
    psd = PSDImage.new("RGB", (300, 200), color=(255, 255, 255))
    psd.append(psd.create_pixel_layer(Image.new("RGB", (300, 200), (0, 0, 0)), name="Paper"))
    psd.append(psd.create_pixel_layer(Image.new("RGB", (140, 80), (230, 200, 40)), name="Block", top=40, left=60))
    psd.save(str(path))
    return path


def wait(app, page, timeout=30):
    deadline = time.monotonic() + timeout
    while page.is_busy() and time.monotonic() < deadline:
        app.processEvents()
        time.sleep(0.01)
    app.processEvents()


def test_export_to_photoshop_asks_first_and_uses_the_exact_colors(app, window, tmp_path):
    page = window.project
    bridge = FakeBridge(["#000000", "#E6C828"])
    page.set_app_links({"psd": app_links.ProgramLink("Photoshop", lambda: bridge, work_dir=tmp_path)})
    asked = []
    page.confirm_app = lambda program, name: asked.append((program, name)) or False  # the person says Cancel
    page.open_file(poster_psd(tmp_path / "poster.psd"))
    wait(app, page)
    assert page.app_found() and page.export_button.text().startswith("Export to Photoshop")
    page.export_primary()
    wait(app, page)
    assert asked == [("Photoshop", "poster.psd")] and bridge.applied == []  # cancelled: nothing sent

    page.confirm_app = lambda program, name: True
    page.export_primary()
    wait(app, page)
    assert len(bridge.applied) == 1
    _, mapping, _ = bridge.applied[0]
    assert mapping and set(mapping) <= {"#000000", "#E6C828"}
    assert page.last_report is not None and page.last_report.saved.endswith("poster - Colorroom A.psd")
