"""Look and feel: the Colorroom colors, fonts, app icon and the Qt style sheet."""

from __future__ import annotations

import tempfile
from pathlib import Path

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import (
    QBrush,
    QColor,
    QConicalGradient,
    QFont,
    QFontDatabase,
    QIcon,
    QPainter,
    QPainterPath,
    QPalette,
    QPixmap,
)
from PySide6.QtWidgets import QApplication

from .. import __version__

# The palette of the app itself, taken from Colorroom 1.7.
PAPER = "#f7f6f2"
WHITE = "#fffefa"
INK = "#211d26"
MUTED = "#635c69"
LINE = "#dfdbe3"
ACCENT = "#5a3cb0"
ACCENT_DARK = "#472c96"
ACCENT_SOFT = "#967be1"
TINT = "#eee8fa"
TINT_STRONG = "#e4d8fa"
PANEL_TINT = "#f5f0fa"
ERROR = "#a12a45"
CHECKER_DARK = "#eeeaf0"
CHECKER_LIGHT = "#f8f6f8"
STAGE = "#f1eef5"  # the quiet backdrop behind the source image

#: Colors of the brand mark, clockwise.
MARK_COLORS = ("#eec586", "#a58bd8", "#6448ae", "#33244d")


def heading_font(point_size: float = 17) -> QFont:
    font = QFont("Georgia")
    font.setStyleHint(QFont.StyleHint.Serif)
    font.setPointSizeF(point_size)
    font.setLetterSpacing(QFont.SpacingType.PercentageSpacing, 97)
    return font


def mono_font(point_size: float = 9.5) -> QFont:
    font = QFont("Consolas")
    if not font.exactMatch():
        font = QFontDatabase.systemFont(QFontDatabase.SystemFont.FixedFont)
    font.setStyleHint(QFont.StyleHint.Monospace)
    font.setPointSizeF(point_size)
    return font


def small_font(point_size: float = 9) -> QFont:
    font = QApplication.font()
    font.setPointSizeF(point_size)
    return font


def brand_mark(size: int = 64) -> QPixmap:
    """The Colorroom mark: a rounded square split into four colored quarters."""
    pixmap = QPixmap(size, size)
    pixmap.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    # CSS: conic-gradient(from 35deg, ...) runs clockwise from 35 degrees past 12 o'clock.
    # Qt angles run counter-clockwise from 3 o'clock, so start at 90 - 35 and reverse.
    gradient = QConicalGradient(QPointF(size / 2, size / 2), 90 - 35)
    quarter = 0.25
    for position, color in enumerate(MARK_COLORS):
        start = 1 - (position + 1) * quarter
        gradient.setColorAt(max(0.0, start + 0.0001), QColor(color))
        gradient.setColorAt(min(1.0, start + quarter), QColor(color))
    path = QPainterPath()
    path.addRoundedRect(QRectF(0, 0, size, size), size * 0.3, size * 0.3)
    painter.fillPath(path, QBrush(gradient))
    painter.end()
    return pixmap


def app_icon() -> QIcon:
    icon = QIcon()
    for size in (16, 24, 32, 48, 64, 128, 256):
        icon.addPixmap(brand_mark(size))
    return icon


def checker_brush(cell: int = 10) -> QBrush:
    """The checkerboard shown behind transparent parts of an image."""
    tile = QPixmap(cell * 2, cell * 2)
    tile.fill(QColor(CHECKER_LIGHT))
    painter = QPainter(tile)
    painter.fillRect(0, 0, cell, cell, QColor(CHECKER_DARK))
    painter.fillRect(cell, cell, cell, cell, QColor(CHECKER_DARK))
    painter.end()
    return QBrush(tile)


def _asset_dir() -> Path:
    folder = Path(tempfile.gettempdir()) / f"colorroom-fable-{__version__}"
    folder.mkdir(parents=True, exist_ok=True)
    return folder


def _chevron_path() -> str:
    """A small down-arrow for combo boxes, written once as an SVG so it stays sharp."""
    target = _asset_dir() / "chevron-down.svg"
    if not target.exists():
        target.write_text(
            '<svg xmlns="http://www.w3.org/2000/svg" width="12" height="12" viewBox="0 0 12 12">'
            f'<path d="M2.5 4.5 6 8l3.5-3.5" fill="none" stroke="{MUTED}" stroke-width="1.6" '
            'stroke-linecap="round" stroke-linejoin="round"/></svg>',
            encoding="utf-8",
        )
    return target.as_posix()


def style_sheet() -> str:
    return f"""
    QMainWindow, QDialog {{ background: {PAPER}; }}
    QWidget {{ color: {INK}; }}
    QToolTip {{ background: {INK}; color: white; border: 1px solid {INK}; padding: 5px 8px; }}

    QFrame#panel {{ background: {WHITE}; border: 1px solid {LINE}; border-radius: 13px; }}
    QFrame#well {{ background: {PANEL_TINT}; border: 1px solid {LINE}; border-radius: 10px; }}
    QLabel#brand {{ font-size: 15pt; font-weight: 700; }}
    QLabel#badge {{ border: 1px solid {LINE}; border-radius: 12px; padding: 3px 11px; color: {MUTED}; font-size: 9pt; }}
    QLabel#readout {{ color: {ACCENT}; font-size: 9pt; }}
    QLabel#status {{ color: {MUTED}; font-size: 9pt; }}
    QLabel#status[error="true"] {{ color: {ERROR}; }}
    QLabel[small="true"] {{ color: {MUTED}; font-size: 9pt; }}
    QLabel[strong="true"] {{ font-weight: 600; }}
    QLabel[fieldLabel="true"] {{ font-size: 9pt; font-weight: 600; }}
    QLabel#toast {{ background: {INK}; color: white; border-radius: 9px; padding: 9px 14px; }}

    QPushButton {{ background: {WHITE}; border: 1px solid {LINE}; border-radius: 8px; padding: 6px 13px;
                   font-weight: 600; min-height: 20px; }}
    QPushButton:hover {{ background: {TINT}; }}
    QPushButton:pressed {{ background: {TINT_STRONG}; }}
    QPushButton:focus {{ border: 1px solid {ACCENT_SOFT}; }}
    QPushButton:checked {{ background: {TINT}; color: {ACCENT}; border: 1px solid {ACCENT}; }}
    QPushButton:disabled {{ color: #a9a3b0; background: {WHITE}; }}
    QPushButton[primary="true"] {{ background: {ACCENT}; border: 1px solid {ACCENT}; color: white; }}
    QPushButton[primary="true"]:hover {{ background: {ACCENT_DARK}; border: 1px solid {ACCENT_DARK}; }}
    QPushButton[primary="true"]:disabled {{ background: #b9aedb; border: 1px solid #b9aedb; color: #f4f0fb; }}
    QPushButton[link="true"] {{ background: transparent; border: 1px solid transparent; color: {ACCENT}; font-weight: 700; }}
    QPushButton[link="true"]:hover {{ background: {TINT}; }}
    QPushButton::menu-indicator {{ image: none; width: 0px; }}

    QComboBox, QLineEdit, QSpinBox {{ background: {WHITE}; border: 1px solid {LINE}; border-radius: 8px;
                                      padding: 5px 9px; min-height: 20px;
                                      selection-background-color: {ACCENT}; selection-color: white; }}
    QComboBox:focus, QLineEdit:focus, QSpinBox:focus {{ border: 1px solid {ACCENT_SOFT}; }}
    QComboBox:disabled, QLineEdit:disabled, QSpinBox:disabled {{ color: #a9a3b0; }}
    QLineEdit[invalid="true"], QSpinBox[invalid="true"] {{ border: 1px solid {ERROR}; }}
    QSpinBox::up-button, QSpinBox::down-button {{ width: 0px; height: 0px; border: none; }}
    QComboBox::drop-down {{ border: none; width: 26px; }}
    QComboBox::down-arrow {{ image: url("{_chevron_path()}"); width: 12px; height: 12px; }}
    QComboBox QAbstractItemView {{ background: {WHITE}; border: 1px solid {LINE}; padding: 4px; outline: 0;
                                   selection-background-color: {TINT}; selection-color: {INK}; }}

    QSlider::groove:horizontal {{ height: 4px; background: {LINE}; border-radius: 2px; }}
    QSlider::sub-page:horizontal {{ background: {ACCENT}; border-radius: 2px; }}
    QSlider::sub-page:horizontal:disabled {{ background: #b9aedb; }}
    QSlider::handle:horizontal {{ width: 14px; height: 14px; margin: -7px 0; border-radius: 9px;
                                  background: {ACCENT}; border: 2px solid {WHITE}; }}
    QSlider::handle:horizontal:hover {{ background: {ACCENT_DARK}; }}
    QSlider::handle:horizontal:disabled {{ background: #b9aedb; }}

    QTableView {{ background: {WHITE}; border: none; outline: 0; gridline-color: transparent;
                  selection-background-color: {TINT}; selection-color: {INK}; }}
    QHeaderView {{ background: {WHITE}; }}
    QHeaderView::section {{ background: {WHITE}; color: {MUTED}; font-size: 9pt; font-weight: 700;
                            border: none; border-bottom: 1px solid {LINE}; padding: 8px 10px; }}
    QTableCornerButton::section {{ background: {WHITE}; border: none; }}

    QScrollArea {{ background: transparent; border: none; }}
    QScrollBar:vertical {{ background: transparent; width: 12px; margin: 2px; }}
    QScrollBar::handle:vertical {{ background: #d2cbdb; border-radius: 4px; min-height: 32px; }}
    QScrollBar::handle:vertical:hover {{ background: #b9aedb; }}
    QScrollBar:horizontal {{ background: transparent; height: 12px; margin: 2px; }}
    QScrollBar::handle:horizontal {{ background: #d2cbdb; border-radius: 4px; min-width: 32px; }}
    QScrollBar::add-line, QScrollBar::sub-line {{ width: 0px; height: 0px; }}
    QScrollBar::add-page, QScrollBar::sub-page {{ background: transparent; }}

    QMenuBar {{ background: {PAPER}; padding: 2px 6px; }}
    QMenuBar::item {{ background: transparent; padding: 5px 10px; border-radius: 6px; }}
    QMenuBar::item:selected {{ background: {TINT}; }}
    QMenu {{ background: {WHITE}; border: 1px solid {LINE}; padding: 6px; }}
    QMenu::item {{ padding: 6px 30px 6px 14px; border-radius: 6px; }}
    QMenu::item:selected {{ background: {TINT}; color: {INK}; }}
    QMenu::item:disabled {{ color: #a9a3b0; }}
    QMenu::separator {{ height: 1px; background: {LINE}; margin: 5px 8px; }}

    QProgressBar {{ border: 1px solid {LINE}; border-radius: 5px; background: {WHITE}; max-height: 8px; min-height: 8px; }}
    QProgressBar::chunk {{ background: {ACCENT}; border-radius: 4px; }}
    QStatusBar {{ background: {PAPER}; color: {MUTED}; }}
    QStatusBar::item {{ border: none; }}
    QSplitter::handle {{ background: transparent; }}
    """


def apply_theme(app: QApplication) -> None:
    """Give the whole application the Colorroom look."""
    app.setStyle("Fusion")
    font = app.font()
    font.setPointSizeF(10)
    app.setFont(font)
    palette = QPalette()
    palette.setColor(QPalette.ColorRole.Window, QColor(PAPER))
    palette.setColor(QPalette.ColorRole.WindowText, QColor(INK))
    palette.setColor(QPalette.ColorRole.Base, QColor(WHITE))
    palette.setColor(QPalette.ColorRole.AlternateBase, QColor(PANEL_TINT))
    palette.setColor(QPalette.ColorRole.Text, QColor(INK))
    palette.setColor(QPalette.ColorRole.Button, QColor(WHITE))
    palette.setColor(QPalette.ColorRole.ButtonText, QColor(INK))
    palette.setColor(QPalette.ColorRole.Highlight, QColor(ACCENT))
    palette.setColor(QPalette.ColorRole.HighlightedText, QColor("white"))
    palette.setColor(QPalette.ColorRole.ToolTipBase, QColor(INK))
    palette.setColor(QPalette.ColorRole.ToolTipText, QColor("white"))
    palette.setColor(QPalette.ColorRole.PlaceholderText, QColor("#9a93a3"))
    app.setPalette(palette)
    app.setStyleSheet(style_sheet())
    app.setWindowIcon(app_icon())
