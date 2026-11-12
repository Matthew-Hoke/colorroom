"""Start Colorroom FABLE.

    python -m colorroom                  open the window (with the sample image)
    python -m colorroom photo.jpg        open the window on an image
    python -m colorroom analyze ...      command-line analysis, no window (see cli.py)

For checking an installation there is also:
    python -m colorroom --screenshot out.png [photo.jpg]
which draws the window into an image file without showing it, then exits.
"""

from __future__ import annotations

import sys


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv if argv is None else argv)
    if len(argv) > 1 and argv[1] in ("analyze", "--help", "-h", "--version"):
        from .cli import main as cli_main

        return cli_main(argv[1:])
    return run_gui(argv)


def run_gui(argv: list[str]) -> int:
    from PySide6.QtCore import Qt, QTimer
    from PySide6.QtWidgets import QApplication

    from . import APP_NAME
    from .ui.main_window import MainWindow
    from .ui.theme import apply_theme

    screenshot = None
    if "--screenshot" in argv:
        position = argv.index("--screenshot")
        screenshot = argv[position + 1] if position + 1 < len(argv) else "colorroom-fable.png"
        del argv[position : position + 2]

    if sys.platform == "win32":
        # Give the app its own taskbar identity (and icon) instead of Python's.
        try:
            import ctypes

            ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("ColorroomFABLE.App")
        except Exception:
            pass

    app = QApplication(argv)
    app.setApplicationName(APP_NAME)
    app.setOrganizationName("ColorroomFABLE")
    apply_theme(app)
    window = MainWindow(persist=screenshot is None)
    if screenshot is None:
        window.show_initial()
    else:
        window.setAttribute(Qt.WidgetAttribute.WA_DontShowOnScreen, True)
        window.resize(1460, 940)
        window.show()
    paths = [argument for argument in app.arguments()[1:] if not argument.startswith("-")]
    if paths:
        window.open_path(paths[0])
    else:
        window.open_sample()

    if screenshot is not None:
        waited = {"ticks": 0}

        def check() -> None:
            waited["ticks"] += 1
            if window.is_busy() and waited["ticks"] < 1200:
                QTimer.singleShot(50, check)
                return
            if window.index is not None:
                window.inspect(int(window.index.frequency_order[0]))
            QTimer.singleShot(300, finish)

        def finish() -> None:
            saved = window.grab().save(screenshot)
            app.exit(0 if saved and window.index is not None else 1)

        QTimer.singleShot(50, check)
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
