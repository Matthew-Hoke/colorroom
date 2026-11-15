"""The page strip: thumbnails of every page of a document, down the left side.

Each thumbnail carries a mini strip of that page's main colors, so you can see where the
palette changes without opening every page. It hides itself for one-page files.
"""

from __future__ import annotations

from typing import Optional, Sequence

import numpy as np
from PySide6.QtCore import QRectF, QSize, Qt, Signal
from PySide6.QtGui import QColor, QFont, QImage, QPainter, QPainterPath, QPen, QPixmap
from PySide6.QtWidgets import (
    QAbstractItemView,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QSpinBox,
    QStyle,
    QStyledItemDelegate,
    QVBoxLayout,
    QWidget,
)

from . import theme
from .widgets import heading, small_label

_SUMMARY = Qt.ItemDataRole.UserRole
_PIXMAP = Qt.ItemDataRole.UserRole + 1
WIDTH = 178
THUMB_HEIGHT = 96


class _PageCell(QStyledItemDelegate):
    HEIGHT = THUMB_HEIGHT + 46

    def sizeHint(self, option, index) -> QSize:
        return QSize(WIDTH - 30, self.HEIGHT)

    def paint(self, painter: QPainter, option, index) -> None:
        rect = QRectF(option.rect).adjusted(3, 3, -3, -3)
        selected = bool(option.state & QStyle.StateFlag.State_Selected)
        hovered = bool(option.state & QStyle.StateFlag.State_MouseOver)
        painter.save()
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        if selected or hovered:
            painter.setPen(QPen(QColor(theme.ACCENT if selected else theme.LINE), 2 if selected else 1))
            painter.setBrush(QColor(theme.TINT if selected else theme.PANEL_TINT))
            painter.drawRoundedRect(rect, 8, 8)
        area = QRectF(rect.x() + 7, rect.y() + 7, rect.width() - 14, THUMB_HEIGHT - 8)
        pixmap: Optional[QPixmap] = index.data(_PIXMAP)
        summary = index.data(_SUMMARY)
        if pixmap is not None and not pixmap.isNull():
            scale = min(area.width() / pixmap.width(), area.height() / pixmap.height())
            shown = QRectF(0, 0, pixmap.width() * scale, pixmap.height() * scale)
            shown.moveCenter(area.center())
            painter.fillRect(shown, theme.checker_brush(6))
            painter.drawPixmap(shown, pixmap, QRectF(pixmap.rect()))
            painter.setPen(QPen(QColor(128, 120, 140, 110), 1))
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.drawRect(shown)
        else:  # not looked at yet
            painter.setPen(QPen(QColor(theme.LINE), 1, Qt.PenStyle.DashLine))
            painter.setBrush(QColor(theme.PANEL_TINT))
            painter.drawRoundedRect(area, 6, 6)
            painter.setFont(theme.small_font(8))
            painter.setPen(QColor(theme.MUTED))
            painter.drawText(area, Qt.AlignmentFlag.AlignCenter, "Looking…")
        strip = QRectF(area.x(), area.bottom() + 6, area.width(), 9)
        if summary is not None and summary.colors:
            shape = QPainterPath()
            shape.addRoundedRect(strip, 4, 4)
            painter.setClipPath(shape)
            x = strip.x()
            total = sum(summary.shares) or 1.0
            for color, share in zip(summary.colors, summary.shares):
                width = max(3.0, strip.width() * share / total)
                painter.fillRect(QRectF(x, strip.y(), width + 1, strip.height()), QColor(color))
                x += width
            painter.setClipping(False)
        font = theme.small_font(8.5)
        font.setWeight(QFont.Weight.DemiBold if selected else QFont.Weight.Normal)
        painter.setFont(font)
        painter.setPen(QColor(theme.ACCENT if selected else theme.INK))
        label = painter.fontMetrics().elidedText(index.data(Qt.ItemDataRole.DisplayRole) or "", Qt.TextElideMode.ElideRight, int(area.width()))
        painter.drawText(QRectF(area.x(), strip.bottom() + 3, area.width(), 18), Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter, label)
        painter.restore()


class PageStrip(QWidget):
    pageChosen = Signal(int)  # 0-based page the user asked for
    overviewRequested = Signal()  # "Whole document" was clicked

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.setObjectName("pageStrip")
        self.setFixedWidth(WIDTH)
        self._current = -1
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 4, 4, 0)
        layout.setSpacing(7)
        self.title = heading("Pages", 13)
        layout.addWidget(self.title)
        nav = QHBoxLayout()
        nav.setSpacing(5)
        self.previous_button = QPushButton("‹")
        self.next_button = QPushButton("›")
        for button, tip in ((self.previous_button, "Previous page (Page Up)"), (self.next_button, "Next page (Page Down)")):
            button.setFixedWidth(34)
            button.setToolTip(tip)
        self.jump = QSpinBox()
        self.jump.setRange(1, 1)
        self.jump.setToolTip("Type a page number and press Enter")
        self.jump.setKeyboardTracking(False)
        self.count_label = QLabel("of 1")
        self.count_label.setProperty("small", True)
        nav.addWidget(self.previous_button)
        nav.addWidget(self.jump, 1)
        nav.addWidget(self.count_label)
        nav.addWidget(self.next_button)
        layout.addLayout(nav)
        self.list = QListWidget()
        self.list.setItemDelegate(_PageCell(self.list))
        self.list.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.list.setVerticalScrollMode(QAbstractItemView.ScrollMode.ScrollPerPixel)
        self.list.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.list.setMouseTracking(True)
        layout.addWidget(self.list, 1)
        self.overview_button = QPushButton("Whole document")
        self.overview_button.setToolTip("One palette for all pages: which colors repeat, which appear once, which are nearly the same")
        layout.addWidget(self.overview_button)
        self.note = small_label()
        layout.addWidget(self.note)
        self.hide()

        self.list.currentRowChanged.connect(self._row_changed)
        self.previous_button.clicked.connect(lambda: self.step(-1))
        self.next_button.clicked.connect(lambda: self.step(1))
        self.jump.valueChanged.connect(lambda value: self._ask(value - 1))
        self.overview_button.clicked.connect(self.overviewRequested)

    # ----------------------------------------------------------------------------------

    def page_count(self) -> int:
        return self.list.count()

    def current(self) -> int:
        return self._current

    def set_pages(self, names: Sequence[str], word: str = "Pages") -> None:
        """Show these pages (hidden when there is only one)."""
        self.list.blockSignals(True)
        self.list.clear()
        for name in names:
            item = QListWidgetItem(name)
            item.setToolTip(name)
            self.list.addItem(item)
        self.list.blockSignals(False)
        self._current = -1
        self.title.setText(word)
        self.jump.blockSignals(True)
        self.jump.setRange(1, max(1, len(names)))
        self.jump.blockSignals(False)
        self.count_label.setText(f"of {len(names)}")
        self.note.setText("")
        self.setVisible(len(names) > 1)

    def clear(self) -> None:
        self.set_pages(())

    def set_summary(self, page: int, summary) -> None:
        """Fill in a page's thumbnail and color strip once it has been looked at."""
        item = self.list.item(page)
        if item is None:
            return
        rgba = np.ascontiguousarray(summary.thumbnail)
        height, width = rgba.shape[:2]
        image = QImage(rgba.data, width, height, 4 * width, QImage.Format.Format_RGBA8888).copy()
        item.setData(_PIXMAP, QPixmap.fromImage(image))
        item.setData(_SUMMARY, summary)
        done = sum(1 for row in range(self.list.count()) if self.list.item(row).data(_SUMMARY) is not None)
        self.note.setText("" if done == self.list.count() else f"Looked at {done} of {self.list.count()}…")

    def summary(self, page: int):
        item = self.list.item(page)
        return item.data(_SUMMARY) if item is not None else None

    def set_current(self, page: int) -> None:
        """Mark a page as the one being shown, without announcing it as a click."""
        self._current = page
        for widget in (self.list, self.jump):
            widget.blockSignals(True)
        self.list.setCurrentRow(page)
        if 0 <= page < self.list.count():
            self.list.scrollToItem(self.list.item(page), QAbstractItemView.ScrollHint.EnsureVisible)
            self.jump.setValue(page + 1)
        for widget in (self.list, self.jump):
            widget.blockSignals(False)
        self.previous_button.setEnabled(page > 0)
        self.next_button.setEnabled(page < self.list.count() - 1)

    def step(self, by: int) -> None:
        self._ask(self._current + by)

    def _row_changed(self, row: int) -> None:
        self._ask(row)

    def _ask(self, page: int) -> None:
        if 0 <= page < self.list.count() and page != self._current:
            self.pageChosen.emit(page)
