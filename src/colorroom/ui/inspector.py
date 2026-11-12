"""The shade inspector: related shades of one color, filtered by tone and by difference.

This finishes the "Shade range" idea that Colorroom 1.7 had in its page but never
wired up: the Maximum difference slider narrows the list to the closest shades.
"""

from __future__ import annotations

from typing import Optional

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import QHBoxLayout, QLabel, QPushButton, QScrollArea, QSizePolicy, QSlider, QVBoxLayout, QWidget

from ..core.index import ColorIndex
from ..core.shades import RelatedShades, related_shades
from . import theme
from .inventory import format_share
from .widgets import Chip, ColorBlock, ElidedLabel, FlowArea, Panel, field_label, small_label

_COPY_LIMIT = 5000


def format_difference(difference: float) -> str:
    return "<0.1%" if difference < 0.1 else f"{difference:.1f}%"


class InspectorPanel(Panel):
    shadeHovered = Signal(object)  # ShadeEntry or None
    shadeChosen = Signal(int)  # distinct index of the shade that was clicked
    copyRequested = Signal(str, str)  # text for the clipboard, confirmation message
    pickRequested = Signal(str)  # HEX color to add to or remove from the picks

    PAGE_SIZE = 16
    _IDLE_TEXT = "Click a swatch, a table row or a spot in the image to explore its related shades."

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__("Shade inspector", self._IDLE_TEXT, parent)
        self._index: Optional[ColorIndex] = None
        self._related: Optional[RelatedShades] = None
        self._color = ""
        self._palette_color: Optional[str] = None
        self._tone: Optional[int] = None
        self._page = 0
        self._tone_chips: list[tuple[Optional[int], Chip]] = []

        self._content = QWidget()
        column = QVBoxLayout(self._content)
        column.setContentsMargins(0, 0, 0, 0)
        column.setSpacing(8)

        # --- the inspected color -------------------------------------------------------
        subject = QHBoxLayout()
        subject.setSpacing(8)
        self.palette_block = ColorBlock(42)
        self.block = ColorBlock(42)
        subject.addWidget(self.palette_block)
        subject.addWidget(self.block)
        names = QVBoxLayout()
        names.setSpacing(0)
        self.name = ElidedLabel()
        self.name.setFont(theme.mono_font(12.5))
        self.detail = ElidedLabel()
        self.detail.setProperty("small", True)
        names.addWidget(self.name)
        names.addWidget(self.detail)
        subject.addLayout(names, 1)
        self.copy_button = QPushButton("Copy HEX")
        self.pick_button = QPushButton("Pick")
        self.pick_button.setCheckable(True)
        self.pick_button.setToolTip("Add this color to your palette picks")
        for button in (self.copy_button, self.pick_button):
            button.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
            subject.addWidget(button)
        column.addLayout(subject)

        self.copy_palette_button = QPushButton()
        self.copy_palette_button.setProperty("link", True)
        self.copy_palette_button.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
        self.copy_palette_button.setToolTip("Copy the palette color itself rather than its closest image color")
        column.addWidget(self.copy_palette_button, 0, Qt.AlignmentFlag.AlignLeft)

        # --- filters -------------------------------------------------------------------
        self.tones = FlowArea(5)
        column.addWidget(self.tones)
        range_row = QHBoxLayout()
        range_row.setSpacing(8)
        range_row.addWidget(field_label("Maximum difference"))
        self.range_slider = QSlider(Qt.Orientation.Horizontal)
        self.range_slider.setRange(1, 1000)  # tenths of a percent
        self.range_slider.setValue(1000)
        self.range_slider.setPageStep(50)
        self.range_slider.setToolTip("Only list shades that differ from this color by up to this much")
        self.range_value = QLabel("100%")
        self.range_value.setMinimumWidth(46)
        self.range_value.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        range_row.addWidget(self.range_slider, 1)
        range_row.addWidget(self.range_value)
        column.addLayout(range_row)

        # --- the shades ----------------------------------------------------------------
        self.note = small_label()
        column.addWidget(self.note)
        self.shades = FlowArea(5)
        holder = QWidget()
        holder_layout = QVBoxLayout(holder)
        holder_layout.setContentsMargins(0, 0, 4, 0)
        holder_layout.addWidget(self.shades)
        holder_layout.addStretch(1)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        scroll.setWidget(holder)
        scroll.viewport().setAutoFillBackground(False)
        holder.setAutoFillBackground(False)
        column.addWidget(scroll, 1)

        pager = QHBoxLayout()
        pager.setSpacing(6)
        self.previous_button = QPushButton("Previous")
        self.next_button = QPushButton("More shades")
        self.next_button.setProperty("primary", True)
        self.copy_shades_button = QPushButton("Copy shades")
        self.copy_shades_button.setToolTip("Copy the HEX codes of every shade that matches the filters")
        pager.addWidget(self.previous_button)
        pager.addWidget(self.next_button)
        pager.addStretch(1)
        pager.addWidget(self.copy_shades_button)
        column.addLayout(pager)

        self.body.addWidget(self._content, 1)
        self._spacer = QWidget()
        self.body.addWidget(self._spacer, 1)

        self.copy_button.clicked.connect(self._copy_color)
        self.copy_palette_button.clicked.connect(self._copy_palette_color)
        self.pick_button.clicked.connect(lambda: self.pickRequested.emit(self._color))
        self.range_slider.valueChanged.connect(self._range_changed)
        self.previous_button.clicked.connect(lambda: self._turn(-1))
        self.next_button.clicked.connect(lambda: self._turn(1))
        self.copy_shades_button.clicked.connect(self._copy_shades)
        self.clear()

    # ----------------------------------------------------------------------------------
    # Subject
    # ----------------------------------------------------------------------------------

    def clear(self) -> None:
        """Forget the inspected color (used when a new image is opened)."""
        self._index = None
        self._related = None
        self._color = ""
        self._palette_color = None
        self._tone_chips = []
        self.tones.clear()
        self.shades.clear()
        self.range_slider.setValue(self.range_slider.maximum())
        self.subtitle.setText(self._IDLE_TEXT)
        self.subtitle.show()
        self._content.hide()
        self._spacer.show()

    @property
    def maximum(self) -> float:
        return self.range_slider.value() / 10

    @property
    def color(self) -> str:
        return self._color

    def set_subject(self, index: ColorIndex, distinct: int, palette_color: Optional[str], picked: bool) -> None:
        """Show the related shades of the image color at distinct index `distinct`."""
        color = index.hex(distinct)
        self._index = index
        self._related = related_shades(index, distinct)
        self._color = color
        self._palette_color = palette_color if palette_color and palette_color != color else None
        self._tone = None
        self._page = 0

        self.block.set_color(color)
        self.block.setToolTip(f"Image color {color}")
        self.palette_block.set_color(self._palette_color)
        self.palette_block.setVisible(self._palette_color is not None)
        amount = f"{index.count(distinct):,} px · {format_share(index.share(distinct))}"
        pixels = f"{amount} of the image"
        if self._palette_color:
            self.palette_block.setToolTip(f"Palette color {self._palette_color}")
            self.name.setText(f"{self._palette_color} → {color}")
            self.detail.setText(f"Closest image color · {amount}")
            self.copy_button.setText("Copy image HEX")
            self.copy_palette_button.setText(f"Copy palette HEX {self._palette_color}")
        else:
            self.name.setText(color)
            self.detail.setText(pixels)
            self.copy_button.setText("Copy HEX")
        self.copy_palette_button.setVisible(self._palette_color is not None)
        self.set_picked(picked)

        self.tones.clear()
        self._tone_chips = []
        summaries = self._related.tone_summaries()
        if summaries:
            everything = Chip("All tones", suffix=f"{len(self._related):,}", mono=False, checkable=True)
            everything.setChecked(True)
            everything.setToolTip("Every related shade, most similar first")
            everything.clicked.connect(lambda: self._set_tone(None))
            self.tones.add(everything)
            self._tone_chips.append((None, everything))
            for summary in summaries:
                chip = Chip(summary.name, summary.sample.color, f"{summary.count:,}", mono=False, checkable=True)
                chip.setToolTip(f"Only {summary.name.lower()} shades, most common first")
                chip.clicked.connect(lambda _=False, tone=summary.tone: self._set_tone(tone))
                chip.hovered.connect(lambda inside, entry=summary.sample: self.shadeHovered.emit(entry if inside else None))
                self.tones.add(chip)
                self._tone_chips.append((summary.tone, chip))
        self.tones.setVisible(bool(summaries))
        self.tones.updateGeometry()

        self.subtitle.hide()
        self._spacer.hide()
        self._content.show()
        self._refresh()

    def set_picked(self, picked: bool) -> None:
        self.pick_button.setChecked(picked)
        self.pick_button.setText("Picked ✓" if picked else "Pick")

    # ----------------------------------------------------------------------------------
    # Filters and paging
    # ----------------------------------------------------------------------------------

    def _set_tone(self, tone: Optional[int]) -> None:
        self._tone = tone
        self._page = 0
        for chip_tone, chip in self._tone_chips:
            chip.setChecked(chip_tone == tone)
        self._refresh()

    def _range_changed(self) -> None:
        self.range_value.setText(f"{self.maximum:g}%")
        self._page = 0
        if self._related is not None:
            self._refresh()

    def _turn(self, pages: int) -> None:
        self._page += pages
        self._refresh()

    def _refresh(self) -> None:
        related = self._related
        self.shadeHovered.emit(None)
        self.shades.clear()
        positions = related.select(self._tone, self.maximum)
        total = int(positions.size)
        pages = max(1, -(-total // self.PAGE_SIZE))
        self._page = max(0, min(self._page, pages - 1))
        start = self._page * self.PAGE_SIZE
        shown = related.entries(positions[start : start + self.PAGE_SIZE])
        for entry in shown:
            chip = Chip(entry.color, entry.color, format_difference(entry.difference))
            chip.setToolTip(f"{entry.color} · {entry.pixels:,} px. Click to inspect this shade.")
            chip.hovered.connect(lambda inside, e=entry: self.shadeHovered.emit(e if inside else None))
            chip.clicked.connect(lambda _=False, e=entry: self.shadeChosen.emit(e.index))
            self.shades.add(chip)
        self.shades.updateGeometry()

        if not len(related):
            self.note.setText("No other shades in this color family were found.")
            self.note.setToolTip("")
        elif not total:
            self.note.setText("No shades match these filters. Raise the maximum difference or choose All tones.")
            self.note.setToolTip("")
        else:
            order = "most similar first" if self._tone is None else "most common first"
            self.note.setText(
                f"{start + 1:,}–{start + len(shown):,} of {total:,} shades · {order} · % = how different it looks"
            )
            self.note.setToolTip(
                f"The percentage is an estimated visual difference from {self._color}: "
                "0% is identical, larger numbers look less alike."
            )
        self.previous_button.setVisible(self._page > 0)
        self.next_button.setVisible(self._page + 1 < pages)
        self.copy_shades_button.setEnabled(total > 0)
        self.range_slider.setEnabled(len(related) > 0)

    # ----------------------------------------------------------------------------------
    # Clipboard
    # ----------------------------------------------------------------------------------

    def _copy_color(self) -> None:
        if self._color:
            self.copyRequested.emit(self._color, f"{self._color} copied")

    def _copy_palette_color(self) -> None:
        if self._palette_color:
            self.copyRequested.emit(self._palette_color, f"{self._palette_color} copied")

    def _copy_shades(self) -> None:
        if self._related is None:
            return
        positions = self._related.select(self._tone, self.maximum)
        entries = self._related.entries(positions[:_COPY_LIMIT])
        text = ", ".join(entry.color for entry in entries)
        if positions.size > _COPY_LIMIT:
            message = f"Copied the first {_COPY_LIMIT:,} of {positions.size:,} shades"
        else:
            message = f"{len(entries):,} shade HEX code{'' if len(entries) == 1 else 's'} copied"
        self.copyRequested.emit(text, message)
