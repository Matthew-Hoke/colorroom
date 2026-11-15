"""The "My palette" tab: collect colors, then check, preview and export them.

Left: the palette you are building (several named palettes can live in one project).
Right: five tools that all work on that palette:
    Contrast       which color pairs are readable as text
    Color vision   how the palette and the image look with color blindness
    Tints & shades a 50 to 950 scale from any one color
    Previews       the palette on a web page, app, game, logo, poster and gradient map
                   (your own design file is on the "Your project" tab, ui/project_tab.py)
    Values         HEX, RGB, HSL and approximate CMYK
"""

from __future__ import annotations

from pathlib import Path
from typing import Optional, Sequence

import numpy as np
from PySide6.QtCore import QByteArray, QMimeData, QPoint, QPointF, QRectF, QSize, Qt, Signal
from PySide6.QtGui import (
    QColor,
    QFont,
    QGuiApplication,
    QIcon,
    QImage,
    QKeySequence,
    QPainter,
    QPainterPath,
    QPen,
    QPixmap,
    QRegion,
    QShortcut,
)
from PySide6.QtWidgets import (
    QAbstractItemView,
    QComboBox,
    QFileDialog,
    QHBoxLayout,
    QHeaderView,
    QInputDialog,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QMenu,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QSplitter,
    QStyle,
    QStyledItemDelegate,
    QTableWidget,
    QTableWidgetItem,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from ..core import designer
from ..core.colorspace import contrast_ratio, normalize_hex, readable_text_color
from ..core.designer import Library, LibraryEntry, NamedPalette, PaletteColor, Project
from ..core.exporters import PALETTE_FORMATS, palette_text, save_palette
from . import theme
from .widgets import ColorBlock, FlowArea, Panel, field_label, small_label

#: Extra export formats offered on this tab: key -> (menu label, file extension).
STUDIO_FORMATS = {
    "svg": ("Swatches as SVG (Figma, Illustrator)", ".svg"),
    "tokens": ("Design tokens (Figma token plugins)", ".tokens.json"),
}


def strip_icon(colors: Sequence[str]) -> QIcon:
    """A small strip of a palette's colors, shown beside its name in menus and lists."""
    pixmap = QPixmap(96, 18)
    pixmap.fill(Qt.GlobalColor.transparent)
    if colors:
        painter = QPainter(pixmap)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        shape = QPainterPath()
        shape.addRoundedRect(QRectF(0.5, 0.5, 95, 17), 4, 4)
        painter.setClipPath(shape)
        width = 96 / len(colors)
        for position, color in enumerate(colors):
            painter.fillRect(QRectF(position * width, 0, width + 1, 18), QColor(color))
        painter.end()
    return QIcon(pixmap)


def _text_on(color: str) -> QColor:
    return QColor(readable_text_color(color))


def _soft_border() -> QPen:
    return QPen(QColor(128, 120, 140, 90), 1)


# ======================================================================================
# The list of colors in the palette
# ======================================================================================


CHECK_ZONE = 34  # width of the tick-box area at the left of a row
DELETE_ZONE = 36  # width of the × area at the right of a row


class _ColorRow(QStyledItemDelegate):
    """One palette color: tick box, swatch, name and HEX, and an × to delete it."""

    HEIGHT = 46

    def sizeHint(self, option, index) -> QSize:
        return QSize(120, self.HEIGHT)

    def paint(self, painter: QPainter, option, index) -> None:
        color: PaletteColor = index.data(Qt.ItemDataRole.UserRole)
        if color is None:
            return
        view: ColorList = option.widget
        rect = QRectF(option.rect).adjusted(2, 2, -2, -2)
        selected = bool(option.state & QStyle.StateFlag.State_Selected)
        hovered = bool(option.state & QStyle.StateFlag.State_MouseOver)
        painter.save()
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        if selected or hovered:
            painter.setPen(QPen(QColor(theme.ACCENT if selected else theme.LINE), 1))
            painter.setBrush(QColor(theme.TINT if selected else theme.PANEL_TINT))
            painter.drawRoundedRect(rect, 7, 7)
        # tick box: ticked means selected
        box = QRectF(rect.x() + 10, rect.center().y() - 8, 16, 16)
        painter.setPen(QPen(QColor(theme.ACCENT if selected else theme.MUTED), 1.4))
        painter.setBrush(QColor(theme.ACCENT if selected else theme.WHITE))
        painter.drawRoundedRect(box, 4, 4)
        if selected:
            painter.setPen(QPen(QColor("white"), 2, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin))
            painter.drawPolyline([box.topLeft() + QPointF(3.5, 8.5), box.topLeft() + QPointF(6.8, 11.5), box.topLeft() + QPointF(12.5, 4.8)])
        block = QRectF(rect.x() + CHECK_ZONE + 2, rect.y() + 5, rect.height() - 10, rect.height() - 10)
        painter.setPen(_soft_border())
        painter.setBrush(QColor(color.hex))
        painter.drawRoundedRect(block, 6, 6)
        painter.setFont(theme.small_font(7.5))
        painter.setPen(_text_on(color.hex))
        painter.drawText(block, Qt.AlignmentFlag.AlignCenter, str(index.row() + 1))
        left = block.right() + 11
        width = rect.right() - left - DELETE_ZONE - 4
        if color.name:
            font = theme.small_font(9.5)
            font.setWeight(QFont.Weight.DemiBold)
            painter.setFont(font)
            painter.setPen(QColor(theme.INK))
            painter.drawText(QRectF(left, rect.y() + 4, width, 19), Qt.AlignmentFlag.AlignVCenter, color.name)
            painter.setFont(theme.mono_font(8.5))
            painter.setPen(QColor(theme.MUTED))
            painter.drawText(QRectF(left, rect.y() + 22, width, 17), Qt.AlignmentFlag.AlignVCenter, color.hex)
        else:
            painter.setFont(theme.mono_font(10))
            painter.setPen(QColor(theme.INK))
            painter.drawText(QRectF(left, rect.y(), width, rect.height()), Qt.AlignmentFlag.AlignVCenter, color.hex)
        zone = QRectF(rect.right() - DELETE_ZONE, rect.y(), DELETE_ZONE, rect.height())
        if color.locked:
            painter.setFont(theme.small_font(7.5))
            painter.setPen(QColor(theme.ACCENT))
            painter.drawText(zone.adjusted(-22, 0, -6, 0), Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignRight, "Locked")
        else:
            hot = view is not None and view.hot_delete_row == index.row()
            center = zone.center()
            if hot:
                painter.setPen(Qt.PenStyle.NoPen)
                painter.setBrush(QColor(theme.ERROR))
                painter.drawEllipse(center, 11, 11)
            painter.setPen(QPen(QColor("white" if hot else theme.MUTED), 1.8, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
            painter.drawLine(center + QPointF(-4, -4), center + QPointF(4, 4))
            painter.drawLine(center + QPointF(4, -4), center + QPointF(-4, 4))
        painter.restore()


class ColorList(QListWidget):
    """The palette's colors.

    Tick the box of a row (or Ctrl-click, Shift-click) to select several; the × deletes
    one color straight away; drag a row to reorder.
    """

    reordered = Signal()
    deleteRequested = Signal(int)  # row whose × was clicked

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.hot_delete_row = -1
        self.setItemDelegate(_ColorRow(self))
        self.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.setDragDropMode(QAbstractItemView.DragDropMode.InternalMove)
        self.setDefaultDropAction(Qt.DropAction.MoveAction)
        self.setVerticalScrollMode(QAbstractItemView.ScrollMode.ScrollPerPixel)
        self.setMouseTracking(True)
        self.setMinimumHeight(96)  # two rows: the window fits a 1280 × 720 screen
        self._empty_text = ""

    def set_empty_text(self, text: str) -> None:
        self._empty_text = text
        self.viewport().update()

    def show_colors(self, colors: Sequence[PaletteColor], keep: Sequence[int] = ()) -> None:
        self.blockSignals(True)
        self.clear()
        for color in colors:
            item = QListWidgetItem()
            item.setData(Qt.ItemDataRole.UserRole, color)
            item.setToolTip(f"{color.label()}  {color.hex}" if color.name else color.hex)
            self.addItem(item)
        for position in keep:
            if 0 <= position < self.count():
                self.item(position).setSelected(True)
        self.blockSignals(False)
        self.hot_delete_row = -1
        self.itemSelectionChanged.emit()

    def colors(self) -> list[PaletteColor]:
        return [self.item(row).data(Qt.ItemDataRole.UserRole) for row in range(self.count())]

    def selected_rows(self) -> list[int]:
        return sorted(index.row() for index in self.selectedIndexes())

    def zone_at(self, position) -> tuple[int, str]:
        """(row, part) under a viewport position: part is 'check', 'delete', 'body' or ''."""
        index = self.indexAt(position)
        if not index.isValid():
            return -1, ""
        rect = self.visualRect(index)
        if position.x() < rect.x() + CHECK_ZONE + 2:
            return index.row(), "check"
        if position.x() > rect.right() - DELETE_ZONE - 2:
            return index.row(), "delete"
        return index.row(), "body"

    def click_zone(self, row: int, part: str) -> None:
        """Do what a click on that part of a row does."""
        item = self.item(row)
        if item is None:
            return
        if part == "check":
            item.setSelected(not item.isSelected())
        elif part == "delete" and not item.data(Qt.ItemDataRole.UserRole).locked:
            self.deleteRequested.emit(row)

    def mousePressEvent(self, event) -> None:
        row, part = self.zone_at(event.position().toPoint())
        if event.button() == Qt.MouseButton.LeftButton and part in ("check", "delete"):
            self.click_zone(row, part)
            return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event) -> None:
        row, part = self.zone_at(event.position().toPoint())
        hot = row if part == "delete" else -1
        if hot != self.hot_delete_row:
            self.hot_delete_row = hot
            self.viewport().update()
        self.viewport().setCursor(Qt.CursorShape.PointingHandCursor if part in ("check", "delete") else Qt.CursorShape.ArrowCursor)
        super().mouseMoveEvent(event)

    def leaveEvent(self, event) -> None:
        if self.hot_delete_row != -1:
            self.hot_delete_row = -1
            self.viewport().update()
        super().leaveEvent(event)

    def dropEvent(self, event) -> None:
        super().dropEvent(event)
        self.reordered.emit()

    def paintEvent(self, event) -> None:
        super().paintEvent(event)
        if self.count() == 0 and self._empty_text:
            painter = QPainter(self.viewport())
            painter.setFont(theme.small_font(9.5))
            painter.setPen(QColor(theme.MUTED))
            painter.drawText(
                self.viewport().rect().adjusted(18, 0, -18, 0),
                Qt.AlignmentFlag.AlignCenter | Qt.TextFlag.TextWordWrap,
                self._empty_text,
            )


# ======================================================================================
# Tool 1: contrast
# ======================================================================================


class ContrastGrid(QWidget):
    """Every color as text on every other color, with its WCAG contrast ratio."""

    HEADER = 34
    MIN_CELL = 58
    MAX_CELL = 104

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self._colors: list[str] = []
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)

    def set_colors(self, colors: Sequence[str]) -> None:
        self._colors = list(colors)
        side = self.HEADER + len(self._colors) * self.MIN_CELL + 2
        self.setMinimumSize(side if self._colors else 200, side if self._colors else 120)
        self.update()

    def _cell(self) -> float:
        count = max(1, len(self._colors))
        room = min(self.width(), self.height() * 1.25) - self.HEADER - 2
        return max(self.MIN_CELL, min(self.MAX_CELL, room / count))

    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        colors = self._colors
        if len(colors) < 2:
            painter.setFont(theme.small_font(9.5))
            painter.setPen(QColor(theme.MUTED))
            painter.drawText(self.rect(), Qt.AlignmentFlag.AlignCenter, "Add at least two colors to compare them.")
            return
        cell = self._cell()
        height = cell * 0.8
        head = self.HEADER
        for position, color in enumerate(colors):
            # column heads (the text color) and row heads (the background color)
            top = QRectF(head + position * cell + 3, 3, cell - 6, head - 8)
            side = QRectF(3, head + position * height + 3, head - 8, height - 6)
            for block in (top, side):
                painter.setPen(_soft_border())
                painter.setBrush(QColor(color))
                painter.drawRoundedRect(block, 5, 5)
        for row, background in enumerate(colors):
            for column, text in enumerate(colors):
                box = QRectF(head + column * cell + 2, head + row * height + 2, cell - 4, height - 4)
                if row == column:
                    painter.setPen(QPen(QColor(theme.LINE), 1, Qt.PenStyle.DashLine))
                    painter.setBrush(Qt.BrushStyle.NoBrush)
                    painter.drawRoundedRect(box, 6, 6)
                    continue
                ratio = contrast_ratio(background, text)
                grade = designer.contrast_grade(ratio)
                painter.setPen(_soft_border())
                painter.setBrush(QColor(background))
                painter.drawRoundedRect(box, 6, 6)
                sample = theme.small_font(max(9.5, min(15, cell / 6.5)))
                sample.setWeight(QFont.Weight.DemiBold)
                painter.setFont(sample)
                painter.setPen(QColor(text))
                painter.drawText(
                    QRectF(box.x(), box.y() + 2, box.width(), box.height() * 0.55), Qt.AlignmentFlag.AlignCenter, "Aa"
                )
                note = _text_on(background)
                note.setAlpha(235 if grade != designer.FAIL else 150)
                painter.setFont(theme.small_font(7.5))
                painter.setPen(note)
                label = f"{ratio:.1f}  {grade}" if cell >= 74 else f"{ratio:.1f}"
                painter.drawText(
                    QRectF(box.x(), box.y() + box.height() * 0.52, box.width(), box.height() * 0.42),
                    Qt.AlignmentFlag.AlignCenter,
                    label,
                )
                if grade == designer.FAIL:
                    painter.setPen(QPen(note, 1))
                    painter.drawLine(box.topRight() + QPointF(-10, 4), box.topRight() + QPointF(-4, 10))
                    painter.drawLine(box.topRight() + QPointF(-4, 4), box.topRight() + QPointF(-10, 10))


class ContrastPage(QWidget):
    copyRequested = Signal(str, str)  # text, confirmation
    notify = Signal(str)
    status = Signal(str, bool)

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self._colors: list[str] = []
        self._names: list[str] = []
        self.last_dir = ""
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 8, 0, 0)
        layout.setSpacing(8)
        self.summary = small_label()
        layout.addWidget(self.summary)
        self.grid = ContrastGrid()
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setWidget(self.grid)
        scroll.viewport().setAutoFillBackground(False)
        self.grid.setAutoFillBackground(False)
        layout.addWidget(scroll, 1)
        self.best = small_label()
        layout.addWidget(self.best)
        row = FlowArea(6)
        self.copy_table_button = QPushButton("Copy as table")
        self.copy_table_button.setToolTip("Every pair with its ratio and level. Pastes into spreadsheets, documents and chat.")
        self.copy_image_button = QPushButton("Copy as image")
        self.copy_image_button.setToolTip("A picture of the grid. Pastes into Photoshop, Figma, slides and documents.")
        self.export_button = QPushButton("Export…")
        self.export_button.setToolTip("Save the contrast results as a file")
        menu = QMenu(self.export_button)
        for key, (label, extension) in designer.CONTRAST_FORMATS.items():
            menu.addAction(f"{label}  ({extension})").triggered.connect(lambda _=False, k=key: self.export_dialog(k))
        self.export_button.setMenu(menu)
        for button in (self.copy_table_button, self.copy_image_button, self.export_button):
            row.add(button)
        layout.addWidget(row)
        self.copy_table_button.clicked.connect(self.copy_table)
        self.copy_image_button.clicked.connect(self.copy_image)

    def set_palette(self, colors: Sequence[str], names: Sequence[str]) -> None:
        self._colors, self._names = list(colors), list(names)
        self.grid.set_colors(colors)
        total = len(colors) * (len(colors) - 1) // 2
        for button in (self.copy_table_button, self.copy_image_button, self.export_button):
            button.setEnabled(total > 0)
        if total == 0:
            self.summary.setText("Rows are backgrounds, columns are text colors.")
            self.best.setText("")
            return
        body = designer.readable_pairs(colors, 4.5)
        large = designer.readable_pairs(colors, 3.0)
        self.summary.setText(
            f"Rows are backgrounds, columns are text colors. {len(body)} of {total} pairs are readable as body text "
            f"(AA, 4.5 or more); {len(large)} work for large headings (3 or more)."
        )
        if body:
            first, second, ratio = body[0]
            self.best.setText(f"Strongest pair: {first} with {second} ({ratio:.1f} to 1).")
        else:
            self.best.setText("No pair is readable as body text yet. Add a much lighter or much darker color.")

    # --- copy and export -----------------------------------------------------------------

    def grid_image(self) -> QImage:
        """A picture of the whole grid on a plain light background, whatever the theme."""
        cell = 92
        side = ContrastGrid.HEADER + cell * max(1, len(self._colors)) + 2
        painter_target = QImage(side, round(ContrastGrid.HEADER + cell * 0.8 * max(1, len(self._colors)) + 2), QImage.Format.Format_RGB32)
        painter_target.fill(QColor("#FFFEFA"))
        grid = ContrastGrid()
        grid.set_colors(self._colors)
        grid.resize(painter_target.width(), painter_target.height())
        grid.render(painter_target, QPoint(), QRegion(), QWidget.RenderFlag.DrawChildren)  # no window background
        grid.deleteLater()
        return painter_target

    def copy_table(self) -> None:
        if len(self._colors) > 1:
            self.copyRequested.emit(designer.contrast_text(self._colors, self._names, "tsv"), "Contrast table copied")

    def copy_image(self) -> None:
        if len(self._colors) > 1:
            QGuiApplication.clipboard().setImage(self.grid_image())
            self.notify.emit("Picture of the contrast grid copied")

    def export_to(self, path, kind: str) -> bool:
        """Save the contrast results to `path` in the format `kind`."""
        target = Path(path)
        try:
            if kind == "png":
                if not self.grid_image().save(str(target), "PNG"):
                    raise OSError("the picture could not be written")
            elif kind == "svg":
                target.write_text(designer.contrast_svg(self._colors), encoding="utf-8", newline="\n")
            else:
                target.write_text(designer.contrast_text(self._colors, self._names, kind), encoding="utf-8", newline="")
        except (OSError, ValueError) as error:
            self.status.emit(f"The contrast results could not be saved: {error}", True)
            return False
        self.last_dir = str(target.parent)
        self.status.emit(f"Contrast results saved: {target}", False)
        self.notify.emit(f"Saved {target.name}")
        return True

    def export_dialog(self, kind: str) -> None:
        if len(self._colors) < 2:
            return
        label, extension = designer.CONTRAST_FORMATS[kind]
        start = str(Path(self.last_dir or str(Path.home())) / f"contrast{extension}")
        path, _ = QFileDialog.getSaveFileName(self, "Export contrast results", start, f"{label} (*{extension})")
        if path:
            target = Path(path)
            self.export_to(target if target.suffix else target.with_suffix(extension), kind)


# ======================================================================================
# Tool 2: color vision
# ======================================================================================


class VisionStrips(QWidget):
    """The palette as seen with each type of color vision."""

    ROW = 62

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self._colors: list[str] = []
        self.setMinimumHeight(self.ROW * len(designer.VISION_TYPES))
        self.setFixedHeight(self.ROW * len(designer.VISION_TYPES))

    def set_colors(self, colors: Sequence[str]) -> None:
        self._colors = list(colors)
        self.update()

    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        label_width = 176
        for row, (key, name, description) in enumerate(designer.VISION_TYPES):
            top = row * self.ROW
            font = theme.small_font(9.5)
            font.setWeight(QFont.Weight.DemiBold)
            painter.setFont(font)
            painter.setPen(QColor(theme.INK))
            painter.drawText(QRectF(0, top + 8, label_width, 20), Qt.AlignmentFlag.AlignVCenter, name)
            painter.setFont(theme.small_font(8))
            painter.setPen(QColor(theme.MUTED))
            painter.drawText(
                QRectF(0, top + 27, label_width - 8, 30), Qt.AlignmentFlag.AlignTop | Qt.TextFlag.TextWordWrap, description
            )
            if not self._colors:
                continue
            room = self.width() - label_width
            width = min(92.0, room / len(self._colors))
            for position, color in enumerate(self._colors):
                seen = designer.simulate(color, key)
                block = QRectF(label_width + position * width + 2, top + 7, width - 4, self.ROW - 16)
                painter.setPen(_soft_border())
                painter.setBrush(QColor(seen))
                painter.drawRoundedRect(block, 6, 6)
                if width >= 62:
                    painter.setFont(theme.mono_font(7.5))
                    painter.setPen(_text_on(seen))
                    painter.drawText(block, Qt.AlignmentFlag.AlignCenter, seen)


class VisionPage(QWidget):
    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self._rgba: Optional[np.ndarray] = None
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 8, 0, 0)
        content = QWidget()
        layout = QVBoxLayout(content)
        layout.setContentsMargins(0, 0, 6, 0)
        layout.setSpacing(10)
        self.strips = VisionStrips()
        layout.addWidget(self.strips)
        self.warning = small_label()
        layout.addWidget(self.warning)
        row = QHBoxLayout()
        row.addWidget(field_label("Image as seen with"))
        self.kind = QComboBox()
        for key, name, _ in designer.VISION_TYPES:
            self.kind.addItem(name, key)
        self.kind.setCurrentIndex(2)
        row.addWidget(self.kind)
        row.addStretch(1)
        layout.addLayout(row)
        self.picture = QLabel()
        self.picture.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop)
        self.picture.setMinimumHeight(40)
        layout.addWidget(self.picture)
        layout.addStretch(1)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        scroll.setWidget(content)
        scroll.viewport().setAutoFillBackground(False)
        content.setAutoFillBackground(False)
        outer.addWidget(scroll)
        self.kind.currentIndexChanged.connect(self._show_image)

    def set_palette(self, colors: Sequence[str], names: Sequence[str]) -> None:
        self.strips.set_colors(colors)
        notes = []
        for key, name, _ in designer.VISION_TYPES[1:4]:
            pairs = designer.confusable_pairs(colors, key)
            if pairs:
                shown = ", ".join(f"{first} and {second}" for first, second in pairs[:3])
                more = f" and {len(pairs) - 3} more" if len(pairs) > 3 else ""
                notes.append(f"{name}: {shown}{more} become hard to tell apart.")
        if len(colors) < 2:
            self.warning.setText("")
        elif notes:
            self.warning.setText("Check these pairs. " + " ".join(notes))
        else:
            self.warning.setText("No pair of colors collapses into the same color for the common types of color blindness.")

    def set_image(self, rgba: Optional[np.ndarray]) -> None:
        if rgba is None:
            self._rgba = None
        else:
            height, width = rgba.shape[:2]
            step = max(1, -(-max(width, height) // 560))  # a small copy is plenty for a preview
            self._rgba = np.ascontiguousarray(rgba[::step, ::step])
        self._show_image()

    def _show_image(self) -> None:
        if self._rgba is None:
            self.picture.setPixmap(QPixmap())
            self.picture.setText("Open an image on the Image tab to preview it here.")
            self.picture.setProperty("small", True)
            return
        seen = self._rgba.copy()
        seen[..., :3] = designer.simulate_rgb(self._rgba[..., :3], self.kind.currentData())
        height, width = seen.shape[:2]
        image = QImage(seen.data, width, height, 4 * width, QImage.Format.Format_RGBA8888).copy()
        self.picture.setText("")
        self.picture.setPixmap(QPixmap.fromImage(image))


# ======================================================================================
# Tool 3: tints and shades
# ======================================================================================


class RampStrip(QWidget):
    clicked = Signal(str)  # HEX of the step that was clicked

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self._ramp: list[tuple[int, str]] = []
        self._base = ""
        self.setMinimumHeight(150)
        self.setCursor(Qt.CursorShape.PointingHandCursor)

    def set_ramp(self, ramp: Sequence[tuple[int, str]], base: str) -> None:
        self._ramp, self._base = list(ramp), base
        self.update()

    def _block(self, position: int) -> QRectF:
        width = self.width() / max(1, len(self._ramp))
        return QRectF(position * width + 2, 2, width - 4, min(self.height() - 4, 190))

    def mousePressEvent(self, event) -> None:
        for position, (_, color) in enumerate(self._ramp):
            if self._block(position).contains(event.position()):
                self.clicked.emit(color)

    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        if not self._ramp:
            painter.setFont(theme.small_font(9.5))
            painter.setPen(QColor(theme.MUTED))
            painter.drawText(self.rect(), Qt.AlignmentFlag.AlignCenter, "Add a color to your palette to build a scale from it.")
            return
        for position, (step, color) in enumerate(self._ramp):
            block = self._block(position)
            own = color == self._base
            painter.setPen(QPen(QColor(theme.ACCENT), 2) if own else _soft_border())
            painter.setBrush(QColor(color))
            painter.drawRoundedRect(block, 8, 8)
            ink = _text_on(color)
            font = theme.small_font(9)
            font.setWeight(QFont.Weight.DemiBold)
            painter.setFont(font)
            painter.setPen(ink)
            painter.drawText(block.adjusted(0, 10, 0, 0), Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignTop, str(step))
            if block.width() >= 58:
                painter.setFont(theme.mono_font(7.5))
                painter.drawText(
                    block.adjusted(0, 0, 0, -10), Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignBottom, color
                )
            if own:
                painter.setFont(theme.small_font(7.5))
                painter.drawText(block, Qt.AlignmentFlag.AlignCenter, "your\ncolor")


class RampPage(QWidget):
    copyRequested = Signal(str, str)  # text, confirmation
    paletteRequested = Signal(str, list)  # name, list of (label, hex)

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 8, 0, 0)
        layout.setSpacing(10)
        row = QHBoxLayout()
        row.addWidget(field_label("Build a scale from"))
        self.base = QComboBox()
        self.base.setMinimumWidth(190)
        row.addWidget(self.base)
        row.addStretch(1)
        layout.addLayout(row)
        self.strip = RampStrip()
        layout.addWidget(self.strip, 1)
        layout.addWidget(
            small_label(
                "Eleven steps from 50 (lightest) to 950 (darkest), evenly spaced for the eye. "
                "Click a step to copy its HEX code."
            )
        )
        buttons = QHBoxLayout()
        self.css_button = QPushButton("Copy as CSS variables")
        self.tailwind_button = QPushButton("Copy for Tailwind")
        self.palette_button = QPushButton("Save scale as a new palette")
        for button in (self.css_button, self.tailwind_button, self.palette_button):
            buttons.addWidget(button)
        buttons.addStretch(1)
        layout.addLayout(buttons)
        self.base.currentIndexChanged.connect(self._rebuild)
        self.strip.clicked.connect(lambda color: self.copyRequested.emit(color, f"{color} copied"))
        self.css_button.clicked.connect(lambda: self._copy("css"))
        self.tailwind_button.clicked.connect(lambda: self._copy("tailwind"))
        self.palette_button.clicked.connect(self._as_palette)
        self._ramp: list[tuple[int, str]] = []

    def set_palette(self, colors: Sequence[str], names: Sequence[str]) -> None:
        wanted = self.base.currentData()
        self.base.blockSignals(True)
        self.base.clear()
        for position, color in enumerate(colors):
            name = names[position] if position < len(names) else ""
            self.base.addItem(f"{name}  {color}" if name else color, color)
            swatch = QPixmap(14, 14)
            swatch.fill(QColor(color))
            self.base.setItemIcon(position, swatch)
        found = self.base.findData(wanted)
        self.base.setCurrentIndex(found if found >= 0 else 0)
        self.base.blockSignals(False)
        self._rebuild()

    def _name(self) -> str:
        label = self.base.currentText().split("  ")[0] if "  " in self.base.currentText() else "brand"
        return designer.slug(label, "brand")

    def _rebuild(self) -> None:
        color = self.base.currentData()
        self._ramp = designer.tint_ramp(color) if color else []
        self.strip.set_ramp(self._ramp, color or "")
        for button in (self.css_button, self.tailwind_button, self.palette_button):
            button.setEnabled(bool(self._ramp))

    def _copy(self, kind: str) -> None:
        if not self._ramp:
            return
        if kind == "css":
            self.copyRequested.emit(designer.ramp_css(self._name(), self._ramp), "Scale copied as CSS variables")
        else:
            self.copyRequested.emit(designer.ramp_tailwind(self._name(), self._ramp), "Scale copied for Tailwind")

    def _as_palette(self) -> None:
        if self._ramp:
            name = self._name()
            self.paletteRequested.emit(f"{name} scale", [(f"{name} {step}", color) for step, color in self._ramp])


# ======================================================================================
# Tool 4: mockups
# ======================================================================================


#: The previews: key -> (button label, width / height of the artwork). Your own file is on
#: the "Your project" tab (ui/project_tab.py); a link to it comes first in the row.
PREVIEWS = {
    "web": ("Web page", 16 / 10),
    "app": ("App UI", 2 * 9 / 16 + 0.08),
    "game": ("Game UI", 16 / 9),
    "logo": ("Logo", 3 / 2),
    "poster": ("Poster", 2 * 0.7 + 0.06),
    "gradient": ("Gradient map", 16 / 9),
}


class MockupCanvas(QWidget):
    """One large preview of the palette at work."""

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self._roles: Optional[designer.Roles] = None
        self._other: Optional[designer.Roles] = None  # the opposite (dark or light) arrangement
        self._colors: list[str] = []
        self._kind = "web"
        self._rgba: Optional[np.ndarray] = None  # a small copy of the open image
        self._mapped: Optional[QImage] = None
        self.setMinimumHeight(160)  # low enough for a 1280 × 720 screen

    def set_state(self, kind: str, colors: Sequence[str], roles, other) -> None:
        self._kind, self._colors, self._roles, self._other = kind, list(colors), roles, other
        self._mapped = None
        self.update()

    def set_image(self, rgba: Optional[np.ndarray]) -> None:
        self._rgba = rgba
        self._mapped = None
        self.update()

    def artwork_image(self) -> QImage:
        """The current preview as a picture, for copying or saving."""
        rect = self.artwork_rect().toAlignedRect()
        return self.grab(rect).toImage()

    def artwork_rect(self) -> QRectF:
        """Where the preview is drawn: as large as fits, keeping its proportions."""
        ratio = PREVIEWS[self._kind][1]
        if self._kind == "gradient" and self._rgba is not None:
            ratio = self._rgba.shape[1] / (self._rgba.shape[0] * 1.16)
        height = min(float(self.height()), self.width() / ratio, 640.0)
        return QRectF(0, 0, height * ratio, height)

    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        if self._roles is None:
            painter.setFont(theme.small_font(9.5))
            painter.setPen(QColor(theme.MUTED))
            painter.drawText(self.rect(), Qt.AlignmentFlag.AlignCenter, "Add colors to your palette to see them in use.")
            return
        rect = self.artwork_rect()
        if self._kind == "app":  # two phones: the light and the dark arrangement
            gap = rect.width() * 0.04
            width = (rect.width() - gap) / 2
            frames = [(QRectF(0, 0, width, rect.height()), self._roles), (QRectF(width + gap, 0, width, rect.height()), self._other)]
            for frame, roles in frames:
                self._framed(painter, frame, lambda p, r, use=roles: self._phone(p, r, use), radius=rect.height() * 0.05)
        elif self._kind == "poster":
            gap = rect.width() * 0.04
            width = (rect.width() - gap) / 2
            self._framed(painter, QRectF(0, 0, width, rect.height()), lambda p, r: self._poster(p, r, self._roles))
            self._framed(painter, QRectF(width + gap, 0, width, rect.height()), lambda p, r: self._poster(p, r, self._other))
        else:
            draw = {"web": self._page, "game": self._game, "logo": self._logo, "gradient": self._gradient}[self._kind]
            self._framed(painter, rect, lambda p, r: draw(p, r, self._roles))

    def _framed(self, painter: QPainter, rect: QRectF, draw, radius: float = 10) -> None:
        shape = QPainterPath()
        shape.addRoundedRect(rect.adjusted(0.5, 0.5, -0.5, -0.5), radius, radius)
        painter.save()
        painter.setClipPath(shape)
        draw(painter, rect)
        painter.restore()
        painter.setPen(_soft_border())
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawPath(shape)

    @staticmethod
    def _faded(color: str, alpha: int) -> QColor:
        faded = QColor(color)
        faded.setAlpha(alpha)
        return faded

    def _poster(self, painter: QPainter, rect: QRectF, roles) -> None:
        unit = rect.width() / 100
        painter.fillRect(rect, QColor(roles.background))
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(roles.secondary))
        painter.drawEllipse(QPointF(rect.x() + 70 * unit, rect.y() + 46 * unit), 46 * unit, 46 * unit)
        painter.setBrush(QColor(roles.primary))
        painter.drawEllipse(QPointF(rect.x() + 36 * unit, rect.y() + 62 * unit), 30 * unit, 30 * unit)
        painter.setBrush(QColor(roles.accent))
        painter.drawRect(QRectF(rect.x() + 10 * unit, rect.y() + 10 * unit, 14 * unit, 3 * unit))
        title = theme.heading_font(max(8.0, 9.5 * unit))
        title.setWeight(QFont.Weight.Bold)
        painter.setFont(title)
        painter.setPen(QColor(roles.text))
        base = rect.bottom() - 40 * unit
        painter.drawText(QRectF(rect.x() + 10 * unit, base, 82 * unit, 15 * unit), Qt.AlignmentFlag.AlignVCenter, "Color")
        painter.drawText(QRectF(rect.x() + 10 * unit, base + 13 * unit, 82 * unit, 15 * unit), Qt.AlignmentFlag.AlignVCenter, "in use")
        painter.setFont(theme.small_font(max(5.0, 3.2 * unit)))
        painter.setPen(self._faded(roles.text, 200))
        painter.drawText(
            QRectF(rect.x() + 10 * unit, rect.bottom() - 11 * unit, 82 * unit, 7 * unit),
            Qt.AlignmentFlag.AlignVCenter,
            "A study of one palette · 2026",
        )

    def _page(self, painter: QPainter, rect: QRectF, roles) -> None:
        unit = rect.height() / 100
        left = rect.x() + 6 * unit
        painter.fillRect(rect, QColor(roles.background))
        painter.fillRect(QRectF(rect.x(), rect.y(), rect.width(), 12 * unit), QColor(roles.surface))
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(roles.primary))
        painter.drawEllipse(QPointF(left + 2.5 * unit, rect.y() + 6 * unit), 2.5 * unit, 2.5 * unit)
        nav = theme.small_font(max(5.0, 2.9 * unit))
        painter.setFont(nav)
        painter.setPen(QColor(roles.text if contrast_ratio(roles.text, roles.surface) >= 3 else readable_text_color(roles.surface)))
        for position, word in enumerate(("Work", "About", "Contact")):
            painter.drawText(
                QRectF(rect.right() - (46 - position * 14) * unit, rect.y(), 14 * unit, 12 * unit), Qt.AlignmentFlag.AlignCenter, word
            )
        title = theme.heading_font(max(8.0, 6.6 * unit))
        title.setWeight(QFont.Weight.Bold)
        painter.setFont(title)
        painter.setPen(QColor(roles.text))
        painter.drawText(
            QRectF(left, rect.y() + 19 * unit, rect.width() * 0.62, 20 * unit),
            Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop | Qt.TextFlag.TextWordWrap,
            "A palette that works",
        )
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(self._faded(roles.text, 120))
        for line, width in enumerate((0.5, 0.44)):
            painter.drawRoundedRect(QRectF(left, rect.y() + (41 + line * 4.2) * unit, rect.width() * width, 1.6 * unit), 1, 1)
        button = QRectF(left, rect.y() + 52 * unit, 26 * unit, 9 * unit)
        painter.setBrush(QColor(roles.primary))
        painter.drawRoundedRect(button, 2.2 * unit, 2.2 * unit)
        label = theme.small_font(max(5.0, 2.9 * unit))
        label.setWeight(QFont.Weight.DemiBold)
        painter.setFont(label)
        painter.setPen(QColor(roles.on_primary))
        painter.drawText(button, Qt.AlignmentFlag.AlignCenter, "Get started")
        second = QRectF(button.right() + 3 * unit, button.y(), 22 * unit, 9 * unit)
        painter.setPen(QPen(QColor(roles.text), 1))
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawRoundedRect(second, 2.2 * unit, 2.2 * unit)
        painter.drawText(second, Qt.AlignmentFlag.AlignCenter, "Learn more")
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(roles.secondary))
        painter.drawRoundedRect(QRectF(rect.right() - rect.width() * 0.27, rect.y() + 20 * unit, rect.width() * 0.22, 40 * unit), 3 * unit, 3 * unit)
        painter.setBrush(QColor(roles.accent))
        painter.drawEllipse(QPointF(rect.right() - rect.width() * 0.26, rect.y() + 56 * unit), 7 * unit, 7 * unit)
        card_width = (rect.width() - 12 * unit - 8 * unit) / 3
        for position, tone in enumerate((roles.primary, roles.secondary, roles.accent)):
            card = QRectF(left + position * (card_width + 4 * unit), rect.y() + 70 * unit, card_width, 24 * unit)
            painter.setBrush(QColor(roles.surface))
            painter.drawRoundedRect(card, 2.2 * unit, 2.2 * unit)
            painter.setBrush(QColor(tone))
            painter.drawRoundedRect(QRectF(card.x() + 3 * unit, card.y() + 3 * unit, 7 * unit, 7 * unit), 1.6 * unit, 1.6 * unit)
            ink = roles.text if contrast_ratio(roles.text, roles.surface) >= 3 else readable_text_color(roles.surface)
            painter.setBrush(self._faded(ink, 170))
            painter.drawRoundedRect(QRectF(card.x() + 3 * unit, card.y() + 13 * unit, card.width() * 0.6, 1.8 * unit), 1, 1)
            painter.setBrush(self._faded(ink, 90))
            painter.drawRoundedRect(QRectF(card.x() + 3 * unit, card.y() + 17.5 * unit, card.width() * 0.8, 1.4 * unit), 1, 1)

    def _phone(self, painter: QPainter, rect: QRectF, roles) -> None:
        unit = rect.width() / 100
        painter.fillRect(rect, QColor(roles.background))
        header = QRectF(rect.x(), rect.y(), rect.width(), 36 * unit)
        painter.fillRect(header, QColor(roles.primary))
        title = theme.small_font(max(6.0, 7.5 * unit))
        title.setWeight(QFont.Weight.Bold)
        painter.setFont(title)
        painter.setPen(QColor(roles.on_primary))
        painter.drawText(header.adjusted(9 * unit, 12 * unit, 0, 0), Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter, "Today")
        painter.setPen(Qt.PenStyle.NoPen)
        ink = roles.text
        for row, tone in enumerate((roles.secondary, roles.accent, roles.primary, roles.secondary)):
            top = rect.y() + (44 + row * 27) * unit
            card = QRectF(rect.x() + 7 * unit, top, rect.width() - 14 * unit, 22 * unit)
            if card.bottom() > rect.bottom() - 4:
                break
            painter.setBrush(QColor(roles.surface))
            painter.drawRoundedRect(card, 4 * unit, 4 * unit)
            painter.setBrush(QColor(tone))
            painter.drawEllipse(QPointF(card.x() + 11 * unit, card.center().y()), 5.5 * unit, 5.5 * unit)
            on_card = ink if contrast_ratio(ink, roles.surface) >= 3 else readable_text_color(roles.surface)
            painter.setBrush(self._faded(on_card, 190))
            painter.drawRoundedRect(QRectF(card.x() + 22 * unit, card.y() + 6.5 * unit, card.width() * 0.45, 3 * unit), 1.5, 1.5)
            painter.setBrush(self._faded(on_card, 100))
            painter.drawRoundedRect(QRectF(card.x() + 22 * unit, card.y() + 12.5 * unit, card.width() * 0.6, 2.4 * unit), 1.2, 1.2)
        painter.setBrush(QColor(roles.accent))
        painter.drawEllipse(QPointF(rect.right() - 17 * unit, rect.bottom() - 17 * unit), 10 * unit, 10 * unit)
        painter.setPen(QPen(_text_on(roles.accent), max(1.5, 1.6 * unit)))
        center = QPointF(rect.right() - 17 * unit, rect.bottom() - 17 * unit)
        painter.drawLine(center + QPointF(-4 * unit, 0), center + QPointF(4 * unit, 0))
        painter.drawLine(center + QPointF(0, -4 * unit), center + QPointF(0, 4 * unit))


    def _by_lightness(self) -> list[str]:
        return designer.gradient_stops(self._colors)

    def _game(self, painter: QPainter, rect: QRectF, roles) -> None:
        """A side-scrolling game screen: scenery, a character, and a full heads-up display."""
        tones = self._by_lightness()  # dark to light
        dark, light = tones[0], tones[-1]
        middle = tones[len(tones) // 2]
        unit = rect.height() / 100
        left, top, width = rect.x(), rect.y(), rect.width()
        painter.setPen(Qt.PenStyle.NoPen)
        # sky, sun, two ranges of hills and the ground
        for band in range(24):
            painter.setBrush(QColor(designer.mix_oklab(dark, middle, band / 23 * 0.85)))
            painter.drawRect(QRectF(left, top + band * rect.height() / 24, width, rect.height() / 24 + 1))
        painter.setBrush(QColor(tones[-2] if len(tones) > 2 else light))
        painter.drawEllipse(QPointF(left + width * 0.72, top + 30 * unit), 9 * unit, 9 * unit)
        for depth, (tone, base, rise) in enumerate(((roles.secondary, 66, 20), (roles.primary, 76, 14))):
            hills = QPainterPath()
            hills.moveTo(left, rect.bottom())
            hills.lineTo(left, top + base * unit)
            steps = 7
            for step in range(steps + 1):
                x = left + width * step / steps
                y = top + (base - (rise if (step + depth) % 2 else rise * 0.25)) * unit
                hills.lineTo(x, y)
            hills.lineTo(rect.right(), rect.bottom())
            painter.setBrush(self._faded(designer.mix_oklab(tone, dark, 0.35 if depth == 0 else 0.15), 255))
            painter.drawPath(hills)
        painter.setBrush(QColor(dark))
        painter.drawRect(QRectF(left, top + 84 * unit, width, 16 * unit))
        painter.setBrush(QColor(roles.secondary))
        for start, length in ((0.07, 0.2), (0.36, 0.16), (0.6, 0.24)):
            painter.drawRoundedRect(QRectF(left + width * start, top + 82 * unit, width * length, 3 * unit), unit, unit)
        # the player and two collectibles
        hero = QRectF(left + width * 0.2, top + 68 * unit, 7 * unit, 14 * unit)
        painter.setBrush(QColor(light))
        painter.drawRoundedRect(hero, 2 * unit, 2 * unit)
        painter.setBrush(QColor(roles.accent))
        painter.drawRect(QRectF(hero.x(), hero.y() + 4 * unit, hero.width(), 2.4 * unit))
        for position in (0.42, 0.48):
            painter.setBrush(QColor(light))
            painter.drawEllipse(QPointF(left + width * position, top + 72 * unit), 2 * unit, 2 * unit)
        # heads-up display: bars, score, minimap, hotbar and a dialog box
        panel = self._faded(dark, 215)
        bars = QRectF(left + 3 * unit, top + 3 * unit, 46 * unit, 15 * unit)
        painter.setBrush(panel)
        painter.drawRoundedRect(bars, 2 * unit, 2 * unit)
        label = theme.small_font(max(5.0, 1.9 * unit))
        label.setWeight(QFont.Weight.Bold)
        for row, (name, tone, amount) in enumerate((("HP", roles.primary, 0.72), ("MP", roles.accent, 0.45))):
            y = bars.y() + (2.6 + row * 5.6) * unit
            painter.setFont(label)
            painter.setPen(QColor(light))
            painter.drawText(QRectF(bars.x() + 2 * unit, y, 7 * unit, 4.4 * unit), Qt.AlignmentFlag.AlignVCenter, name)
            painter.setPen(Qt.PenStyle.NoPen)
            track = QRectF(bars.x() + 9 * unit, y + 0.6 * unit, 34 * unit, 3.2 * unit)
            painter.setBrush(self._faded(light, 60))
            painter.drawRoundedRect(track, 1.6 * unit, 1.6 * unit)
            painter.setBrush(QColor(tone))
            painter.drawRoundedRect(QRectF(track.x(), track.y(), track.width() * amount, track.height()), 1.6 * unit, 1.6 * unit)
        score = theme.mono_font(max(6.0, 3.1 * unit))
        score.setWeight(QFont.Weight.Bold)
        painter.setFont(score)
        painter.setPen(QColor(light))
        painter.drawText(QRectF(left, top + 4 * unit, width, 6 * unit), Qt.AlignmentFlag.AlignHCenter, "012 480")
        painter.setFont(label)
        painter.setPen(QColor(roles.accent))
        painter.drawText(QRectF(left, top + 10.5 * unit, width, 4 * unit), Qt.AlignmentFlag.AlignHCenter, "LEVEL 3")
        painter.setPen(QPen(QColor(light), max(1.0, 0.5 * unit)))
        painter.setBrush(panel)
        radar = QPointF(rect.right() - 12 * unit, top + 12 * unit)
        painter.drawEllipse(radar, 8.5 * unit, 8.5 * unit)
        painter.setPen(Qt.PenStyle.NoPen)
        for dx, dy, tone in ((0, 0, light), (3, -2.5, roles.accent), (-3.5, 2, roles.primary), (2, 4, roles.secondary)):
            painter.setBrush(QColor(tone))
            painter.drawEllipse(radar + QPointF(dx * unit, dy * unit), 1.1 * unit, 1.1 * unit)
        slot = 8.5 * unit
        count = 5
        start = left + (width - count * slot - (count - 1) * 1.5 * unit) / 2
        for position in range(count):
            box = QRectF(start + position * (slot + 1.5 * unit), rect.bottom() - slot - 2.5 * unit, slot, slot)
            chosen = position == 1
            edge = QColor(light)
            edge.setAlpha(255 if chosen else 110)
            painter.setPen(QPen(edge, max(1.0, (0.9 if chosen else 0.35) * unit)))
            painter.setBrush(panel)
            painter.drawRoundedRect(box, 1.6 * unit, 1.6 * unit)
            if position < 4:
                painter.setPen(Qt.PenStyle.NoPen)
                painter.setBrush(QColor((roles.primary, roles.accent, roles.secondary, light)[position]))
                painter.drawRoundedRect(box.adjusted(2.4 * unit, 2.4 * unit, -2.4 * unit, -2.4 * unit), unit, unit)
        dialog = QRectF(rect.right() - 62 * unit, top + 44 * unit, 58 * unit, 25 * unit)
        if dialog.x() > left + width * 0.45:
            painter.setPen(QPen(QColor(roles.primary), max(1.0, 0.5 * unit)))
            painter.setBrush(panel)
            painter.drawRoundedRect(dialog, 2 * unit, 2 * unit)
            painter.setFont(label)
            painter.setPen(QColor(roles.accent))
            painter.drawText(dialog.adjusted(3 * unit, 2 * unit, 0, 0), Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop, "GUIDE")
            painter.setFont(theme.small_font(max(5.0, 2.0 * unit)))
            painter.setPen(QColor(light))
            painter.drawText(
                dialog.adjusted(3 * unit, 7 * unit, -3 * unit, -7 * unit),
                Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop | Qt.TextFlag.TextWordWrap,
                "The bridge is out. Find another way across.",
            )
            button = QRectF(dialog.right() - 19 * unit, dialog.bottom() - 6.5 * unit, 16 * unit, 4.6 * unit)
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QColor(roles.primary))
            painter.drawRoundedRect(button, 1.4 * unit, 1.4 * unit)
            painter.setFont(label)
            painter.setPen(QColor(roles.on_primary))
            painter.drawText(button, Qt.AlignmentFlag.AlignCenter, "Continue")

    def _mark(self, painter: QPainter, center: QPointF, size: float, first: str, second: str, third: str) -> None:
        """A simple geometric mark: two overlapping rounded shapes and a dot."""
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(first))
        painter.drawRoundedRect(QRectF(center.x() - size * 0.5, center.y() - size * 0.5, size * 0.72, size * 0.72), size * 0.2, size * 0.2)
        painter.setBrush(self._faded(second, 235))
        painter.drawEllipse(QPointF(center.x() + size * 0.14, center.y() + size * 0.14), size * 0.36, size * 0.36)
        painter.setBrush(QColor(third))
        painter.drawEllipse(QPointF(center.x() + size * 0.14, center.y() + size * 0.14), size * 0.12, size * 0.12)

    def _logo(self, painter: QPainter, rect: QRectF, roles) -> None:
        """The same logo on four backgrounds, the way a brand sheet shows it."""
        tones = self._by_lightness()
        dark, light = tones[0], tones[-1]
        tiles = (
            (roles.background, roles.primary, roles.secondary, roles.accent, roles.text),
            (roles.primary, roles.on_primary, roles.on_primary, roles.primary, roles.on_primary),
            (dark, light, roles.accent, dark, light),
            ("#FFFFFF", roles.primary, roles.secondary, "#FFFFFF", "#1B1B1F"),
        )
        gap = max(2.0, rect.height() * 0.012)
        painter.fillRect(rect, QColor(theme.LINE))
        width, height = (rect.width() - gap) / 2, (rect.height() - gap) / 2
        for position, (background, first, second, third, ink) in enumerate(tiles):
            tile = QRectF(rect.x() + (position % 2) * (width + gap), rect.y() + (position // 2) * (height + gap), width, height)
            painter.fillRect(tile, QColor(background))
            size = min(tile.height() * 0.42, tile.width() * 0.2)
            total = size + size * 0.35 + size * 2.5
            start = tile.center().x() - total / 2
            self._mark(painter, QPointF(start + size / 2, tile.center().y()), size, first, second, third)
            word = theme.heading_font(max(7.0, size * 0.33))
            word.setWeight(QFont.Weight.Bold)
            painter.setFont(word)
            painter.setPen(QColor(ink))
            text = QRectF(start + size * 1.3, tile.center().y() - size * 0.5, size * 2.9, size * 0.62)
            painter.drawText(text, Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter, "Mesa")
            painter.setFont(theme.small_font(max(5.0, size * 0.15)))
            faint = QColor(ink)
            faint.setAlpha(190)
            painter.setPen(faint)
            painter.drawText(
                QRectF(text.x() + size * 0.03, text.bottom() + size * 0.02, size * 2.9, size * 0.3),
                Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
                "STUDIO  ·  EST. 2026",
            )

    def _sample_scene(self) -> np.ndarray:
        """A shaded sphere on a floor, used for the gradient map when no image is open."""
        height, width = 270, 480
        y, x = np.mgrid[0:height, 0:width].astype(np.float64)
        scene = 40 + 150 * (y / height) ** 1.4
        dx, dy = (x - width * 0.5) / (height * 0.36), (y - height * 0.46) / (height * 0.36)
        inside = dx * dx + dy * dy < 1
        depth = np.sqrt(np.clip(1 - dx * dx - dy * dy, 0, 1))
        shade = np.clip(0.2 + 0.85 * (-0.45 * dx - 0.55 * dy + 0.7 * depth), 0, 1) * 255
        scene = np.where(inside, shade, scene)
        gray = np.clip(scene, 0, 255).astype(np.uint8)
        return np.dstack([gray, gray, gray, np.full_like(gray, 255)])

    def _gradient(self, painter: QPainter, rect: QRectF, roles) -> None:
        """The image recolored with the palette, above the gradient that did it."""
        if self._mapped is None:
            source = self._rgba if self._rgba is not None else self._sample_scene()
            mapped = np.ascontiguousarray(source.copy())
            mapped[..., :3] = designer.gradient_map(source[..., :3], self._colors)
            height, width = mapped.shape[:2]
            self._mapped = QImage(mapped.data, width, height, 4 * width, QImage.Format.Format_RGBA8888).copy()
        bar_height = rect.height() * 0.12
        picture = QRectF(rect.x(), rect.y(), rect.width(), rect.height() - bar_height)
        painter.fillRect(rect, QColor(self._by_lightness()[0]))
        scale = min(picture.width() / self._mapped.width(), picture.height() / self._mapped.height())
        shown = QRectF(0, 0, self._mapped.width() * scale, self._mapped.height() * scale)
        shown.moveCenter(picture.center())
        painter.drawImage(shown, self._mapped)
        table = designer.gradient_lut(self._colors)
        strip = QImage(table.tobytes(), 256, 1, 256 * 3, QImage.Format.Format_RGB888).copy()
        bar = QRectF(rect.x(), rect.bottom() - bar_height, rect.width(), bar_height)
        painter.drawImage(bar, strip)
        stops = self._by_lightness()
        painter.setFont(theme.mono_font(max(6.0, bar_height * 0.2)))
        for position, color in enumerate(stops):
            if len(stops) > 12 and position % 2:
                continue
            x = bar.x() + bar.width() * (position / max(1, len(stops) - 1))
            cell = QRectF(min(max(bar.x(), x - 40), bar.right() - 80), bar.y(), 80, bar.height())
            if position == 0:
                cell, flags = QRectF(bar.x() + 6, bar.y(), 80, bar.height()), Qt.AlignmentFlag.AlignLeft
            elif position == len(stops) - 1:
                cell, flags = QRectF(bar.right() - 86, bar.y(), 80, bar.height()), Qt.AlignmentFlag.AlignRight
            else:
                flags = Qt.AlignmentFlag.AlignHCenter
            if len(stops) <= 7 or position in (0, len(stops) - 1):
                painter.setPen(_text_on(color))
                painter.drawText(cell, flags | Qt.AlignmentFlag.AlignVCenter, color)


class RoleChooser(FlowArea):
    """Which palette color plays each role (Background, Text, Primary...): Auto, or by hand.

    Shared by Previews and the "Your project" tab, each with its own choices. Hand-set
    roles whose color leaves the palette go back to Auto. `changed` is emitted whenever the
    person (or a method below) changes a role or the arrangement.
    """

    changed = Signal()

    def __init__(self, inline_buttons: bool = True, parent: Optional[QWidget] = None) -> None:
        super().__init__(6, parent)
        self._colors: list[str] = []
        self._names: list[str] = []
        self._variant = 0
        self._overrides: dict[str, str] = {}  # role name -> HEX chosen by hand
        self._syncing = False
        self.combos: dict[str, QComboBox] = {}
        for name in designer.ROLE_NAMES:
            holder = QWidget()
            box = QHBoxLayout(holder)
            box.setContentsMargins(0, 0, 4, 0)
            box.setSpacing(5)
            box.addWidget(field_label(name))
            combo = QComboBox()
            combo.setMinimumWidth(118)
            combo.setToolTip(f"Which palette color is the {name.lower()}. Auto lets Colorroom choose.")
            combo.currentIndexChanged.connect(lambda _=0, n=name: self._chosen(n))
            self.combos[name] = combo
            box.addWidget(combo)
            self.add(holder)
        self.swap_button = QPushButton("Try another arrangement")
        self.swap_button.setProperty("link", True)
        self.swap_button.setToolTip("Let Colorroom give the colors different jobs: light or dark background, different accents")
        self.auto_button = QPushButton("All auto")
        self.auto_button.setProperty("link", True)
        self.auto_button.setToolTip("Forget the roles set by hand")
        if inline_buttons:
            self.add(self.swap_button)  # otherwise the owner places it
        self.add(self.auto_button)
        self.swap_button.clicked.connect(self.next_variant)
        self.auto_button.clicked.connect(self.clear_overrides)
        self._fill()

    def set_palette(self, colors: Sequence[str], names: Sequence[str] = ()) -> None:
        self._colors, self._names = list(colors), list(names)
        self._overrides = {name: color for name, color in self._overrides.items() if color in self._colors}
        self._fill()

    def _fill(self) -> None:
        self._syncing = True
        auto = designer.assign_roles(self._colors, self._variant)
        for name, combo in self.combos.items():
            combo.clear()
            chosen = auto.as_dict()[name] if auto is not None else ""
            combo.addItem(f"Auto  {chosen}".strip(), "")
            for position, color in enumerate(self._colors):
                label = self._names[position] if position < len(self._names) and self._names[position] else color
                swatch = QPixmap(14, 14)
                swatch.fill(QColor(color))
                combo.addItem(QIcon(swatch), label, color)
            combo.setCurrentIndex(max(0, combo.findData(self._overrides.get(name, ""))))
            combo.setEnabled(bool(self._colors))
        self._syncing = False
        self.auto_button.setEnabled(bool(self._overrides))
        self.swap_button.setEnabled(len(self._colors) > 1)

    def _chosen(self, name: str) -> None:
        if self._syncing:
            return
        color = self.combos[name].currentData()
        if color:
            self._overrides[name] = color
        else:
            self._overrides.pop(name, None)
        self.auto_button.setEnabled(bool(self._overrides))
        self.changed.emit()

    def set_role(self, name: str, color: Optional[str]) -> None:
        """Give a role to a palette color by hand (None or "" returns it to automatic)."""
        if name not in self.combos:
            return
        if color and color in self._colors:
            self._overrides[name] = color
        else:
            self._overrides.pop(name, None)
        self._fill()
        self.changed.emit()

    def overrides(self) -> dict[str, str]:
        return dict(self._overrides)

    def set_overrides(self, overrides: dict) -> None:
        """Set every hand-set role at once, quietly (colors not in the palette are ignored)."""
        self._overrides = {name: color for name, color in dict(overrides).items() if name in self.combos and color in self._colors}
        self._fill()

    def clear_overrides(self) -> None:
        self._overrides = {}
        self._fill()
        self.changed.emit()

    def variant(self) -> int:
        return self._variant

    def set_variant(self, variant: int) -> None:
        """Choose the arrangement quietly (0: light background, 1: dark; higher rotate accents)."""
        if variant != self._variant:
            self._variant = int(variant)
            self._fill()

    def next_variant(self) -> None:
        self._variant += 1
        self._fill()
        self.changed.emit()

    def roles(self) -> Optional[designer.Roles]:
        return designer.with_overrides(designer.assign_roles(self._colors, self._variant), self._overrides)

    def other_roles(self) -> Optional[designer.Roles]:
        """The opposite arrangement (dark for light), with the same roles set by hand."""
        return designer.with_overrides(designer.assign_roles(self._colors, self._variant + 1), self._overrides)


class MockupPage(QWidget):
    """The Previews tool: pick what to preview the palette on, and who does what.

    Roles: each job (Background, Text, Primary...) is chosen automatically, or set by hand
    with the Roles row; hand-set roles apply to every preview. Your own design file is on
    the "Your project" tab; the first link of the row goes there (`projectRequested`).
    """

    notify = Signal(str)
    status = Signal(str, bool)
    projectRequested = Signal()

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self._colors: list[str] = []
        self._names: list[str] = []
        self._kind = "web"
        self.last_dir = ""
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 8, 0, 0)
        layout.setSpacing(8)

        chooser = FlowArea(6)
        self.project_link = QPushButton("Try it on your own file: Your project")
        self.project_link.setProperty("link", True)
        self.project_link.setToolTip("Open your own PSD, AI, PDF, PNG or JPG, recolor it with your palettes and export the same file type")
        chooser.add(self.project_link)
        self.kind_buttons: dict[str, QPushButton] = {}
        for key, (label, _) in PREVIEWS.items():
            button = QPushButton(label)
            button.setCheckable(True)
            button.setChecked(key == self._kind)
            button.clicked.connect(lambda _=False, k=key: self.set_kind(k))
            self.kind_buttons[key] = button
            chooser.add(button)
        layout.addWidget(chooser)

        # --- roles: who does what ------------------------------------------------------
        self.role_chooser = RoleChooser(inline_buttons=True)
        self.role_combos = self.role_chooser.combos
        self.swap_button = self.role_chooser.swap_button
        self.auto_button = self.role_chooser.auto_button
        layout.addWidget(self.role_chooser)

        self.roles_label = small_label()
        layout.addWidget(self.roles_label)
        self.canvas = MockupCanvas()
        layout.addWidget(self.canvas, 1)

        share = FlowArea(6)
        self.copy_preview_button = QPushButton("Copy preview")
        self.copy_preview_button.setToolTip("Copy this preview as a picture")
        self.save_preview_button = QPushButton("Save preview…")
        self.save_preview_button.setToolTip("Save this preview as a PNG picture, at the size shown here")
        share.add(self.copy_preview_button)
        share.add(self.save_preview_button)
        layout.addWidget(share)

        self.project_link.clicked.connect(self.projectRequested)
        self.role_chooser.changed.connect(self._show)
        self.copy_preview_button.clicked.connect(self.copy_preview)
        self.save_preview_button.clicked.connect(self.save_preview_dialog)
        self._show()

    # ----------------------------------------------------------------------------------
    # Palette and roles
    # ----------------------------------------------------------------------------------

    def set_palette(self, colors: Sequence[str], names: Sequence[str]) -> None:
        self._colors, self._names = list(colors), list(names)
        self.role_chooser.set_palette(colors, names)
        self._show()

    def set_role(self, name: str, color: Optional[str]) -> None:
        """Give a role to a palette color by hand (None or "" returns it to automatic)."""
        self.role_chooser.set_role(name, color)

    def overrides(self) -> dict[str, str]:
        return self.role_chooser.overrides()

    def clear_overrides(self) -> None:
        self.role_chooser.clear_overrides()

    def next_variant(self) -> None:
        self.role_chooser.next_variant()

    def roles(self) -> Optional[designer.Roles]:
        return self.role_chooser.roles()

    def set_image(self, rgba: Optional[np.ndarray]) -> None:
        """The image open on the Image tab (a small copy), used by the gradient map."""
        self.canvas.set_image(rgba)
        self._show()

    def kind(self) -> str:
        return self._kind

    def set_kind(self, kind: str) -> None:
        if kind in PREVIEWS:
            self._kind = kind
        for key, button in self.kind_buttons.items():
            button.setChecked(key == self._kind)
        self._show()

    # ----------------------------------------------------------------------------------
    # Showing, copying and saving
    # ----------------------------------------------------------------------------------

    def _show(self) -> None:
        roles = self.roles()
        self.canvas.set_state(self._kind, self._colors, roles, self.role_chooser.other_roles())
        self.swap_button.setEnabled(len(self._colors) > 1 and self._kind != "gradient")
        ready = roles is not None
        self.copy_preview_button.setEnabled(ready)
        self.save_preview_button.setEnabled(ready)
        if roles is None:
            self.roles_label.setText("")
        elif self._kind == "gradient":
            source = "your image" if self.canvas._rgba is not None else "a sample scene (open an image to use your own)"
            self.roles_label.setText(
                f"Gradient map of {source}: shadows take the darkest palette color, highlights the lightest."
            )
        elif self._kind in ("app", "poster"):
            self.roles_label.setText("Shown in a light and a dark arrangement.")
        else:
            self.roles_label.setText("")

    def preview_image(self) -> QImage:
        """The current preview as a picture."""
        return self.canvas.artwork_image()

    def copy_preview(self) -> None:
        QGuiApplication.clipboard().setImage(self.preview_image())
        self.notify.emit("Preview copied as a picture")

    def save_preview(self, path) -> bool:
        target = Path(path)
        if target.suffix.lower() != ".png":
            target = target.with_name(target.name + ".png")
        if not self.preview_image().save(str(target), "PNG"):
            self.status.emit(f"The preview could not be saved to {target}", True)
            return False
        self.last_dir = str(target.parent)
        self.status.emit(f"Preview saved: {target}", False)
        self.notify.emit(f"Saved {target.name}")
        return True

    def save_preview_dialog(self) -> None:
        start = str(Path(self.last_dir or str(Path.home())) / f"preview-{self._kind}.png")
        path, _ = QFileDialog.getSaveFileName(self, "Save preview", start, "PNG picture (*.png)")
        if path:
            self.save_preview(path)


# ======================================================================================
# Tool 5: values
# ======================================================================================


class ValuesPage(QWidget):
    copyRequested = Signal(str, str)

    COLUMNS = ("", "Name", "HEX", "RGB", "HSL", "CMYK (approx.)")

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self._colors: list[str] = []
        self._names: list[str] = []
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 8, 0, 0)
        layout.setSpacing(8)
        self.table = QTableWidget(0, len(self.COLUMNS))
        self.table.setHorizontalHeaderLabels(self.COLUMNS)
        self.table.verticalHeader().hide()
        self.table.setShowGrid(False)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.table.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        header = self.table.horizontalHeader()
        header.setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.Fixed)
        header.setDefaultAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
        self.table.setColumnWidth(0, 44)
        layout.addWidget(self.table, 1)
        layout.addWidget(
            small_label(
                "Click a value to copy it. CMYK is the plain arithmetic conversion: real print values depend on "
                "the printer and paper profile, so check a proof before sending anything to press."
            )
        )
        row = QHBoxLayout()
        self.copy_button = QPushButton("Copy the whole table")
        self.copy_button.setToolTip("Tab-separated, so it pastes into a spreadsheet or a document table")
        row.addWidget(self.copy_button)
        row.addStretch(1)
        layout.addLayout(row)
        self.table.cellClicked.connect(self._cell_clicked)
        self.copy_button.clicked.connect(
            lambda: self.copyRequested.emit(designer.values_table(self._colors, self._names), "Table copied")
        )

    def set_palette(self, colors: Sequence[str], names: Sequence[str]) -> None:
        self._colors, self._names = list(colors), list(names)
        self.table.setRowCount(len(colors))
        for row, color in enumerate(colors):
            holder = QWidget()
            box = QHBoxLayout(holder)
            box.setContentsMargins(12, 0, 0, 0)
            block = ColorBlock(22)
            block.set_color(color)
            box.addWidget(block)
            self.table.setItem(row, 0, QTableWidgetItem())
            self.table.setCellWidget(row, 0, holder)
            values = (
                names[row] if row < len(names) else "",
                color,
                designer.rgb_text(color),
                designer.hsl_text(color),
                designer.cmyk_text(color),
            )
            for column, value in enumerate(values, 1):
                item = QTableWidgetItem(value)
                if column == 2:
                    item.setFont(theme.mono_font(9.5))
                self.table.setItem(row, column, item)
            self.table.setRowHeight(row, 36)
        self.copy_button.setEnabled(bool(colors))

    def _cell_clicked(self, row: int, column: int) -> None:
        item = self.table.item(row, column)
        if column and item is not None and item.text():
            self.copyRequested.emit(item.text(), f"{item.text()} copied")
        self.table.clearSelection()


# ======================================================================================
# The tab itself
# ======================================================================================


class StudioPage(QWidget):
    """The whole "My palette" tab."""

    notify = Signal(str)  # a short confirmation for the window to show
    status = Signal(str, bool)  # a status-line message, and whether it is an error
    changed = Signal()  # the palette's colors changed
    libraryChanged = Signal()  # a palette was saved to the library
    libraryRequested = Signal()  # the user wants to see the Library tab
    projectRequested = Signal()  # the user wants to see the Your project tab

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.project = Project()
        self.library = Library()  # palettes saved to come back to (shown on the Library tab)
        self._undo: list[tuple[NamedPalette, list[PaletteColor]]] = []
        self.project_path = ""
        self.last_dir = ""
        self.stem = "colorroom"
        self._build()
        self.refresh()

    # ----------------------------------------------------------------------------------
    # Construction
    # ----------------------------------------------------------------------------------

    def _build(self) -> None:
        root = QHBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        self.splitter = QSplitter(Qt.Orientation.Horizontal)
        self.splitter.setHandleWidth(12)
        self.splitter.setChildrenCollapsible(False)
        root.addWidget(self.splitter)

        # --- left: the palette ---------------------------------------------------------
        self.panel = Panel("My palette", "Collect colors here, then check and export them.")
        self.palette_menu_button = QPushButton("Palette")
        self.palette_menu_button.setToolTip("New, rename, duplicate or delete a palette; open or save a palette file")
        menu = QMenu(self.palette_menu_button)
        menu.addAction("New palette").triggered.connect(lambda: self.new_palette())
        menu.addAction("Rename…").triggered.connect(self.rename_palette_dialog)
        menu.addAction("Duplicate").triggered.connect(self.duplicate_palette)
        menu.addAction("Delete").triggered.connect(self.delete_palette)
        menu.addSeparator()
        menu.addAction("Open palette file…").triggered.connect(self.open_project_dialog)
        menu.addAction("Save palette file…").triggered.connect(self.save_project_dialog)
        self.palette_menu_button.setMenu(menu)
        self.presets_button = QPushButton("Library")
        self.presets_button.setToolTip("Save this palette to your library, or open one you saved before")
        self.presets_menu = QMenu(self.presets_button)
        self.presets_button.setMenu(self.presets_menu)
        self.panel.header.addWidget(self.presets_button, 0, Qt.AlignmentFlag.AlignTop)
        self.panel.header.addWidget(self.palette_menu_button, 0, Qt.AlignmentFlag.AlignTop)

        self.palette_combo = QComboBox()
        self.palette_combo.setToolTip("Switch between your palettes")
        self.panel.body.addWidget(self.palette_combo)

        self.list = ColorList()
        self.list.set_empty_text(
            "No colors yet.\n\nOn the Image tab, select rows in the color table (Ctrl or Shift for several) and "
            "choose “Add selected to My palette”, or add a whole design palette. You can also type HEX codes here."
        )
        self.panel.body.addWidget(self.list, 1)

        edit = FlowArea(6)
        self.add_button = QPushButton("Add HEX…")
        self.add_button.setToolTip("Type or paste one or more HEX codes")
        self.paste_button = QPushButton("Paste HEX")
        self.paste_button.setToolTip("Add every HEX code found on the clipboard")
        self.name_button = QPushButton("Name…")
        self.lock_button = QPushButton("Lock")
        self.lock_button.setToolTip("Locked colors are kept by Remove and Clear")
        self.sort_button = QPushButton("Sort")
        sort_menu = QMenu(self.sort_button)
        for key, label in (("hue", "By hue"), ("lightness", "Light to dark"), ("saturation", "Most vivid first")):
            sort_menu.addAction(label).triggered.connect(lambda _=False, k=key: self.sort_colors(k))
        self.sort_button.setMenu(sort_menu)
        for button in (self.add_button, self.paste_button, self.name_button, self.lock_button, self.sort_button):
            edit.add(button)
        self.panel.body.addWidget(edit)
        remove = FlowArea(6)
        self.remove_button = QPushButton("Delete selected")
        self.remove_button.setToolTip("Delete the ticked colors (Delete key). Locked colors are kept.")
        self.clear_button = QPushButton("Delete all")
        self.clear_button.setToolTip("Delete every color in this palette. Locked colors are kept.")
        self.select_all_button = QPushButton("Select all")
        self.undo_button = QPushButton("Undo")
        self.undo_button.setProperty("link", True)
        self.undo_button.setToolTip("Bring back what you just deleted or reordered (Ctrl+Z)")
        for button in (self.select_all_button, self.remove_button, self.clear_button, self.undo_button):
            remove.add(button)
        self.panel.body.addWidget(remove)
        self.count_label = small_label()
        self.panel.body.addWidget(self.count_label)

        send = FlowArea(6)
        self.copy_hex_button = QPushButton("Copy HEX codes")
        self.copy_hex_button.setProperty("primary", True)
        self.copy_hex_button.setToolTip("All HEX codes as text, separated by commas")
        self.copy_swatches_button = QPushButton("Copy as swatches")
        self.copy_swatches_button.setToolTip(
            "Copies the colors as vector squares. Paste into Figma or Illustrator to get one shape per color."
        )
        self.copy_image_button = QPushButton("Copy as image")
        self.copy_image_button.setToolTip("Copies a picture of the swatches. Paste into Photoshop, a document or a chat.")
        self.export_button = QPushButton("Export palette…")
        self.export_button.setToolTip(
            "Save the palette's colors as swatches for Adobe apps, Figma, CSS and more.\n"
            "To recolor your own design file and save it, use the Your project tab."
        )
        export_menu = QMenu(self.export_button)
        for key, (label, extension) in {**PALETTE_FORMATS, **STUDIO_FORMATS}.items():
            export_menu.addAction(f"{label}  ({extension})").triggered.connect(lambda _=False, k=key: self.export_dialog(k))
        self.export_button.setMenu(export_menu)
        for button in (self.copy_hex_button, self.copy_swatches_button, self.copy_image_button, self.export_button):
            send.add(button)
        self.panel.body.addWidget(send)
        self.splitter.addWidget(self.panel)

        # --- right: the tools ----------------------------------------------------------
        self.tools_panel = Panel("Palette tools", "Everything here works on the palette on the left.")
        self.favorite_button = QPushButton("☆ Favorite")
        self.favorite_button.setCheckable(True)
        self.favorite_button.setToolTip("Save this palette to your Library as a favorite")
        self.tools_panel.header.addWidget(self.favorite_button, 0, Qt.AlignmentFlag.AlignTop)
        self.tabs = QTabWidget()
        self.tabs.setDocumentMode(True)
        self.contrast = ContrastPage()
        self.vision = VisionPage()
        self.ramp = RampPage()
        self.mockups = MockupPage()
        self.values = ValuesPage()
        for page, title in (
            (self.mockups, "Previews"),  # first: the preview is what the app is for
            (self.contrast, "Contrast"),
            (self.vision, "Color vision"),
            (self.ramp, "Tints && shades"),
            (self.values, "Values"),
        ):
            self.tabs.addTab(page, title)
        self.tools_panel.body.addWidget(self.tabs, 1)
        self.splitter.addWidget(self.tools_panel)
        self.panel.setMinimumWidth(330)
        self.tools_panel.setMinimumWidth(520)
        self.splitter.setSizes([400, 1000])
        self.splitter.setStretchFactor(0, 0)
        self.splitter.setStretchFactor(1, 1)

        # --- wiring --------------------------------------------------------------------
        self.palette_combo.activated.connect(self.switch_palette)
        self.list.itemSelectionChanged.connect(self._selection_changed)
        self.list.reordered.connect(self._reordered)
        self.list.itemDoubleClicked.connect(lambda _item: self.name_dialog())
        self.add_button.clicked.connect(self.add_dialog)
        self.paste_button.clicked.connect(self.paste_colors)
        self.name_button.clicked.connect(self.name_dialog)
        self.lock_button.clicked.connect(self.toggle_lock)
        self.remove_button.clicked.connect(self.remove_selected)
        self.clear_button.clicked.connect(self.clear_colors)
        self.select_all_button.clicked.connect(self.toggle_select_all)
        self.undo_button.clicked.connect(self.undo)
        self.list.deleteRequested.connect(self.remove_row)
        QShortcut(QKeySequence(Qt.Key.Key_Delete), self.list, self.remove_selected, context=Qt.ShortcutContext.WidgetShortcut)
        QShortcut(QKeySequence.StandardKey.Undo, self, self.undo, context=Qt.ShortcutContext.WidgetWithChildrenShortcut)
        self._rebuild_presets_menu()
        self.copy_hex_button.clicked.connect(self.copy_hex)
        self.favorite_button.clicked.connect(self.toggle_favorite)
        self.copy_swatches_button.clicked.connect(self.copy_swatches)
        self.copy_image_button.clicked.connect(self.copy_image)
        for page in (self.ramp, self.values, self.contrast):
            page.copyRequested.connect(self.copy_text)
        for page in (self.contrast, self.mockups):
            page.notify.connect(self.notify)
            page.status.connect(self.status)
        self.ramp.paletteRequested.connect(self._palette_from_ramp)
        self.mockups.projectRequested.connect(self.projectRequested)

    # ----------------------------------------------------------------------------------
    # State
    # ----------------------------------------------------------------------------------

    @property
    def palette(self) -> NamedPalette:
        return self.project.current

    def colors(self) -> list[str]:
        return self.palette.hexes()

    def refresh(self, keep: Sequence[int] = ()) -> None:
        """Redraw everything from the project."""
        palette = self.palette
        self.palette_combo.blockSignals(True)
        self.palette_combo.clear()
        for item in self.project.palettes:
            self.palette_combo.addItem(f"{item.name}  ({len(item.colors)})")
        self.palette_combo.setCurrentIndex(self.project.active)
        self.palette_combo.blockSignals(False)
        self.palette_combo.setVisible(len(self.project.palettes) > 1)
        self.panel.title.setText(palette.name)
        self.list.show_colors(palette.colors, keep)
        colors, names = palette.hexes(), palette.names()
        for page in (self.contrast, self.vision, self.ramp, self.mockups, self.values):
            page.set_palette(colors, names)
        count = len(colors)
        self.count_label.setText(
            f"{count} of {designer.MAX_COLORS} colors · tick to select · × deletes · drag to reorder · double-click to name"
            if count
            else f"Up to {designer.MAX_COLORS} colors per palette."
        )
        for button in (
            self.copy_hex_button,
            self.copy_swatches_button,
            self.copy_image_button,
            self.export_button,
            self.sort_button,
            self.clear_button,
            self.select_all_button,
        ):
            button.setEnabled(bool(count))
        self.undo_button.setEnabled(bool(self._undo))
        self._selection_changed()
        self._rebuild_presets_menu()
        self.changed.emit()

    def _selection_changed(self) -> None:
        rows = self.list.selected_rows()
        self.name_button.setEnabled(len(rows) == 1)
        self.lock_button.setEnabled(bool(rows))
        self.remove_button.setEnabled(bool(rows))
        self.remove_button.setText(f"Delete {len(rows)} selected" if len(rows) > 1 else "Delete selected")
        everything = bool(rows) and len(rows) == self.list.count()
        self.select_all_button.setText("Select none" if everything else "Select all")
        colors = self.palette.colors
        all_locked = bool(rows) and all(colors[row].locked for row in rows if row < len(colors))
        self.lock_button.setText("Unlock" if all_locked else "Lock")

    def _reordered(self) -> None:
        self.palette.colors = self.list.colors()
        self.refresh(self.list.selected_rows())

    def set_image(self, rgba: Optional[np.ndarray], stem: str = "colorroom") -> None:
        self.stem = stem or "colorroom"
        self.vision.set_image(rgba)
        self.mockups.set_image(self.vision._rgba)  # the same small copy serves both previews

    # ----------------------------------------------------------------------------------
    # Colors
    # ----------------------------------------------------------------------------------

    def add_colors(self, colors: Sequence[str], quiet: bool = False) -> int:
        """Add HEX colors to the current palette. Returns how many were new."""
        added, duplicates, dropped = self.palette.add(colors)
        if added:
            first = len(self.palette.colors) - added
            self.refresh(range(first, len(self.palette.colors)))
        if not quiet:
            parts = []
            if added:
                parts.append(f"{added} {'color' if added == 1 else 'colors'} added to {self.palette.name}")
            if duplicates:
                parts.append(f"{duplicates} already there")
            if dropped:
                parts.append(f"{dropped} left out (a palette holds {designer.MAX_COLORS})")
            self.notify.emit(" · ".join(parts) if parts else "Nothing to add")
        return added

    def add_text(self, text: str) -> int:
        colors = designer.parse_hex_list(text)
        if not colors:
            self.notify.emit("No HEX codes found. They look like #B47A3C.")
            return 0
        return self.add_colors(colors)

    def add_dialog(self) -> None:
        text, accepted = QInputDialog.getText(
            self, "Add colors", "HEX codes (one or several, separated by spaces or commas):"
        )
        if accepted and text.strip():
            self.add_text(text)

    def paste_colors(self) -> None:
        self.add_text(QGuiApplication.clipboard().text())

    def _remember(self) -> None:
        """Keep the palette as it is now, so the next change can be undone."""
        self._undo.append((self.palette, [PaletteColor(c.hex, c.name, c.locked) for c in self.palette.colors]))
        del self._undo[:-20]

    def undo(self) -> None:
        """Bring back the palette as it was before the last delete or sort."""
        while self._undo:
            palette, colors = self._undo.pop()
            if any(palette is known for known in self.project.palettes):
                palette.colors = colors
                self.project.active = next(i for i, known in enumerate(self.project.palettes) if known is palette)
                self.refresh()
                self.notify.emit("Undone")
                return
        self.refresh()

    def _removed(self, removed: int, kept: int) -> None:
        if not removed:
            self._undo.pop()
        self.refresh()
        parts = []
        if removed:
            parts.append(f"{removed} {'color' if removed == 1 else 'colors'} deleted · Undo brings {'it' if removed == 1 else 'them'} back")
        if kept:
            parts.append(f"{kept} locked {'color' if kept == 1 else 'colors'} kept")
        self.notify.emit(" · ".join(parts) if parts else "Nothing to delete")

    def remove_selected(self) -> None:
        """Delete the ticked colors (locked ones stay)."""
        rows = self.list.selected_rows()
        if not rows:
            return
        self._remember()
        removed = self.palette.remove(rows)
        self._removed(removed, len(rows) - removed)

    def remove_row(self, row: int) -> None:
        """Delete one color: the × on its row."""
        self._remember()
        self._removed(self.palette.remove([row]), 0)

    def clear_colors(self) -> None:
        """Delete all: every color that is not locked."""
        total = len(self.palette.colors)
        self._remember()
        removed = self.palette.clear_unlocked()
        self._removed(removed, total - removed)

    def toggle_select_all(self) -> None:
        if len(self.list.selected_rows()) == self.list.count():
            self.list.clearSelection()
        else:
            self.list.selectAll()

    def toggle_lock(self) -> None:
        rows = self.list.selected_rows()
        colors = self.palette.colors
        lock = not all(colors[row].locked for row in rows)
        for row in rows:
            colors[row].locked = lock
        self.refresh(rows)

    def name_color(self, position: int, name: str) -> None:
        if 0 <= position < len(self.palette.colors):
            self.palette.colors[position].name = name.strip()[:60]
            self.refresh([position])

    def name_dialog(self) -> None:
        rows = self.list.selected_rows()
        if len(rows) != 1:
            return
        color = self.palette.colors[rows[0]]
        name, accepted = QInputDialog.getText(self, "Name this color", f"Name for {color.hex} (leave empty for none):", text=color.name)
        if accepted:
            self.name_color(rows[0], name)

    def sort_colors(self, by: str) -> None:
        self._remember()
        self.palette.sort(by)
        self.refresh()

    # ----------------------------------------------------------------------------------
    # Palettes and project files
    # ----------------------------------------------------------------------------------

    def new_palette(self, name: str = "Palette", colors: Sequence[PaletteColor] = ()) -> None:
        if not self.palette.colors and len(self.project.palettes) == 1 and colors:
            self.palette.name = self.project.unique_name(name) if name != self.palette.name else name
            self.palette.colors = list(colors)[: designer.MAX_COLORS]
        else:
            self.project.new_palette(name, list(colors)[: designer.MAX_COLORS])
        self.refresh()

    def _palette_from_ramp(self, name: str, steps: list) -> None:
        self.project.new_palette(name, [PaletteColor(color, label) for label, color in steps])
        self.refresh()
        self.notify.emit(f"New palette: {self.palette.name}")

    def switch_palette(self, position: int) -> None:
        self.project.active = position
        self.refresh()

    def rename_palette(self, name: str) -> None:
        name = name.strip()[:80]
        if name and name != self.palette.name:
            current = self.palette
            current.name = ""
            current.name = self.project.unique_name(name)
            self.refresh()

    def rename_palette_dialog(self) -> None:
        name, accepted = QInputDialog.getText(self, "Rename palette", "Palette name:", text=self.palette.name)
        if accepted:
            self.rename_palette(name)

    def duplicate_palette(self) -> None:
        self.project.new_palette(f"{self.palette.name} copy", self.palette.colors)
        self.refresh()

    def delete_palette(self) -> None:
        self.project.delete_current()
        self.refresh()

    # ----------------------------------------------------------------------------------
    # Library: palettes saved to come back to (the Library tab shows all of them)
    # ----------------------------------------------------------------------------------

    def library_match(self) -> Optional[LibraryEntry]:
        """The library palette with exactly the colors of the current palette, if there is one.

        A favorite is preferred when several saved palettes have the same colors.
        """
        colors = self.colors()
        if not colors:
            return None
        same = [entry for entry in self.library.entries if entry.palette.hexes() == colors]
        return next((entry for entry in same if entry.favorite), same[0] if same else None)

    def _sync_favorite_button(self) -> None:
        match = self.library_match()
        starred = match is not None and match.favorite
        self.favorite_button.setEnabled(bool(self.colors()))
        self.favorite_button.setChecked(starred)
        self.favorite_button.setText("★ Favorite" if starred else "☆ Favorite")
        if starred:
            self.favorite_button.setToolTip(f"In your Library favorites as “{match.palette.name}”. Click to remove the star.")
        elif match is not None:
            self.favorite_button.setToolTip(f"Already in your Library as “{match.palette.name}”. Click to make it a favorite.")
        else:
            self.favorite_button.setToolTip("Save this palette to your Library as a favorite")

    def toggle_favorite(self) -> None:
        """The star in the Palette tools header: favorite this palette in the library.

        A palette that is not in the library yet is saved there as a favorite. One that is
        already there is starred (or un-starred) instead of being saved a second time.
        """
        if not self.colors():
            self._sync_favorite_button()
            return
        match = self.library_match()
        if match is None:
            self.save_to_library(favorite=True)  # tells the user and announces the change
        else:
            match.favorite = not match.favorite
            self._rebuild_presets_menu()
            self.libraryChanged.emit()
            self.notify.emit(
                f"Added to favorites: {match.palette.name}" if match.favorite else f"Removed from favorites: {match.palette.name}"
            )
        self._sync_favorite_button()

    def _rebuild_presets_menu(self) -> None:
        self._sync_favorite_button()
        menu = self.presets_menu
        menu.clear()
        menu.setStyleSheet("QMenu { icon-size: 96px; }")
        save = menu.addAction("Save this palette to the library…")
        save.setEnabled(bool(self.colors()))
        save.triggered.connect(self.save_to_library_dialog)
        star = menu.addAction("Save as a favorite")
        star.setEnabled(bool(self.colors()))
        star.triggered.connect(lambda: self.save_to_library(favorite=True))
        menu.addSeparator()
        entries = self.library.entries
        if not entries:
            empty = menu.addAction("Nothing saved yet")
            empty.setEnabled(False)
        favorites = self.library.favorites()
        recent = [entry for entry in reversed(entries) if not entry.favorite][:8]
        for title, group in (("Favorites", favorites[:12]), ("Recently saved", recent)):
            if not group:
                continue
            header = menu.addAction(title.upper())
            header.setEnabled(False)
            for entry in group:
                label = f"{'★ ' if entry.favorite else ''}{entry.palette.name}   ({len(entry.palette.colors)})"
                action = menu.addAction(strip_icon(entry.palette.hexes()), label)
                action.setToolTip("Open this palette")
                action.triggered.connect(lambda _=False, e=entry: self.open_entry(e))
        menu.addSeparator()
        menu.addAction("Open the Library tab").triggered.connect(self.libraryRequested)

    def save_to_library(self, name: str = "", folder: str = "", favorite: bool = False) -> Optional[LibraryEntry]:
        """Save a copy of the current palette to the library. Returns the saved entry."""
        if not self.colors():
            self.notify.emit("Add some colors before saving to the library.")
            return None
        entry = self.library.add(self.palette, folder, name, favorite)
        self._rebuild_presets_menu()
        self.libraryChanged.emit()
        where = f" in {entry.folder}" if entry.folder else ""
        self.notify.emit(f"Saved to the library{where}: {entry.palette.name}")
        return entry

    def save_to_library_dialog(self) -> None:
        name, accepted = QInputDialog.getText(self, "Save to the library", "Name for this palette:", text=self.palette.name)
        if not accepted:
            return
        folder = ""
        if self.library.folders:
            choices = ["(no folder)", *self.library.folders]
            picked, accepted = QInputDialog.getItem(self, "Save to the library", "Folder:", choices, 0, False)
            if not accepted:
                return
            folder = "" if picked == choices[0] else picked
        self.save_to_library(name, folder)

    def open_entry(self, entry: LibraryEntry) -> None:
        """Open a copy of a library palette here. Your other palettes are left alone."""
        colors = [PaletteColor(c.hex, c.name) for c in entry.palette.colors]
        if not self.palette.colors and len(self.project.palettes) == 1:
            self.palette.name, self.palette.colors = entry.palette.name, colors  # fill the empty starting palette
        else:
            self.project.new_palette(entry.palette.name, colors)
        self.refresh()
        self.notify.emit(f"Opened from the library: {entry.palette.name}")

    def set_library(self, library: Library) -> None:
        self.library = library
        self._rebuild_presets_menu()

    def save_project(self, path) -> bool:
        target = Path(path)
        if target.suffix.lower() != designer.PROJECT_EXTENSION:
            target = target.with_name(target.name + designer.PROJECT_EXTENSION)
        try:
            self.project.save(target)
        except OSError as error:
            self.status.emit(f"The palette file could not be saved: {error}", True)
            return False
        self.project_path, self.last_dir = str(target), str(target.parent)
        self.status.emit(f"Palette file saved: {target}", False)
        self.notify.emit(f"Saved {target.name}")
        return True

    def open_project(self, path) -> bool:
        try:
            project = Project.load(path)
        except (OSError, ValueError) as error:
            self.status.emit(f"That palette file could not be opened: {error}", True)
            return False
        self.project = project
        self.project_path, self.last_dir = str(path), str(Path(path).parent)
        self.refresh()
        self.status.emit(f"Palette file opened: {path}", False)
        return True

    def _start_dir(self) -> str:
        return self.last_dir or str(Path.home())

    def save_project_dialog(self) -> None:
        start = self.project_path or str(Path(self._start_dir()) / f"{self.stem}-palettes{designer.PROJECT_EXTENSION}")
        path, _ = QFileDialog.getSaveFileName(self, "Save palette file", start, f"Colorroom palettes (*{designer.PROJECT_EXTENSION})")
        if path:
            self.save_project(path)

    def open_project_dialog(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self, "Open palette file", self._start_dir(), f"Colorroom palettes (*{designer.PROJECT_EXTENSION});;All files (*)"
        )
        if path:
            self.open_project(path)

    # ----------------------------------------------------------------------------------
    # Copy and export
    # ----------------------------------------------------------------------------------

    def copy_text(self, text: str, confirmation: str) -> None:
        QGuiApplication.clipboard().setText(text)
        self.notify.emit(confirmation)

    def copy_hex(self) -> None:
        if self.colors():
            self.copy_text(palette_text(self.colors()), "HEX codes copied")

    def swatches_svg(self) -> str:
        return designer.swatches_svg(self.colors(), self.palette.names(), self.palette.name)

    def copy_swatches(self) -> None:
        if not self.colors():
            return
        svg = self.swatches_svg()
        data = QMimeData()
        data.setText(svg)  # Figma and Illustrator turn pasted SVG text into shapes
        data.setData("image/svg+xml", QByteArray(svg.encode("utf-8")))
        QGuiApplication.clipboard().setMimeData(data)
        self.notify.emit("Swatches copied. Paste into Figma or Illustrator.")

    def swatch_image(self) -> QImage:
        """A picture of the palette: one labelled block per color."""
        colors, names = self.colors() or ["#FFFFFF"], self.palette.names()
        side, gap, margin = 180, 18, 28
        text_height = 64 if any(names) else 40
        width = margin * 2 + len(colors) * side + (len(colors) - 1) * gap
        image = QImage(width, margin * 2 + side + text_height, QImage.Format.Format_RGB32)
        image.fill(QColor("#FFFEFA"))
        painter = QPainter(image)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        for position, color in enumerate(colors):
            x = margin + position * (side + gap)
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QColor(color))
            painter.drawRoundedRect(QRectF(x, margin, side, side), 14, 14)
            font = theme.mono_font(13)
            font.setPixelSize(20)
            painter.setFont(font)
            painter.setPen(QColor("#211D26"))
            painter.drawText(QRectF(x, margin + side + 8, side, 26), Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter, color)
            if position < len(names) and names[position]:
                small = theme.small_font(10)
                small.setPixelSize(16)
                painter.setFont(small)
                painter.setPen(QColor("#635C69"))
                painter.drawText(
                    QRectF(x, margin + side + 34, side, 22), Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter, names[position]
                )
        painter.end()
        return image

    def copy_image(self) -> None:
        if self.colors():
            QGuiApplication.clipboard().setImage(self.swatch_image())
            self.notify.emit("Picture of the swatches copied. Paste into Photoshop or a document.")

    def export_to(self, path, kind: str) -> bool:
        """Save the palette to `path` in the format `kind`. Returns True if it worked."""
        target = Path(path)
        colors, names = self.colors(), self.palette.names()
        try:
            if kind == "svg":
                target.write_text(self.swatches_svg(), encoding="utf-8", newline="\n")
            elif kind == "tokens":
                target.write_text(designer.design_tokens(colors, names, self.palette.name), encoding="utf-8", newline="\n")
            else:
                save_palette(colors, target, self.palette.name, names)
        except (OSError, ValueError) as error:
            self.status.emit(f"The palette could not be saved: {error}", True)
            return False
        self.last_dir = str(target.parent)
        swatches = target.suffix.lower() == ".ase"
        how = "  To load it in Photoshop: Window > Swatches, then the panel menu > Import Swatches." if swatches else ""
        self.status.emit(f"Palette saved: {target}{how}", False)
        self.notify.emit(f"Saved {target.name}")
        return True

    def export_dialog(self, kind: str) -> None:
        if not self.colors():
            return
        formats = {**PALETTE_FORMATS, **STUDIO_FORMATS}
        label, extension = formats[kind]
        name = designer.slug(self.palette.name, "palette")
        start = str(Path(self._start_dir()) / f"{name}{extension}")
        path, _ = QFileDialog.getSaveFileName(self, "Export palette", start, f"{label} (*{extension})")
        if not path:
            return
        target = Path(path)
        if not target.name.lower().endswith(extension):
            target = target.with_name(target.name + extension)
        self.export_to(target, kind)
