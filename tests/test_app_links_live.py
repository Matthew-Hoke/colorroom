"""End to end through the Your project tab with the real design programs (made by Opus 5.5).

Skipped unless COLORROOM_LIVE=1 and the program is installed. Each test copies a file from
`test-kit/real/` (the user's own files, git-ignored and never written to) into a temporary
folder, opens it in the tab, keeps the suggested roles, presses Export, and checks the program's
report: every changed color read back from the saved copy, nothing else changed.
"""

from __future__ import annotations

import os
import shutil
import time
from pathlib import Path

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

pytest.importorskip("PySide6")

LIVE = os.environ.get("COLORROOM_LIVE") == "1"
REAL = Path(__file__).resolve().parent.parent / "test-kit" / "real"
CASES = [
    pytest.param("photoshop-real.psd", "Photoshop", ".psd", marks=pytest.mark.photoshop),
    pytest.param("illustrator-real.ai", "Illustrator", ".ai", marks=pytest.mark.illustrator),
    pytest.param("indesign-real.idml", "InDesign", ".indd", marks=pytest.mark.indesign),
]


@pytest.fixture(scope="module")
def app():
    from PySide6.QtWidgets import QApplication

    from colorroom.ui.theme import apply_theme

    application = QApplication.instance() or QApplication([])
    apply_theme(application)
    return application


def wait(app, page, timeout):
    deadline = time.monotonic() + timeout
    while page.is_busy() and time.monotonic() < deadline:
        app.processEvents()
        time.sleep(0.02)
    app.processEvents()
    assert not page.is_busy(), "the program did not finish in time"


@pytest.mark.skipif(not LIVE, reason="live test: set COLORROOM_LIVE=1")
@pytest.mark.parametrize("name, program, suffix", CASES)
def test_export_to_the_program_from_your_project(app, tmp_path, name, program, suffix):
    from colorroom.core.app_links import program_links
    from colorroom.core.bridges.base import file_hash
    from colorroom.ui.main_window import MainWindow

    original = REAL / name
    if not original.is_file():
        pytest.skip(f"put a copy of a real file at test-kit/real/{name}")
    links = program_links(start=True)
    if not links[original.suffix.lstrip(".")].available():
        pytest.skip(f"{program} is not installed")
    before = file_hash(original)
    source = tmp_path / name
    shutil.copyfile(original, source)

    window = MainWindow(persist=False)
    try:
        # A palette of the person's own, as when they tried the yellow and navy one.
        window.studio.add_colors(["#FFCC00", "#0B295F", "#D5A100", "#0072BB", "#FFFFFF", "#82BCE2"], quiet=True)
        app.processEvents()
        page = window.project
        page.set_app_links(links)
        page.confirm_app = lambda program_name, file_name: True
        page.open_file(source)
        wait(app, page, 300)
        assert page._layers is not None, "the file did not open"
        print(f"{name}: palette {page._colors}, main colors {page._layers.colors}, targets {page.targets()}")
        assert page.export_button.text() == f"Export to {program}"
        page.export_primary()
        wait(app, page, 600)
        report = page.last_report
        assert report is not None, window.status_label.text()
        print(report.summary())
        assert report.ok, report.summary()
        saved = Path(report.saved)
        assert saved.is_file() and saved.parent == tmp_path and saved.suffix == suffix
        assert report.changed
    finally:
        window.close()
        app.processEvents()
    assert file_hash(original) == before
