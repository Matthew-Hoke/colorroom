"""Render the app off-screen and save screenshots to docs/screenshots.

Run from the project folder:   .venv\\Scripts\\python tools\\screenshots.py

No window appears. It is a quick way to check the look after a change, and it
exercises most of the interface (open, palette styles, picking, inspecting, filtering).
"""

from __future__ import annotations

import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from PySide6.QtCore import QPointF, Qt  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from colorroom.ui.main_window import MainWindow  # noqa: E402
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
    window.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
