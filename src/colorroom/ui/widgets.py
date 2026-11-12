"""Small reusable widgets: flow layout, color chips, palette swatches, panels and toasts."""

from __future__ import annotations

from typing import Optional, Sequence

from PySide6.QtCore import QPoint, QRect, QRectF, QSize, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QFontMetrics, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import (
    QAbstractButton,
    QBoxLayout,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLayout,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from . import theme


def small_label(text: str = "", wrap: bool = True) -> QLabel:
    """Muted helper text."""
    label = QLabel(text)
    label.setProperty("small", True)
    label.setWordWrap(wrap)
    return label


def field_label(text: str) -> QLabel:
    label = QLabel(text)
    label.setProperty("fieldLabel", True)
    return label


def heading(text: str, point_size: float = 17) -> QLabel:
    label = QLabel(text)
    label.setFont(theme.heading_font(point_size))
    return label


def refresh_style(widget: QWidget) -> None:
    """Re-apply the style sheet after a dynamic property changed."""
    widget.style().unpolish(widget)
    widget.style().polish(widget)
    widget.update()


class ElidedLabel(QLabel):
    """A one-line label that shortens long text with "…" in the middle instead of growing."""

    def __init__(self, text: str = "", parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self._full = text
        self.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        self.setMinimumWidth(40)

    def setText(self, text: str) -> None:
        self._full = text
        self.setToolTip(text)
        self.update()

    def text(self) -> str:
        return self._full

    def sizeHint(self) -> QSize:
        metrics = QFontMetrics(self.font())
        return QSize(metrics.horizontalAdvance(self._full) + 2, metrics.height() + 2)

    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        metrics = QFontMetrics(self.font())
        shown = metrics.elidedText(self._full, Qt.TextElideMode.ElideMiddle, self.width())
        painter.setPen(self.palette().color(self.foregroundRole()))
        painter.drawText(self.rect(), Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter, shown)


class ResponsiveRow(QWidget):
    """Two groups of controls: side by side when there is room, stacked when there is not."""

    def __init__(self, first: QWidget, second: QWidget, gap: int = 14, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self._first, self._second, self._gap = first, second, gap
        self._box = QBoxLayout(QBoxLayout.Direction.LeftToRight, self)
        self._box.setContentsMargins(0, 0, 0, 0)
        self._box.setSpacing(gap)
        self._box.setSizeConstraint(QLayout.SizeConstraint.SetNoConstraint)
        self._box.addWidget(first, 1)
        self._box.addWidget(second, 0)
        for group in (first, second):
            group.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Fixed)
        self.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Fixed)

    def _needed(self) -> int:
        return self._first.minimumSizeHint().width() + self._second.minimumSizeHint().width() + self._gap

    def _stacked(self) -> bool:
        return self._box.direction() == QBoxLayout.Direction.TopToBottom

    def _height(self) -> int:
        first, second = self._first.sizeHint().height(), self._second.sizeHint().height()
        return first + second + 8 if self._stacked() else max(first, second)

    def minimumSizeHint(self) -> QSize:
        first, second = self._first.minimumSizeHint(), self._second.minimumSizeHint()
        return QSize(max(first.width(), second.width()), self._height())

    def sizeHint(self) -> QSize:
        first, second = self._first.sizeHint(), self._second.sizeHint()
        width = max(first.width(), second.width()) if self._stacked() else first.width() + second.width() + self._gap
        return QSize(width, self._height())

    def resizeEvent(self, event) -> None:
        wanted = QBoxLayout.Direction.LeftToRight if self.width() >= self._needed() else QBoxLayout.Direction.TopToBottom
        if wanted != self._box.direction():
            self._box.setDirection(wanted)
            self._box.setSpacing(self._gap if wanted == QBoxLayout.Direction.LeftToRight else 8)
            self.updateGeometry()
        super().resizeEvent(event)


class Panel(QFrame):
    """A white rounded card with a serif title and a line of helper text."""

    def __init__(self, title: str, subtitle: str = "", parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.setObjectName("panel")
        self.body = QVBoxLayout(self)
        self.body.setContentsMargins(16, 14, 16, 14)
        self.body.setSpacing(10)
        self.header = QHBoxLayout()
        self.header.setSpacing(10)
        titles = QVBoxLayout()
        titles.setSpacing(2)
        self.title = heading(title)
        self.subtitle = small_label(subtitle)
        titles.addWidget(self.title)
        titles.addWidget(self.subtitle)
        self.header.addLayout(titles, 1)
        self.body.addLayout(self.header)


class FlowLayout(QLayout):
    """Lays widgets out left to right and wraps to a new row when the width runs out."""

    def __init__(self, parent: Optional[QWidget] = None, spacing: int = 6) -> None:
        super().__init__(parent)
        self._items = []
        self._gap = spacing
        self.setContentsMargins(0, 0, 0, 0)

    def addItem(self, item) -> None:
        self._items.append(item)

    def count(self) -> int:
        return len(self._items)

    def itemAt(self, index: int):
        return self._items[index] if 0 <= index < len(self._items) else None

    def takeAt(self, index: int):
        return self._items.pop(index) if 0 <= index < len(self._items) else None

    def clear(self) -> None:
        while self._items:
            widget = self._items.pop().widget()
            if widget is not None:
                widget.setParent(None)
                widget.deleteLater()
        self.invalidate()

    def expandingDirections(self):
        return Qt.Orientation(0)

    def hasHeightForWidth(self) -> bool:
        return True

    def heightForWidth(self, width: int) -> int:
        return self._arrange(QRect(0, 0, width, 0), apply=False)

    def setGeometry(self, rect: QRect) -> None:
        super().setGeometry(rect)
        self._arrange(rect, apply=True)

    def sizeHint(self) -> QSize:
        return self.minimumSize()

    def minimumSize(self) -> QSize:
        size = QSize()
        for item in self._items:
            size = size.expandedTo(item.minimumSize())
        margins = self.contentsMargins()
        return size + QSize(margins.left() + margins.right(), margins.top() + margins.bottom())

    def _arrange(self, rect: QRect, apply: bool) -> int:
        margins = self.contentsMargins()
        area = rect.adjusted(margins.left(), margins.top(), -margins.right(), -margins.bottom())
        x, y, row_height = area.x(), area.y(), 0
        for item in self._items:
            hint = item.sizeHint()
            if x > area.x() and x + hint.width() > area.right() + 1:
                x = area.x()
                y += row_height + self._gap
                row_height = 0
            if apply:
                item.setGeometry(QRect(QPoint(x, y), hint))
            x += hint.width() + self._gap
            row_height = max(row_height, hint.height())
        return y + row_height - rect.y() + margins.bottom()


class FlowArea(QWidget):
    """A plain widget whose children wrap like words in a paragraph."""

    def __init__(self, spacing: int = 6, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.flow = FlowLayout(self, spacing)
        policy = QSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Minimum)
        policy.setHeightForWidth(True)
        self.setSizePolicy(policy)

    def add(self, widget: QWidget) -> None:
        self.flow.addWidget(widget)

    def clear(self) -> None:
        self.flow.clear()

    def hasHeightForWidth(self) -> bool:
        return True

    def heightForWidth(self, width: int) -> int:
        return self.flow.heightForWidth(width)

    def sizeHint(self) -> QSize:
        # Report the height needed at the width we actually have. Qt's default would
        # report the height of a one-item-wide column, which is far too tall.
        width = self.width() if self.width() > 0 else 360
        return QSize(width, self.flow.heightForWidth(width))

    def minimumSizeHint(self) -> QSize:
        return self.flow.minimumSize()

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        if event.size().width() != event.oldSize().width():
            self.updateGeometry()


class Chip(QAbstractButton):
    """A compact pill: optional color dot, a label, and an optional muted suffix."""

    hovered = Signal(bool)

    def __init__(
        self,
        text: str,
        color: Optional[str] = None,
        suffix: str = "",
        mono: bool = True,
        checkable: bool = False,
        parent: Optional[QWidget] = None,
    ) -> None:
        super().__init__(parent)
        self._text = text
        self._color = color
        self._suffix = suffix
        self._font = theme.mono_font(9) if mono else theme.small_font(9)
        self._suffix_font = theme.small_font(8.5)
        self._inside = False
        self.setCheckable(checkable)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFocusPolicy(Qt.FocusPolicy.TabFocus)
        self.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)

    def sizeHint(self) -> QSize:
        width = 9 + QFontMetrics(self._font).horizontalAdvance(self._text) + 9
        if self._color:
            width += 13 + 6
        if self._suffix:
            width += 6 + QFontMetrics(self._suffix_font).horizontalAdvance(self._suffix)
        return QSize(width, 28)

    def minimumSizeHint(self) -> QSize:
        return self.sizeHint()

    def enterEvent(self, event) -> None:
        self._inside = True
        self.hovered.emit(True)
        self.update()

    def leaveEvent(self, event) -> None:
        self._inside = False
        self.hovered.emit(False)
        self.update()

    def focusInEvent(self, event) -> None:
        super().focusInEvent(event)
        self.hovered.emit(True)

    def focusOutEvent(self, event) -> None:
        super().focusOutEvent(event)
        if not self._inside:
            self.hovered.emit(False)

    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        active = self._inside or self.isChecked() or self.isDown()
        rect = QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5)
        border = theme.ACCENT if self.isChecked() else theme.ACCENT_SOFT if self.hasFocus() else theme.LINE
        painter.setPen(QPen(QColor(border), 1))
        painter.setBrush(QColor(theme.TINT if active else theme.WHITE))
        painter.drawRoundedRect(rect, 6, 6)
        x = 9.0
        if self._color:
            dot = QRectF(x, (self.height() - 13) / 2, 13, 13)
            painter.setPen(QPen(QColor(33, 29, 38, 50), 1))
            painter.setBrush(QColor(self._color))
            painter.drawRoundedRect(dot, 3, 3)
            x += 13 + 6
        painter.setFont(self._font)
        painter.setPen(QColor(theme.ACCENT if active else theme.INK))
        width = QFontMetrics(self._font).horizontalAdvance(self._text)
        painter.drawText(QRectF(x, 0, width + 2, self.height()), Qt.AlignmentFlag.AlignVCenter, self._text)
        x += width
        if self._suffix:
            painter.setFont(self._suffix_font)
            painter.setPen(QColor(theme.ACCENT if active else theme.MUTED))
            painter.drawText(QRectF(x + 6, 0, self.width(), self.height()), Qt.AlignmentFlag.AlignVCenter, self._suffix)


class SwatchButton(QAbstractButton):
    """One palette swatch: a block of color above its HEX code (and brand role)."""

    hovered = Signal(bool)

    def __init__(self, color: str, role: str = "", parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.color = color
        self.role = role
        self._inside = False
        self._selected = False
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFocusPolicy(Qt.FocusPolicy.TabFocus)

    def set_selected(self, selected: bool) -> None:
        if selected != self._selected:
            self._selected = selected
            self.update()

    def enterEvent(self, event) -> None:
        self._inside = True
        self.hovered.emit(True)
        self.update()

    def leaveEvent(self, event) -> None:
        self._inside = False
        self.hovered.emit(False)
        self.update()

    def focusInEvent(self, event) -> None:
        super().focusInEvent(event)
        self.hovered.emit(True)

    def focusOutEvent(self, event) -> None:
        super().focusOutEvent(event)
        if not self._inside:
            self.hovered.emit(False)

    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        active = self._inside or self._selected or self.hasFocus()
        rect = QRectF(self.rect()).adjusted(1, 1, -1, -1)
        label_height = 44 if self.role else 27
        outline = QPainterPath()
        outline.addRoundedRect(rect, 8, 8)
        painter.fillPath(outline, QColor(theme.TINT if self._selected else theme.WHITE))
        painter.save()
        painter.setClipPath(outline)
        painter.fillRect(QRectF(rect.left(), rect.top(), rect.width(), rect.height() - label_height), QColor(self.color))
        painter.restore()
        painter.setPen(QPen(QColor(theme.ACCENT if active else theme.LINE), 2 if self._selected else 1))
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawPath(outline)
        text_top = rect.bottom() - label_height
        painter.setFont(theme.mono_font(8.5))
        painter.setPen(QColor(theme.ACCENT if self._selected else theme.INK))
        painter.drawText(QRectF(rect.left(), text_top, rect.width(), 27), Qt.AlignmentFlag.AlignCenter, self.color)
        if self.role:
            painter.setFont(theme.small_font(7.5))
            painter.setPen(QColor(theme.MUTED))
            painter.drawText(QRectF(rect.left(), text_top + 22, rect.width(), 18), Qt.AlignmentFlag.AlignCenter, self.role)


class SwatchGrid(QWidget):
    """The palette: up to six equal swatches per row."""

    swatchHovered = Signal(object)  # PaletteEntry, or None when the pointer leaves
    swatchClicked = Signal(object)  # PaletteEntry

    GAP = 7
    MIN_WIDTH = 60  # wide enough for a HEX code
    ROLE_MIN_WIDTH = 96  # wide enough for "Secondary · 30%"
    MAX_COLUMNS = 6

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self._buttons: list[SwatchButton] = []
        self._entries: Sequence = ()
        self._empty_text = ""
        self._selected: Optional[str] = None
        self.setMinimumHeight(96)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)

    def set_empty_text(self, text: str) -> None:
        self._empty_text = text
        self.update()

    def set_entries(self, entries: Sequence) -> None:
        for button in self._buttons:
            button.setParent(None)
            button.deleteLater()
        self._buttons = []
        self._entries = tuple(entries)
        for entry in self._entries:
            button = SwatchButton(entry.color, entry.role, self)
            tip = entry.color if entry.exact else f"{entry.color} is not in the image. Closest image color: {entry.source_color}"
            button.setToolTip(tip)
            button.hovered.connect(lambda inside, e=entry: self.swatchHovered.emit(e if inside else None))
            button.clicked.connect(lambda _=False, e=entry: self.swatchClicked.emit(e))
            button.set_selected(entry.color == self._selected)
            button.show()
            self._buttons.append(button)
        self._arrange()
        self.update()

    def set_selected(self, color: Optional[str]) -> None:
        self._selected = color
        for button in self._buttons:
            button.set_selected(button.color == color)

    def resizeEvent(self, event) -> None:
        self._arrange()

    def _arrange(self) -> None:
        count = len(self._buttons)
        if not count:
            self.setFixedHeight(96)
            return
        has_roles = any(entry.role for entry in self._entries)
        narrowest = self.ROLE_MIN_WIDTH if has_roles else self.MIN_WIDTH
        width = max(self.width(), narrowest)
        columns = max(1, min(self.MAX_COLUMNS, count, (width + self.GAP) // (narrowest + self.GAP)))
        item_width = (width - self.GAP * (columns - 1)) / columns
        item_height = 100 if has_roles else 84
        for position, button in enumerate(self._buttons):
            row, column = divmod(position, columns)
            left = round(column * (item_width + self.GAP))
            right = round(column * (item_width + self.GAP) + item_width)
            button.setGeometry(left, row * (item_height + self.GAP), right - left, item_height)
        rows = -(-count // columns)
        self.setFixedHeight(rows * item_height + (rows - 1) * self.GAP)

    def paintEvent(self, event) -> None:
        if self._buttons or not self._empty_text:
            return
        painter = QPainter(self)
        painter.setFont(theme.small_font(9.5))
        painter.setPen(QColor(theme.MUTED))
        painter.drawText(self.rect(), Qt.AlignmentFlag.AlignCenter | Qt.TextFlag.TextWordWrap, self._empty_text)


class ColorBlock(QWidget):
    """A fixed-size rounded block of one color."""

    def __init__(self, size: int = 44, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self._color: Optional[str] = None
        self.setFixedSize(size, size)

    def set_color(self, color: Optional[str]) -> None:
        self._color = color
        self.update()

    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        rect = QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5)
        painter.setPen(QPen(QColor(33, 29, 38, 50), 1))
        painter.setBrush(QColor(self._color) if self._color else QColor(theme.PANEL_TINT))
        painter.drawRoundedRect(rect, 9, 9)


class BrandMark(QWidget):
    def __init__(self, size: int = 26, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.setFixedSize(size, size)

    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        ratio = self.devicePixelRatioF()
        pixmap = theme.brand_mark(round(self.width() * ratio))
        pixmap.setDevicePixelRatio(ratio)
        painter.drawPixmap(0, 0, pixmap)


class Toast(QLabel):
    """A short message that floats over the bottom-right corner and fades away."""

    def __init__(self, parent: QWidget) -> None:
        super().__init__(parent)
        self.setObjectName("toast")
        self.hide()
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.timeout.connect(self.hide)

    def show_message(self, text: str, milliseconds: int = 2700) -> None:
        self.setText(text)
        self.adjustSize()
        self.reposition()
        self.show()
        self.raise_()
        self._timer.start(milliseconds)

    def reposition(self) -> None:
        parent = self.parentWidget()
        if parent is not None:
            self.move(parent.width() - self.width() - 20, parent.height() - self.height() - 20)
