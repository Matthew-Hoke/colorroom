"""The "Image colors" table: every distinct color, shown without paging.

The table is virtual: the model only answers questions about the rows on screen, so a
picture with millions of colors scrolls as smoothly as one with a dozen.
"""

from __future__ import annotations

from typing import Optional

import numpy as np
from PySide6.QtCore import QAbstractTableModel, QModelIndex, QRectF, QSize, Qt, Signal
from PySide6.QtGui import QColor, QPainter, QPen
from PySide6.QtWidgets import QAbstractItemView, QHeaderView, QStyledItemDelegate, QTableView, QWidget

from ..core.colorspace import key_to_rgb, to_hsl
from ..core.index import ColorIndex
from . import theme

COLUMNS = ("", "HEX", "RGB", "HSL", "Pixels", "Image share", "Palette")
SWATCH, HEX, RGB, HSL, PIXELS, SHARE, PICK = range(len(COLUMNS))
ROW_HEIGHT = 34
#: Narrowest comfortable width of each column, in pixels.
_BASE_WIDTHS = {SWATCH: 50, HEX: 96, RGB: 118, HSL: 138, PIXELS: 96, SHARE: 104, PICK: 92}
_FLEXIBLE = (HEX, RGB, HSL, PIXELS, SHARE)


def format_share(share: float) -> str:
    if share < 0.00001:
        return "<0.001%"
    return f"{share * 100:.3f}".rstrip("0").rstrip(".") + "%"


class InventoryModel(QAbstractTableModel):
    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._index: Optional[ColorIndex] = None
        self._order = np.empty(0, dtype=np.int32)
        self._picks: frozenset[str] = frozenset()

    # --- data in -----------------------------------------------------------------------

    def set_rows(self, index: Optional[ColorIndex], order: Optional[np.ndarray]) -> None:
        self.beginResetModel()
        self._index = index
        self._order = order if (index is not None and order is not None) else np.empty(0, dtype=np.int32)
        self.endResetModel()

    def set_picks(self, picks) -> None:
        self._picks = frozenset(picks)
        if self.rowCount():
            self.dataChanged.emit(self.index(0, PICK), self.index(self.rowCount() - 1, PICK))

    # --- lookups -----------------------------------------------------------------------

    def distinct_index(self, row: int) -> int:
        return int(self._order[row])

    def color(self, row: int) -> str:
        return self._index.hex(self.distinct_index(row))

    def is_picked(self, row: int) -> bool:
        return self.color(row) in self._picks

    def row_of(self, distinct_index: int) -> int:
        """Row showing a color, or -1 if it is filtered out."""
        found = np.flatnonzero(self._order == distinct_index)
        return int(found[0]) if found.size else -1

    # --- Qt model interface ------------------------------------------------------------

    def rowCount(self, parent=QModelIndex()) -> int:
        return 0 if parent.isValid() else int(self._order.size)

    def columnCount(self, parent=QModelIndex()) -> int:
        return 0 if parent.isValid() else len(COLUMNS)

    def headerData(self, section, orientation, role=Qt.ItemDataRole.DisplayRole):
        if orientation == Qt.Orientation.Horizontal:
            if role == Qt.ItemDataRole.DisplayRole:
                return COLUMNS[section]
            if role == Qt.ItemDataRole.TextAlignmentRole:
                right = section in (PIXELS, SHARE)
                side = Qt.AlignmentFlag.AlignRight if right else Qt.AlignmentFlag.AlignLeft
                return int(side | Qt.AlignmentFlag.AlignVCenter)
        return None

    def data(self, index, role=Qt.ItemDataRole.DisplayRole):
        if not index.isValid() or self._index is None:
            return None
        column = index.column()
        distinct = int(self._order[index.row()])
        if role == Qt.ItemDataRole.DisplayRole:
            if column == HEX:
                return self._index.hex(distinct)
            if column == RGB:
                red, green, blue = key_to_rgb(int(self._index.keys[distinct]))
                return f"{red}, {green}, {blue}"
            if column == HSL:
                hue, saturation, lightness = to_hsl(self._index.hex(distinct))
                if not saturation:
                    hue = 0
                return f"{round(hue) % 360}°, {round(saturation * 100)}%, {round(lightness * 100)}%"
            if column == PIXELS:
                return f"{self._index.count(distinct):,}"
            if column == SHARE:
                return format_share(self._index.share(distinct))
            return None
        if role == Qt.ItemDataRole.TextAlignmentRole:
            side = Qt.AlignmentFlag.AlignRight if column in (PIXELS, SHARE) else Qt.AlignmentFlag.AlignLeft
            return int(side | Qt.AlignmentFlag.AlignVCenter)
        if role == Qt.ItemDataRole.ToolTipRole:
            color = self._index.hex(distinct)
            if column == PICK:
                return f"Remove {color} from the palette" if color in self._picks else f"Pick {color} for the palette"
            return f"Locate {color} and show its related shades"
        return None


class InventoryDelegate(QStyledItemDelegate):
    """Paints rows: hover and selection tint, the swatch, mono HEX, and the Pick pill."""

    def sizeHint(self, option, index) -> QSize:
        return QSize(80, ROW_HEIGHT)

    def paint(self, painter: QPainter, option, index) -> None:
        view: InventoryView = option.widget
        model: InventoryModel = index.model()
        row, column = index.row(), index.column()
        rect = QRectF(option.rect)
        selected = view.selectionModel().isRowSelected(row, QModelIndex())
        hovered = row == view.hover_row

        painter.save()
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        if selected or hovered:
            painter.fillRect(rect, QColor(theme.TINT if selected else theme.PANEL_TINT))
        painter.setPen(QPen(QColor(theme.LINE), 1))
        painter.drawLine(rect.bottomLeft(), rect.bottomRight())

        if column == SWATCH:
            side = 22
            box = QRectF(rect.x() + 14, rect.center().y() - side / 2, side, side)
            painter.setPen(QPen(QColor(33, 29, 38, 50), 1))
            painter.setBrush(QColor(model.color(row)))
            painter.drawRoundedRect(box, 5, 5)
        elif column == PICK:
            picked = model.is_picked(row)
            pill = QRectF(rect.x() + 8, rect.center().y() - 12, 68, 24)
            hot = hovered and view.hover_column == PICK
            painter.setPen(QPen(QColor(theme.ACCENT if picked or hot else theme.LINE), 1))
            painter.setBrush(QColor(theme.TINT if picked or hot else theme.WHITE))
            painter.drawRoundedRect(pill, 7, 7)
            font = theme.small_font(8.5)
            font.setWeight(font.Weight.DemiBold)
            painter.setFont(font)
            painter.setPen(QColor(theme.ACCENT if picked or hot else theme.INK))
            painter.drawText(pill, Qt.AlignmentFlag.AlignCenter, "Picked ✓" if picked else "Pick")
        else:
            text = index.data(Qt.ItemDataRole.DisplayRole) or ""
            if column == HEX:
                painter.setFont(theme.mono_font(9.5))
                painter.setPen(QColor(theme.ACCENT))
            else:
                painter.setFont(option.font)
                painter.setPen(QColor(theme.INK if column in (PIXELS, SHARE) else theme.MUTED))
            alignment = Qt.AlignmentFlag(index.data(Qt.ItemDataRole.TextAlignmentRole))
            painter.drawText(rect.adjusted(10, 0, -10, 0), alignment, text)
        painter.restore()


class InventoryView(QTableView):
    rowHovered = Signal(int)  # distinct index under the pointer, or -1
    rowActivated = Signal(int)  # distinct index clicked or moved to with the keyboard
    pickToggled = Signal(str)  # HEX color whose Pick pill was clicked

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.hover_row = -1
        self.hover_column = -1
        self._empty_text = ""
        self._model = InventoryModel(self)
        self.setModel(self._model)
        self.setItemDelegate(InventoryDelegate(self))
        self.setMouseTracking(True)
        self.setShowGrid(False)
        self.setWordWrap(False)
        self.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.setVerticalScrollMode(QAbstractItemView.ScrollMode.ScrollPerPixel)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self.setCornerButtonEnabled(False)
        vertical = self.verticalHeader()
        vertical.hide()
        vertical.setSectionResizeMode(QHeaderView.ResizeMode.Fixed)
        vertical.setDefaultSectionSize(ROW_HEIGHT)
        vertical.setMinimumSectionSize(ROW_HEIGHT)
        header = self.horizontalHeader()
        header.setHighlightSections(False)
        header.setSectionsClickable(False)
        header.setDefaultAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
        header.setMinimumSectionSize(40)
        header.setSectionResizeMode(QHeaderView.ResizeMode.Fixed)
        header.setStretchLastSection(False)
        self.setMinimumHeight(ROW_HEIGHT * 3 + 36)
        self._fit_columns()
        self.selectionModel().currentRowChanged.connect(self._current_changed)
        self._model.modelReset.connect(self._rows_replaced)

    @property
    def inventory(self) -> InventoryModel:
        return self._model

    def set_empty_text(self, text: str) -> None:
        self._empty_text = text
        self.viewport().update()

    def select_distinct(self, distinct_index: Optional[int], scroll: bool = False) -> None:
        """Highlight the row of a color (if listed) without announcing it as a click."""
        row = -1 if distinct_index is None else self._model.row_of(distinct_index)
        selection = self.selectionModel()
        selection.blockSignals(True)
        if row < 0:
            selection.clear()
        else:
            self.selectRow(row)
            if scroll:
                self.scrollTo(self._model.index(row, HEX), QAbstractItemView.ScrollHint.EnsureVisible)
        selection.blockSignals(False)
        self.viewport().update()

    # --- layout ------------------------------------------------------------------------

    def _fit_columns(self) -> None:
        """Fit the columns to the panel.

        Spare width is shared between the text columns. When the panel is narrow the
        HSL column is dropped first, then RGB; only below that does it scroll sideways.
        """
        available = self.viewport().width()
        hidden: set[int] = set()
        if available < sum(_BASE_WIDTHS.values()):
            hidden.add(HSL)
            if available < sum(_BASE_WIDTHS.values()) - _BASE_WIDTHS[HSL]:
                hidden.add(RGB)
        flexible = [column for column in _FLEXIBLE if column not in hidden]
        shown = sum(width for column, width in _BASE_WIDTHS.items() if column not in hidden)
        extra = max(0, available - shown) // len(flexible)
        used = 0
        for column, width in _BASE_WIDTHS.items():
            self.setColumnHidden(column, column in hidden)
            if column in hidden or column == PICK:
                continue
            width += extra if column in flexible else 0
            self.setColumnWidth(column, width)
            used += width
        self.setColumnWidth(PICK, max(_BASE_WIDTHS[PICK], available - used))

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        self._fit_columns()

    def _rows_replaced(self) -> None:
        self.hover_row = self.hover_column = -1

    # --- interaction -------------------------------------------------------------------

    def _current_changed(self, current: QModelIndex, previous: QModelIndex) -> None:
        if current.isValid():
            self.rowActivated.emit(self._model.distinct_index(current.row()))

    def _set_hover(self, row: int, column: int) -> None:
        if (row, column) != (self.hover_row, self.hover_column):
            changed_row = row != self.hover_row
            self.hover_row, self.hover_column = row, column
            self.viewport().update()
            if changed_row:
                self.rowHovered.emit(self._model.distinct_index(row) if row >= 0 else -1)
        self.viewport().setCursor(Qt.CursorShape.PointingHandCursor if row >= 0 else Qt.CursorShape.ArrowCursor)

    def mouseMoveEvent(self, event) -> None:
        index = self.indexAt(event.position().toPoint())
        self._set_hover(index.row() if index.isValid() else -1, index.column() if index.isValid() else -1)
        super().mouseMoveEvent(event)

    def leaveEvent(self, event) -> None:
        self._set_hover(-1, -1)
        super().leaveEvent(event)

    def wheelEvent(self, event) -> None:
        super().wheelEvent(event)
        index = self.indexAt(self.viewport().mapFromGlobal(event.globalPosition().toPoint()))
        self._set_hover(index.row() if index.isValid() else -1, index.column() if index.isValid() else -1)

    def mousePressEvent(self, event) -> None:
        index = self.indexAt(event.position().toPoint())
        if event.button() == Qt.MouseButton.LeftButton and index.isValid() and index.column() == PICK:
            self.pickToggled.emit(self._model.color(index.row()))
            return
        if event.button() == Qt.MouseButton.LeftButton and index.isValid() and index.row() == self.currentIndex().row():
            # Clicking the current row again still counts as choosing it.
            self.rowActivated.emit(self._model.distinct_index(index.row()))
        super().mousePressEvent(event)

    def keyPressEvent(self, event) -> None:
        current = self.currentIndex()
        if event.key() in (Qt.Key.Key_Space, Qt.Key.Key_Return, Qt.Key.Key_Enter) and current.isValid():
            self.pickToggled.emit(self._model.color(current.row()))
            return
        super().keyPressEvent(event)

    def paintEvent(self, event) -> None:
        super().paintEvent(event)
        if self._model.rowCount() == 0 and self._empty_text:
            painter = QPainter(self.viewport())
            painter.setFont(theme.small_font(9.5))
            painter.setPen(QColor(theme.MUTED))
            painter.drawText(self.viewport().rect(), Qt.AlignmentFlag.AlignCenter, self._empty_text)
