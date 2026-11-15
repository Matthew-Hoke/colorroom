"""The "Whole document" window: one palette across every page.

It answers the questions a brand designer asks of a multi-page file: which colors are used
throughout, which appear on a single page, and which are almost but not quite the same.
"""

from __future__ import annotations

from typing import Optional, Sequence

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QGuiApplication
from PySide6.QtWidgets import (
    QAbstractItemView,
    QDialog,
    QHBoxLayout,
    QHeaderView,
    QPushButton,
    QSlider,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from ..core import documents
from ..core.exporters import palette_text
from . import theme
from .widgets import ColorBlock, FlowArea, field_label, heading, small_label

COLUMNS = ("", "HEX", "Used on", "Pages", "Note")


def page_list(pages: Sequence[int], limit: int = 12) -> str:
    """[0, 1, 2, 5] -> '1-3, 6' (page numbers as people count them)."""
    runs, start, previous = [], None, None
    for page in pages:
        if start is None:
            start = previous = page
        elif page == previous + 1:
            previous = page
        else:
            runs.append((start, previous))
            start = previous = page
    if start is not None:
        runs.append((start, previous))
    parts = [str(a + 1) if a == b else f"{a + 1}-{b + 1}" for a, b in runs]
    return ", ".join(parts[:limit]) + (" …" if len(parts) > limit else "")


class DocumentDialog(QDialog):
    addRequested = Signal(list)  # HEX colors to add to My palette
    pageRequested = Signal(int)  # 0-based page to show
    notify = Signal(str)

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Whole document")
        self.resize(820, 620)
        self._summaries: list = []
        self._pages = 0
        self._colors: list[documents.DocumentColor] = []
        layout = QVBoxLayout(self)
        layout.setContentsMargins(18, 16, 18, 16)
        layout.setSpacing(10)
        self.title = heading("Whole document", 16)
        layout.addWidget(self.title)
        self.summary = small_label()
        layout.addWidget(self.summary)

        row = QHBoxLayout()
        row.setSpacing(8)
        row.addWidget(field_label("Treat as the same color"))
        self.left_hint = small_label("only if identical", wrap=False)
        self.slider = QSlider(Qt.Orientation.Horizontal)
        self.slider.setRange(0, 80)  # thousandths of Oklab distance
        self.slider.setValue(30)
        self.slider.setToolTip("How different two colors may be and still count as one. Raise it to merge near-matches.")
        self.right_hint = small_label("even if clearly different", wrap=False)
        row.addWidget(self.left_hint)
        row.addWidget(self.slider, 1)
        row.addWidget(self.right_hint)
        layout.addLayout(row)

        self.table = QTableWidget(0, len(COLUMNS))
        self.table.setHorizontalHeaderLabels(COLUMNS)
        self.table.verticalHeader().hide()
        self.table.setShowGrid(False)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        header = self.table.horizontalHeader()
        header.setDefaultAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
        header.setSectionResizeMode(QHeaderView.ResizeMode.Interactive)
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.Fixed)
        header.setStretchLastSection(True)
        for column, width in enumerate((60, 100, 120, 170)):
            self.table.setColumnWidth(column, width)
        layout.addWidget(self.table, 1)
        self.warning = small_label()
        layout.addWidget(self.warning)

        buttons = FlowArea(6)
        self.add_all_button = QPushButton("Add all to My palette")
        self.add_all_button.setProperty("primary", True)
        self.add_selected_button = QPushButton("Add selected")
        self.copy_button = QPushButton("Copy HEX codes")
        self.go_button = QPushButton("Go to page")
        self.go_button.setToolTip("Show the first page that uses the selected color (or double-click a row)")
        for button in (self.add_all_button, self.add_selected_button, self.copy_button, self.go_button):
            buttons.add(button)
        layout.addWidget(buttons)

        self.slider.valueChanged.connect(lambda _value: self._rebuild())
        self.table.itemSelectionChanged.connect(self._selection_changed)
        self.table.cellDoubleClicked.connect(lambda row, _column: self._go(row))
        self.add_all_button.clicked.connect(lambda: self.addRequested.emit([item.color for item in self._colors]))
        self.add_selected_button.clicked.connect(lambda: self.addRequested.emit(self.selected_colors()))
        self.copy_button.clicked.connect(self._copy)
        self.go_button.clicked.connect(lambda: self._go(self._selected_rows()[0]) if self._selected_rows() else None)

    # ----------------------------------------------------------------------------------

    def threshold(self) -> float:
        return self.slider.value() / 1000

    def colors(self) -> list:
        return list(self._colors)

    def set_document(self, name: str, pages: int, summaries: Sequence) -> None:
        """Show the pages looked at so far (`summaries` may still be shorter than `pages`)."""
        self._summaries = [summary for summary in summaries if summary is not None]
        self._pages = pages
        self.title.setText(name)
        self.setWindowTitle(f"Whole document · {name}")
        self._rebuild()

    def _rebuild(self) -> None:
        self._colors = documents.merge_document_colors(self._summaries, self.threshold())
        looked, pages = len(self._summaries), self._pages
        everywhere = sum(1 for item in self._colors if len(item.pages) == looked and looked > 1)
        once = sum(1 for item in self._colors if len(item.pages) == 1 and looked > 1)
        progress = "" if looked >= pages else f" Still looking: {looked} of {pages} pages so far."
        self.summary.setText(
            f"{len(self._colors)} colors across {looked} {'page' if looked == 1 else 'pages'}: "
            f"{everywhere} on every page, {once} on one page only.{progress}"
        )
        close = documents.near_duplicates(self._colors, self.threshold())
        partners: dict[str, list[str]] = {}
        for first, second, _distance in close:
            partners.setdefault(first, []).append(second)
            partners.setdefault(second, []).append(first)
        self.table.setRowCount(len(self._colors))
        for row, item in enumerate(self._colors):
            holder = QWidget()
            box = QHBoxLayout(holder)
            box.setContentsMargins(16, 0, 0, 0)
            block = ColorBlock(22)
            block.set_color(item.color)
            box.addWidget(block)
            self.table.setItem(row, 0, QTableWidgetItem())
            self.table.setCellWidget(row, 0, holder)
            notes = []
            if item.defined:
                notes.append("Defined in the file (exact)")
            if looked > 1 and len(item.pages) == 1:
                notes.append("One page only")
            if len(item.variants) > 1:
                notes.append(f"{len(item.variants)} slightly different versions merged")
            if item.color in partners:
                notes.append("Very close to " + ", ".join(partners[item.color][:2]))
            used = "every page" if (looked > 1 and len(item.pages) == looked) else f"{len(item.pages)} of {looked}"
            for column, text in enumerate((item.color, used, page_list(item.pages), " · ".join(notes)), 1):
                cell = QTableWidgetItem(text)
                if column == 1:
                    cell.setFont(theme.mono_font(9.5))
                cell.setToolTip(", ".join(item.variants) if column == 4 and len(item.variants) > 1 else text)
                self.table.setItem(row, column, cell)
            self.table.setRowHeight(row, 36)
        if close:
            first, second, _distance = close[0]
            more = f" and {len(close) - 1} more {'pair' if len(close) == 2 else 'pairs'}" if len(close) > 1 else ""
            self.warning.setText(
                f"Check: {first} and {second}{more} are nearly the same. If they are meant to be one color, "
                "one of them is probably a slip. Move the slider right to merge them."
            )
        else:
            self.warning.setText("No near-duplicate colors at this setting.")
        self.add_all_button.setEnabled(bool(self._colors))
        self.copy_button.setEnabled(bool(self._colors))
        self._selection_changed()

    def _selected_rows(self) -> list[int]:
        return sorted({index.row() for index in self.table.selectedIndexes()})

    def selected_colors(self) -> list[str]:
        return [self._colors[row].color for row in self._selected_rows() if row < len(self._colors)]

    def _selection_changed(self) -> None:
        chosen = bool(self._selected_rows())
        self.add_selected_button.setEnabled(chosen)
        self.go_button.setEnabled(chosen)

    def _go(self, row: int) -> None:
        if 0 <= row < len(self._colors):
            self.pageRequested.emit(self._colors[row].pages[0])

    def _copy(self) -> None:
        colors = self.selected_colors() or [item.color for item in self._colors]
        QGuiApplication.clipboard().setText(palette_text(colors))
        self.notify.emit(f"{len(colors)} HEX codes copied")
