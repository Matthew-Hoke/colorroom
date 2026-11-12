"""The "Design palette" panel: style options, picked colors and the swatches."""

from __future__ import annotations

from typing import Optional, Sequence

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QComboBox,
    QHBoxLayout,
    QLabel,
    QMenu,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from ..core import palettes
from ..core.exporters import PALETTE_FORMATS
from . import theme
from .widgets import Chip, FlowArea, Panel, SwatchGrid, field_label, small_label


class PalettePanel(Panel):
    settingsChanged = Signal()  # style, color count or theme changed
    swatchHovered = Signal(object)  # PaletteEntry or None
    swatchClicked = Signal(object)  # PaletteEntry
    pickRemoved = Signal(str)  # HEX of a picked color to drop
    picksCleared = Signal()
    copyRequested = Signal()
    exportRequested = Signal(str)  # a key of PALETTE_FORMATS

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(
            "Design palette",
            "Hover a swatch to locate its image color. Click it for related shades sampled from the image.",
            parent,
        )
        # --- options -------------------------------------------------------------------
        options = QHBoxLayout()
        options.setSpacing(8)
        self.mode = QComboBox()
        self.mode.setMinimumWidth(150)
        self.mode.setMaxVisibleItems(20)
        for title, modes in palettes.MODE_GROUPS:
            self.mode.addItem(title.upper())
            header = self.mode.model().item(self.mode.count() - 1)
            header.setFlags(Qt.ItemFlag.NoItemFlags)
            header.setFont(theme.small_font(7.5))
            header.setForeground(QColor(theme.MUTED))
            for key, label in modes:
                self.mode.addItem(label, key)
        self.mode.setCurrentIndex(self.mode.findData("balanced"))
        self.size = QComboBox()
        for size in palettes.SIZES:
            self.size.addItem(str(size), size)
        self.size.setCurrentIndex(self.size.findData(palettes.DEFAULT_SIZE))
        self.theme = QComboBox()
        for name in palettes.THEMES:
            self.theme.addItem(name, name)

        options.addWidget(field_label("Style"))
        options.addWidget(self.mode, 1)
        self._size_label = field_label("Colors")
        options.addWidget(self._size_label)
        options.addWidget(self.size)
        self._theme_label = field_label("Theme")
        options.addWidget(self._theme_label)
        options.addWidget(self.theme)
        self.body.addLayout(options)

        # --- note, picks and swatches scroll together if the panel is short --------------
        content = QWidget()
        column = QVBoxLayout(content)
        column.setContentsMargins(0, 0, 4, 0)
        column.setSpacing(9)
        self.note = small_label()
        column.addWidget(self.note)
        self.picks = FlowArea(5)
        column.addWidget(self.picks)
        self.grid = SwatchGrid()
        self.grid.set_empty_text("Open an image to generate a palette.")
        column.addWidget(self.grid)
        column.addStretch(1)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        scroll.setWidget(content)
        scroll.viewport().setAutoFillBackground(False)
        content.setAutoFillBackground(False)
        self.body.addWidget(scroll, 1)

        # --- actions -------------------------------------------------------------------
        actions = QHBoxLayout()
        actions.setSpacing(8)
        self.copy_button = QPushButton("Copy palette")
        self.copy_button.setProperty("primary", True)
        self.copy_button.setToolTip("Copy the HEX codes, separated by commas")
        self.export_button = QPushButton("Export…")
        self.export_button.setToolTip("Save the palette for CSS, Adobe apps, GIMP and more")
        menu = QMenu(self.export_button)
        for key, (label, extension) in PALETTE_FORMATS.items():
            action = menu.addAction(f"{label}  ({extension})")
            action.triggered.connect(lambda _=False, k=key: self.exportRequested.emit(k))
        self.export_button.setMenu(menu)
        actions.addWidget(self.copy_button)
        actions.addWidget(self.export_button)
        actions.addStretch(1)
        self.body.addLayout(actions)

        self.mode.currentIndexChanged.connect(self._changed)
        self.size.currentIndexChanged.connect(self._changed)
        self.theme.currentIndexChanged.connect(self._changed)
        self.copy_button.clicked.connect(self.copyRequested)
        self.grid.swatchHovered.connect(self.swatchHovered)
        self.grid.swatchClicked.connect(self.swatchClicked)
        self._sync_options()
        self.set_ready(False)

    # ----------------------------------------------------------------------------------

    def current_mode(self) -> str:
        return self.mode.currentData() or "balanced"

    def current_size(self) -> int:
        return int(self.size.currentData())

    def current_theme(self) -> str:
        return self.theme.currentData()

    def set_mode(self, mode: str, notify: bool = False) -> None:
        """Switch style. With notify=False the caller refreshes the palette itself."""
        blocked = self.mode.blockSignals(not notify)
        self.mode.setCurrentIndex(self.mode.findData(mode))
        self.mode.blockSignals(blocked)
        self._sync_options()

    def set_ready(self, ready: bool) -> None:
        for widget in (self.mode, self.size, self.theme):
            widget.setEnabled(ready)
        if not ready:
            self.copy_button.setEnabled(False)
            self.export_button.setEnabled(False)

    def clear(self) -> None:
        self.note.setText("")
        self.picks.clear()
        self.grid.set_entries(())
        self.grid.set_empty_text("Open an image to generate a palette.")
        self.set_ready(False)

    def show_palette(self, palette: palettes.Palette, picks: Sequence[str]) -> None:
        self.note.setText(palette.note)
        self.grid.set_empty_text("Pick colors from the image or the color table.")
        self.grid.set_entries(palette.entries)
        self._show_picks(picks)
        has_colors = bool(palette.entries)
        self.copy_button.setEnabled(has_colors)
        self.export_button.setEnabled(has_colors)

    def _show_picks(self, picks: Sequence[str]) -> None:
        self.picks.clear()
        self.picks.setVisible(bool(picks))
        if not picks:
            return
        label = QLabel("Selected")
        label.setProperty("small", True)
        label.setFixedHeight(28)
        self.picks.add(label)
        for position, color in enumerate(picks):
            chip = Chip(color, color, "×")
            chip.setToolTip(f"Remove {'base color ' if position == 0 else ''}{color}")
            chip.clicked.connect(lambda _=False, c=color: self.pickRemoved.emit(c))
            self.picks.add(chip)
        if len(picks) > 1:
            clear = Chip("Clear all", mono=False)
            clear.setToolTip("Remove every picked color")
            clear.clicked.connect(self.picksCleared)
            self.picks.add(clear)
        self.picks.updateGeometry()

    def _sync_options(self) -> None:
        mode = self.current_mode()
        for widget in (self._size_label, self.size):
            widget.setVisible(palettes.uses_size(mode))
        for widget in (self._theme_label, self.theme):
            widget.setVisible(mode == "seasonal")

    def _changed(self) -> None:
        self._sync_options()
        self.settingsChanged.emit()
