"""The "Your project" tab: open your own design file, try palettes on it, export the same type.

Made by Opus 5.5 (2026-11-14), for Fable 5.2 to review.

The window opens on this tab. Before a file is open it shows the empty state (a drop zone,
Open file… and the sample). With a file open:

    header   file name and facts ...................................... Open another file
    left     Before | After ................................................. More v
             the artwork, as large as fits (drag a box over it to leave an area alone)
    right    (scrolls) the palette picker and its colors, Try another arrangement,
             Change which color plays each role…, Colors in your file (a role for each),
             the photos line and Change photos too; the export report card below it
    footer   contrast line, accuracy line .......................... [ Export as PSD… | v ]

The engine moved here from Previews > Your image: `designer.find_layers` splits the picture
into its main colors, each main color takes a palette role (or Keep), `designer.recolor`
draws the result, and `file_export.export_upload` writes the same file type at full size, in
a background task. The rules that are not window code live in `core/project.py`.

Plain methods for tests sit beside every dialog: `load_upload` (open a file now),
`export_file`, `export_swatches`, `save_preview`. `set_app_links` lets the design programs
(core/bridges) write the file instead; see `core.project.run_app_link` for the contract.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping, Optional

import numpy as np
from PySide6.QtCore import QPointF, QRectF, QSize, Qt, QThreadPool, QUrl, Signal
from PySide6.QtGui import QColor, QDesktopServices, QFontMetrics, QGuiApplication, QImage, QKeySequence, QPainter, QPainterPath, QPen, QShortcut
from PySide6.QtWidgets import (
    QButtonGroup,
    QCheckBox,
    QComboBox,
    QFileDialog,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QMenu,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QSpinBox,
    QSplitter,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from ..core import designer, documents, file_export
from ..core import project as rules
from ..core.designer import Library, Project
from ..core.exporters import save_palette
from ..core.index import ImageError
from ..core.loader import load_image
from ..core.sample import SAMPLE_NAME, sample_image
from . import theme
from .image_view import qimage_to_rgba
from .studio import RoleChooser, strip_icon
from .tasks import Task
from .widgets import Chip, ColorBlock, FlowArea, field_label, heading, refresh_style, small_label

KEEP = "Keep"
SHADING = "Shading"  # the role menu's word for designer.BLEND
UPLOAD_LIMIT = 1100  # longest side of the picture worked on, in pixels (exports use the full size)


def _qimage(rgba: np.ndarray) -> QImage:
    pixels = np.ascontiguousarray(rgba)
    height, width = pixels.shape[:2]
    return QImage(pixels.data, width, height, 4 * width, QImage.Format.Format_RGBA8888).copy()


def _shrink(rgba: np.ndarray, limit: int = UPLOAD_LIMIT) -> np.ndarray:
    """The picture with its longest side at most `limit` pixels (smooth scaling)."""
    height, width = rgba.shape[:2]
    longest = max(width, height)
    if longest <= limit:
        return np.ascontiguousarray(rgba)
    image = QImage(np.ascontiguousarray(rgba).data, width, height, 4 * width, QImage.Format.Format_RGBA8888)
    image = image.scaled(
        max(1, round(width * limit / longest)),
        max(1, round(height * limit / longest)),
        Qt.AspectRatioMode.IgnoreAspectRatio,
        Qt.TransformationMode.SmoothTransformation,
    ).convertToFormat(QImage.Format.Format_RGBA8888)
    buffer = np.frombuffer(image.constBits(), dtype=np.uint8, count=image.sizeInBytes())
    return buffer.reshape(image.height(), image.bytesPerLine())[:, : image.width() * 4].reshape(image.height(), image.width(), 4).copy()


@dataclass
class _Opened:
    """A file read in the background, ready to be shown."""

    path: str
    document: Optional[documents.Document]
    rgba: np.ndarray
    name: str
    known: Optional[list]
    facts: rules.FileFacts
    note: str = ""


# ======================================================================================
# The artwork
# ======================================================================================


class ProjectCanvas(QWidget):
    """The open file, as large as fits. Dragging a box over it leaves that area alone."""

    boxDrawn = Signal(float, float, float, float)  # left, top, right, bottom as fractions

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self._image: Optional[QImage] = None
        self._left_out: list = []
        self._drag_from: Optional[QPointF] = None
        self._drag_to: Optional[QPointF] = None
        self.show_left_out = True
        self.setMinimumSize(240, 160)  # small enough for a 1280 × 720 screen
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)

    def set_picture(self, image: Optional[QImage]) -> None:
        self._image = image
        self.setCursor(Qt.CursorShape.CrossCursor if image is not None else Qt.CursorShape.ArrowCursor)
        self.update()

    def set_left_out(self, boxes) -> None:
        self._left_out = list(boxes)
        self.update()

    def picture_rect(self) -> QRectF:
        """Where the picture is drawn: as large as fits, centered, keeping its proportions."""
        if self._image is None or self._image.isNull():
            return QRectF()
        ratio = self._image.width() / max(1, self._image.height())
        room = QRectF(self.rect()).adjusted(10, 10, -10, -10)
        height = min(room.height(), room.width() / ratio)
        width = height * ratio
        return QRectF(room.x() + (room.width() - width) / 2, room.y() + (room.height() - height) / 2, width, height)

    def artwork_image(self) -> QImage:
        return self.grab(self.picture_rect().toAlignedRect()).toImage()

    def _inside(self, position: QPointF) -> QPointF:
        rect = self.picture_rect()
        return QPointF(min(rect.right(), max(rect.left(), position.x())), min(rect.bottom(), max(rect.top(), position.y())))

    def mousePressEvent(self, event) -> None:
        if event.button() == Qt.MouseButton.LeftButton and self.picture_rect().contains(event.position()):
            self._drag_from = self._drag_to = self._inside(event.position())

    def mouseMoveEvent(self, event) -> None:
        if self._drag_from is not None:
            self._drag_to = self._inside(event.position())
            self.update()

    def mouseReleaseEvent(self, event) -> None:
        if self._drag_from is None:
            return
        start, end = self._drag_from, self._inside(event.position())
        self._drag_from = self._drag_to = None
        rect = self.picture_rect()
        if rect.width() > 0 and abs(end.x() - start.x()) >= 8 and abs(end.y() - start.y()) >= 8:  # a drag, not a click
            left, right = sorted(((start.x() - rect.x()) / rect.width(), (end.x() - rect.x()) / rect.width()))
            top, bottom = sorted(((start.y() - rect.y()) / rect.height(), (end.y() - rect.y()) / rect.height()))
            self.boxDrawn.emit(left, top, right, bottom)
        self.update()

    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        stage = QPainterPath()
        stage.addRoundedRect(QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5), 10, 10)
        painter.fillPath(stage, QColor(theme.STAGE))
        rect = self.picture_rect()
        if rect.isEmpty():
            return
        painter.fillRect(rect, theme.checker_brush())
        painter.drawImage(rect, self._image)
        painter.setPen(QPen(QColor(128, 120, 140, 90), 1))
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawRect(rect.adjusted(-0.5, -0.5, 0.5, 0.5))
        painter.save()
        painter.setClipRect(rect)
        if self.show_left_out:
            for left, top, right, bottom in self._left_out:
                box = QRectF(rect.x() + left * rect.width(), rect.y() + top * rect.height(), (right - left) * rect.width(), (bottom - top) * rect.height())
                painter.setBrush(Qt.BrushStyle.NoBrush)
                painter.setPen(QPen(QColor(0, 0, 0, 150), 3))
                painter.drawRect(box)
                painter.setPen(QPen(QColor("white"), 1.5, Qt.PenStyle.DashLine))
                painter.drawRect(box)
                whole = (right - left) * (bottom - top) >= rules.WHOLE_PICTURE
                if not whole and box.width() > 110 and box.height() > 30:  # no chip on a box over everything
                    tag = QRectF(box.x() + 6, box.y() + 6, 98, 20)
                    painter.setPen(Qt.PenStyle.NoPen)
                    painter.setBrush(QColor(20, 16, 28, 205))
                    painter.drawRoundedRect(tag, 5, 5)
                    painter.setFont(theme.small_font(8))
                    painter.setPen(QColor("white"))
                    painter.drawText(tag, Qt.AlignmentFlag.AlignCenter, "Not recolored")
        if self._drag_from is not None and self._drag_to is not None:
            painter.setPen(QPen(QColor(theme.ACCENT), 2, Qt.PenStyle.DashLine))
            painter.setBrush(QColor(90, 60, 176, 40))
            painter.drawRect(QRectF(self._drag_from, self._drag_to).normalized())
        painter.restore()


# ======================================================================================
# The empty state
# ======================================================================================


class DropZone(QFrame):
    """The dashed area of the empty state. The window passes dropped files to the page."""

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self._active = False
        self.setMinimumHeight(150)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 22, 24, 22)
        layout.setSpacing(12)
        layout.addStretch(1)
        self.label = QLabel("Drop a file here: PSD, AI, PDF, PNG, JPG")
        self.label.setProperty("strong", True)
        self.label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(self.label)
        self.open_button = QPushButton("Open file…")
        self.open_button.setProperty("primary", True)
        self.open_button.setToolTip("Open a Photoshop, Illustrator, PDF or picture file from this computer (Ctrl+O)")
        layout.addWidget(self.open_button, 0, Qt.AlignmentFlag.AlignHCenter)
        layout.addStretch(1)

    def set_active(self, active: bool) -> None:
        if active != self._active:
            self._active = active
            self.update()

    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        frame = QRectF(self.rect()).adjusted(1, 1, -1, -1)
        painter.setPen(QPen(QColor(theme.ACCENT if self._active else theme.ACCENT_SOFT), 1.6, Qt.PenStyle.DashLine))
        painter.setBrush(QColor(theme.TINT if self._active else theme.PANEL_TINT))
        painter.drawRoundedRect(frame, 14, 14)


def _steps_row() -> QWidget:
    """ "1 Open your file › 2 Try palettes on it › 3 Export the same file type"."""
    row = QWidget()
    box = QHBoxLayout(row)
    box.setContentsMargins(0, 0, 0, 0)
    box.setSpacing(8)
    box.addStretch(1)
    for number, text in enumerate(("Open your file", "Try palettes on it", "Export the same file type"), 1):
        if number > 1:
            arrow = QLabel("›")
            arrow.setProperty("small", True)
            box.addWidget(arrow)
        badge = QLabel(str(number))
        badge.setProperty("step", True)
        badge.setAlignment(Qt.AlignmentFlag.AlignCenter)
        badge.setFixedSize(22, 22)
        box.addWidget(badge)
        box.addWidget(QLabel(text))
    box.addStretch(1)
    return row


# ======================================================================================
# The tab
# ======================================================================================


class ProjectPage(QWidget):
    """The whole "Your project" tab. See the module docstring."""

    notify = Signal(str)  # a short confirmation for the window to show
    status = Signal(str, bool)  # a status-line message, and whether it is an error
    busy = Signal(bool)  # a file is being opened or saved in the background
    progress = Signal(float, str)  # how far that work is
    paletteRequested = Signal()  # the person wants to edit palettes on the My palette tab

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        # Palettes to try
        self._project: Optional[Project] = None
        self._library: Optional[Library] = None
        self._ideas: list[rules.PaletteChoice] = []
        self._choices: list[rules.PaletteChoice] = []
        self._choice_key = ""
        self._colors: list[str] = []
        self._names: list[str] = []
        # The open file
        self._layers: Optional[designer.Layers] = None  # the picture, split into its main colors
        self._source: Optional[np.ndarray] = None  # the picture itself (RGBA, at most UPLOAD_LIMIT)
        self._before_image: Optional[QImage] = None
        self._known: Optional[list] = None  # where the file says its pictures are (None: it cannot tell)
        self._found: list = []  # photo areas found (from the file, or guessed from the pixels)
        self._manual: list = []  # boxes drawn by hand
        self._left_out: list = []  # every area left alone right now
        self._whole = False  # the photo areas found covered the whole picture and were dropped
        self._everything_left_out = False
        self._document: Optional[documents.Document] = None
        self._jobs: list[str] = []  # for each main color: a role name, or "" to keep it
        self._upload_name = ""
        self._upload_path = ""  # "" when the picture did not come from a file
        self._upload_page = 0
        self._facts = rules.FileFacts("")
        self._before = False
        self._show_small = False
        # Background work and links
        self._export_tasks: dict[int, Task] = {}
        self._open_tasks: dict[int, Task] = {}
        self._task_id = 0
        self._latest_open = 0
        self._links: dict[str, Any] = {}
        self._link_ok: dict[str, bool] = {}
        # Asked before a design program is started and controlled: confirm_app(program, file name)
        # returns True to go ahead. None (tests) asks nothing.
        self.confirm_app = None
        self.last_report: Any = None
        self.last_dir = ""
        self._syncing = False
        self._shown_swatches: tuple = ()
        self.mapping_table: Optional[QWidget] = None
        self.map_combos: list[QComboBox] = []
        self._results: list[tuple[ColorBlock, QLabel]] = []
        self._small_rows: list[list[QWidget]] = []
        self._build()
        self._show()

    # ----------------------------------------------------------------------------------
    # Construction
    # ----------------------------------------------------------------------------------

    def _build(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        self.stack = QStackedWidget()
        root.addWidget(self.stack)
        self._build_empty()
        self._build_work()
        QShortcut(QKeySequence("Ctrl+E"), self, self.export_primary, context=Qt.ShortcutContext.WindowShortcut)
        QShortcut(QKeySequence(Qt.Key.Key_Backslash), self, self.toggle_before_after, context=Qt.ShortcutContext.WindowShortcut)

    def _build_empty(self) -> None:
        self.empty_page = QWidget()
        outer = QVBoxLayout(self.empty_page)
        outer.setContentsMargins(16, 8, 16, 8)
        outer.addStretch(1)
        column = QWidget()
        column.setMaximumWidth(760)
        column.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        box = QVBoxLayout(column)
        box.setContentsMargins(0, 0, 0, 0)
        box.setSpacing(16)
        title = heading("Recolor your own design and get the same file back", 21)
        title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        title.setWordWrap(True)
        box.addWidget(title)
        self.drop_zone = DropZone()
        self.open_file_button = self.drop_zone.open_button
        box.addWidget(self.drop_zone)
        box.addWidget(_steps_row())
        promise = small_label("Photoshop files come back as Photoshop files, with every layer.")
        promise.setAlignment(Qt.AlignmentFlag.AlignCenter)
        box.addWidget(promise)
        self.empty_message = QLabel()
        self.empty_message.setWordWrap(True)
        self.empty_message.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.empty_message.setProperty("error", True)
        self.empty_message.hide()
        box.addWidget(self.empty_message)
        self.sample_button = QPushButton("No file handy? Try the sample")
        self.sample_button.setProperty("link", True)
        box.addWidget(self.sample_button, 0, Qt.AlignmentFlag.AlignHCenter)
        middle = QHBoxLayout()
        middle.addStretch(1)
        middle.addWidget(column, 10)
        middle.addStretch(1)
        outer.addLayout(middle)
        outer.addStretch(2)
        self.stack.addWidget(self.empty_page)
        self.open_file_button.clicked.connect(self.open_dialog)
        self.sample_button.clicked.connect(self.open_sample)

    def _build_work(self) -> None:
        self.work_page = QWidget()
        layout = QVBoxLayout(self.work_page)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(8)

        # --- header: the file ----------------------------------------------------------
        header = QHBoxLayout()
        header.setSpacing(12)
        self.title_label = QLabel()
        self.title_label.setFont(theme.heading_font(15))
        self.facts_label = QLabel()
        self.facts_label.setProperty("small", True)
        header.addWidget(self.title_label, 0)
        header.addWidget(self.facts_label, 0)
        header.addStretch(1)
        self.page_label = field_label("Page")
        self.page_spin = QSpinBox()
        self.page_spin.setRange(1, 1)
        self.page_spin.setKeyboardTracking(False)
        self.page_spin.setToolTip("Which page or artboard of the file to work on")
        self.open_another_button = QPushButton("Open another file")
        self.open_another_button.setProperty("link", True)
        self.open_another_button.setToolTip("Open a different design file (Ctrl+O)")
        for widget in (self.page_label, self.page_spin, self.open_another_button):
            header.addWidget(widget)
        layout.addLayout(header)

        # --- left: the artwork ---------------------------------------------------------
        self.splitter = QSplitter(Qt.Orientation.Horizontal)
        self.splitter.setHandleWidth(12)
        self.splitter.setChildrenCollapsible(False)
        art = QFrame()
        art.setObjectName("panel")
        art_box = QVBoxLayout(art)
        art_box.setContentsMargins(12, 10, 12, 12)
        art_box.setSpacing(8)
        tools = QHBoxLayout()
        tools.setSpacing(0)
        self.before_button = QPushButton("Before")
        self.after_button = QPushButton("After")
        self.before_button.setProperty("segment", "left")
        self.after_button.setProperty("segment", "right")
        self.view_group = QButtonGroup(self)
        self.view_group.setExclusive(True)
        for button in (self.before_button, self.after_button):
            button.setCheckable(True)
            button.setToolTip("Compare with the original (\\ key). Your choices stay as they are.")
            self.view_group.addButton(button)
            tools.addWidget(button)
        self.after_button.setChecked(True)
        tools.addStretch(1)
        self.more_button = QPushButton("More  ▾")
        self.more_menu = QMenu(self.more_button)
        self.leave_area_action = self.more_menu.addAction("Leave an area alone")
        self.leave_area_action.setToolTip("Drag a box over the picture: what is inside keeps its colors")
        self.forget_boxes_action = self.more_menu.addAction("Forget my boxes")
        self.keep_all_action = self.more_menu.addAction("Keep every original color")
        self.suggest_action = self.more_menu.addAction("Suggest roles again")
        self.more_menu.addSeparator()
        self.copy_preview_action = self.more_menu.addAction("Copy preview")
        self.save_preview_action = self.more_menu.addAction("Save preview as PNG…")
        self.more_button.setMenu(self.more_menu)
        tools.addWidget(self.more_button)
        art_box.addLayout(tools)
        self.canvas = ProjectCanvas()
        art_box.addWidget(self.canvas, 1)
        self.splitter.addWidget(art)

        # --- right: palette and colors (scrolls) ----------------------------------------
        side = QFrame()
        side.setObjectName("panel")
        side_box = QVBoxLayout(side)
        side_box.setContentsMargins(4, 6, 4, 8)
        side_box.setSpacing(8)
        self.side_scroll = QScrollArea()
        self.side_scroll.setWidgetResizable(True)
        self.side_scroll.setFrameShape(QFrame.Shape.NoFrame)
        content = QWidget()
        content.setAutoFillBackground(False)
        column = QVBoxLayout(content)
        column.setContentsMargins(10, 4, 10, 4)
        column.setSpacing(8)
        picker = QHBoxLayout()
        picker.setSpacing(8)
        picker.addWidget(field_label("Palette"))
        self.palette_combo = QComboBox()
        self.palette_combo.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.palette_combo.setMinimumContentsLength(14)
        self.palette_combo.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon)
        self.palette_combo.setIconSize(QSize(64, 14))
        self.palette_combo.setToolTip("Your palettes, your Library, and ideas made from this file")
        picker.addWidget(self.palette_combo, 1)
        column.addLayout(picker)
        self.swatches = FlowArea(5)
        column.addWidget(self.swatches)
        links = FlowArea(4)
        self.role_chooser = RoleChooser(inline_buttons=False)
        self.swap_button = self.role_chooser.swap_button
        links.add(self.swap_button)
        self.roles_toggle = QPushButton("Change which color plays each role…")
        self.roles_toggle.setProperty("link", True)
        self.roles_toggle.setCheckable(True)
        links.add(self.roles_toggle)
        column.addWidget(links)
        self.role_chooser.setVisible(False)
        column.addWidget(self.role_chooser)
        line = QFrame()
        line.setFrameShape(QFrame.Shape.HLine)
        line.setStyleSheet("color: rgba(128, 120, 140, 70);")
        column.addWidget(line)
        self.mapping_box = QWidget()
        self.mapping_holder = QVBoxLayout(self.mapping_box)
        self.mapping_holder.setContentsMargins(0, 0, 0, 0)
        column.addWidget(self.mapping_box)
        self.small_toggle = QPushButton()
        self.small_toggle.setProperty("link", True)
        column.addWidget(self.small_toggle, 0, Qt.AlignmentFlag.AlignLeft)
        self.photo_note = small_label()
        column.addWidget(self.photo_note)
        self.change_photos = QCheckBox("Change photos too")
        self.change_photos.setToolTip(
            "Off: photos and placed pictures keep their colors, and only flat color, shapes and text change.\n"
            "On: everything in the picture is recolored."
        )
        column.addWidget(self.change_photos)
        column.addStretch(1)
        self.side_scroll.setWidget(content)
        side_box.addWidget(self.side_scroll, 1)
        self._build_report_card()
        side_box.addWidget(self.report_card)
        self.splitter.addWidget(side)
        art.setMinimumWidth(300)
        side.setMinimumWidth(380)
        self.splitter.setStretchFactor(0, 1)
        self.splitter.setStretchFactor(1, 0)
        self.splitter.setSizes([860, 430])
        layout.addWidget(self.splitter, 1)

        # --- footer: always visible -----------------------------------------------------
        footer = QFrame()
        footer.setObjectName("panel")
        footer_box = QHBoxLayout(footer)
        footer_box.setContentsMargins(16, 10, 12, 10)
        footer_box.setSpacing(12)
        lines = QVBoxLayout()
        lines.setSpacing(2)
        self.contrast_label = QLabel()
        self.contrast_label.setProperty("strong", True)
        self.accuracy_label = small_label(wrap=False)
        lines.addWidget(self.contrast_label)
        lines.addWidget(self.accuracy_label)
        footer_box.addLayout(lines, 1)
        split = QHBoxLayout()
        split.setSpacing(0)
        self.export_button = QPushButton("Export…")
        self.export_button.setProperty("primary", True)
        self.export_button.setProperty("split", "main")
        self.export_arrow = QPushButton("▾")
        self.export_arrow.setProperty("split", "arrow")
        self.export_arrow.setToolTip("Other formats, and the palette as swatches")
        self.export_menu = QMenu(self.export_arrow)
        self.export_arrow.setMenu(self.export_menu)
        split.addWidget(self.export_button)
        split.addWidget(self.export_arrow)
        footer_box.addLayout(split)
        layout.addWidget(footer)
        self.footer = footer
        self.stack.addWidget(self.work_page)

        # --- wiring ---------------------------------------------------------------------
        self.open_another_button.clicked.connect(self.open_dialog)
        self.page_spin.valueChanged.connect(lambda value: self.show_page(value - 1))
        self.before_button.clicked.connect(lambda: self.set_before(True))
        self.after_button.clicked.connect(lambda: self.set_before(False))
        self.leave_area_action.triggered.connect(self._explain_boxes)
        self.forget_boxes_action.triggered.connect(self.forget_boxes)
        self.keep_all_action.triggered.connect(self.keep_all)
        self.suggest_action.triggered.connect(self.suggest_jobs)
        self.copy_preview_action.triggered.connect(self.copy_preview)
        self.save_preview_action.triggered.connect(self.save_preview_dialog)
        self.canvas.boxDrawn.connect(lambda left, top, right, bottom: self.leave_out((left, top, right, bottom)))
        self.palette_combo.activated.connect(self._palette_picked)
        self.role_chooser.changed.connect(self._roles_changed)
        self.roles_toggle.toggled.connect(self._toggle_roles)
        self.small_toggle.clicked.connect(self._toggle_small)
        self.change_photos.toggled.connect(lambda _on: self._analyze())
        self.export_button.clicked.connect(self.export_primary)

    def _build_report_card(self) -> None:
        self.report_card = QFrame()
        self.report_card.setObjectName("card")
        box = QVBoxLayout(self.report_card)
        box.setContentsMargins(14, 10, 14, 10)
        box.setSpacing(6)
        self.report_title = QLabel()
        self.report_title.setProperty("strong", True)
        self.report_title.setWordWrap(True)
        box.addWidget(self.report_title)
        self.report_summary = small_label()
        box.addWidget(self.report_summary)
        self.report_details_button = QPushButton("Show details")
        self.report_details_button.setProperty("link", True)
        self.report_details_button.setCheckable(True)
        box.addWidget(self.report_details_button, 0, Qt.AlignmentFlag.AlignLeft)
        self.report_details = QLabel()
        self.report_details.setProperty("small", True)
        self.report_details.setWordWrap(True)
        self.report_details.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self.report_scroll = QScrollArea()
        self.report_scroll.setWidgetResizable(True)
        self.report_scroll.setFrameShape(QFrame.Shape.NoFrame)
        self.report_scroll.setWidget(self.report_details)
        self.report_scroll.setMaximumHeight(150)
        self.report_scroll.hide()
        box.addWidget(self.report_scroll)
        buttons = FlowArea(6)
        self.show_folder_button = QPushButton("Show in folder")
        self.copy_report_button = QPushButton("Copy report")
        self.report_done_button = QPushButton("Done")
        self.report_done_button.setProperty("link", True)
        for button in (self.show_folder_button, self.copy_report_button, self.report_done_button):
            buttons.add(button)
        box.addWidget(buttons)
        self.report_card.hide()
        self.report_details_button.toggled.connect(self._toggle_report_details)
        self.show_folder_button.clicked.connect(self.show_in_folder)
        self.copy_report_button.clicked.connect(self.copy_report)
        self.report_done_button.clicked.connect(self.hide_report)

    # ----------------------------------------------------------------------------------
    # Palettes to try
    # ----------------------------------------------------------------------------------

    def set_sources(self, project: Optional[Project], library: Optional[Library]) -> None:
        """The palettes of the My palette tab and the Library. Call again after they change."""
        self._project, self._library = project, library
        before = (tuple(self._colors), tuple(self._names))
        self._refresh_choices()
        if (tuple(self._colors), tuple(self._names)) != before:
            self._show()

    def _refresh_choices(self, new_picture: bool = False) -> None:
        """Rebuild the picker; keep the chosen palette, or start a new picture on the starter."""
        self._choices = rules.palette_choices(self._project, self._library, self._ideas)
        keys = {choice.key for choice in self._choices}
        current = self._choice()
        starter = (new_picture and (current is None or current.from_file)) or (self._choice_key not in keys and self._layers is not None)
        if starter:
            self._choice_key = rules.choose_starter(self._project, self._library, self._ideas)
        choice = self._choice()
        colors, names = (list(choice.colors), list(choice.names)) if choice is not None else ([], [])
        if (colors, names) != (self._colors, self._names):
            self._colors, self._names = colors, names
            self.role_chooser.set_palette(colors, names)
        if starter and choice is not None and choice.from_file:
            self.role_chooser.set_overrides(dict(choice.roles))  # each file color gets its own partner
        self._fill_picker()

    def _choice(self) -> Optional[rules.PaletteChoice]:
        return next((choice for choice in self._choices if choice.key == self._choice_key), None)

    def _fill_picker(self) -> None:
        self._syncing = True
        combo = self.palette_combo
        combo.clear()
        group = None
        for choice in self._choices:
            if choice.group != group:
                group = choice.group
                combo.addItem(group.upper(), None)
                combo.model().item(combo.count() - 1).setEnabled(False)
            combo.addItem(strip_icon(choice.colors), choice.label, choice.key)
        combo.setCurrentIndex(max(0, combo.findData(self._choice_key)))
        combo.setEnabled(bool(self._choices))
        self._syncing = False

    def _palette_picked(self, index: int) -> None:
        key = self.palette_combo.itemData(index)
        if not self._syncing and key:
            self.choose_palette(key)

    def palette_choices(self) -> list[rules.PaletteChoice]:
        return list(self._choices)

    def palette_key(self) -> str:
        return self._choice_key

    def palette_label(self) -> str:
        choice = self._choice()
        return choice.label if choice is not None else ""

    def palette_colors(self) -> list[str]:
        return list(self._colors)

    def choose_palette(self, key: str) -> bool:
        """Try another palette on the file. The roles chosen for its colors stay."""
        if not any(choice.key == key for choice in self._choices):
            return False
        self._choice_key = key
        self._refresh_choices()
        choice = self._choice()
        if choice is not None and choice.from_file:
            self.role_chooser.set_overrides(dict(choice.roles))
        self._show()
        return True

    # ----------------------------------------------------------------------------------
    # Roles: which palette color plays Background, Text, Primary...
    # ----------------------------------------------------------------------------------

    def roles(self) -> Optional[designer.Roles]:
        return self.role_chooser.roles()

    def set_role(self, name: str, color: Optional[str]) -> None:
        self.role_chooser.set_role(name, color)

    def overrides(self) -> dict[str, str]:
        return self.role_chooser.overrides()

    def clear_overrides(self) -> None:
        self.role_chooser.clear_overrides()

    def next_variant(self) -> None:
        self.role_chooser.next_variant()

    def _roles_changed(self) -> None:
        self._show()

    def _toggle_roles(self, open_: bool) -> None:
        self.role_chooser.setVisible(open_)
        self.roles_toggle.setText("Hide the roles" if open_ else "Change which color plays each role…")

    # ----------------------------------------------------------------------------------
    # The open file
    # ----------------------------------------------------------------------------------

    def has_upload(self) -> bool:
        return self._layers is not None

    def file_name(self) -> str:
        return self._upload_name

    def source_format(self) -> str:
        return rules.source_format(self._upload_path)

    def set_upload(self, rgba: Optional[np.ndarray], name: str = "", known: Optional[list] = None) -> None:
        """Work on a picture that is not a file (RGBA array); None empties the tab.

        `known` is where the picture's photos are (boxes as fractions), when that is known.
        """
        self.close_document()
        self._upload_path, self._upload_page = "", 0
        facts = rules.read_facts("", None, 0, rgba, name) if rgba is not None else rules.FileFacts("")
        self._set_picture(rgba, name, known, facts)

    def _set_picture(self, rgba: Optional[np.ndarray], name: str, known: Optional[list], facts: rules.FileFacts) -> None:
        self._manual = []
        self._jobs = []
        self._before = False
        self._facts = facts
        self.hide_report()
        if rgba is None:
            self._source, self._known, self._upload_name, self._found = None, None, "", []
            self._before_image = None
            self._ideas = []
            self._analyze()
            self._refresh_choices()
            return
        self._source = _shrink(rgba)
        self._known, self._upload_name = known, name
        self._found = designer.photo_boxes(self._source, known)
        self._before_image = _qimage(self._source)
        self.empty_message.hide()
        self._analyze(new_picture=True)

    def _analyze(self, new_picture: bool = False) -> None:
        """Find the main colors of the picture, leaving photo areas alone unless asked not to."""
        previous = dict(zip(self._layers.colors, self._jobs)) if self._layers is not None and not new_picture else {}
        if self._source is None:
            self._layers, self._jobs, self._left_out, self._whole, self._everything_left_out = None, [], [], False, False
        else:
            shape = self._source.shape[:2]
            self._left_out, self._whole = rules.leave_out(shape, self._found, self._manual, self.change_photos.isChecked(), self._known)
            mask = designer.boxes_mask(shape, self._left_out) if self._left_out else None
            self._everything_left_out = mask is not None and bool(mask.all())
            self._layers = designer.find_layers(self._source, exclude=mask)
            suggested = designer.suggest_targets(self._layers, designer.assign_roles(["#FFFFFF", "#000000"]))
            if new_picture:
                self._jobs = suggested
                self._ideas = rules.ideas_from_file(self._layers.colors, suggested)
                self.role_chooser.set_variant(rules.starting_variant(self._layers.colors))
                self._refresh_choices(new_picture=True)
            else:
                jobs = []
                for color, guess in zip(self._layers.colors, suggested):
                    # Keep a choice already made for (nearly) the same color.
                    near = min(previous, key=lambda old: designer.oklab_distance(old, color), default=None)
                    jobs.append(previous[near] if near is not None and designer.oklab_distance(near, color) < 0.03 else guess)
                self._jobs = jobs
        self.canvas.set_left_out(self._left_out)
        self._build_mapping()
        self._show()

    def close_document(self) -> None:
        if self._document is not None:
            self._document.close()
        self._document = None

    def show_page(self, page: int) -> None:
        """Work on another page or artboard of the open document."""
        document = self._document
        if document is None or not 0 <= page < document.page_count or self._syncing:
            return
        try:
            rgba = document.render(page, UPLOAD_LIMIT)
        except ImageError as error:
            self.status.emit(str(error), True)
            return
        label = document.name if document.page_count == 1 else f"{document.name}, {document.page_names[page]}"
        known = document.image_boxes(page) if document.kind != "Image" else None
        self._upload_page = page
        self._set_picture(rgba, label, known, rules.read_facts(self._upload_path, document, page, rgba, document.name))
        if document.note:
            self.status.emit(document.note, False)

    def _idml_readable(self) -> bool:
        link = self._links.get("idml")
        return link is not None and callable(getattr(link, "preview", None)) and self._link_ok.get("idml", False)

    def _read(self, path: str, progress=None, cancelled=None) -> _Opened:
        """Read a file (on any thread). Raises ImageError with a message for the person."""
        suffix = Path(path).suffix.lower()
        name = Path(path).name
        if suffix in (".indd", ".idml"):
            key = suffix[1:]
            link = self._links.get(key)
            preview = getattr(link, "preview", None)
            if link is not None and callable(preview) and self._link_ok.get(key, False):
                rgba = np.ascontiguousarray(preview(path, progress, cancelled), dtype=np.uint8)
                return _Opened(path, None, rgba, name, None, rules.FileFacts(name, int(rgba.shape[1]), int(rgba.shape[0])))
            raise ImageError(rules.indd_message(self._idml_readable()) if key == "indd" else rules.IDML_NOT_YET)
        if documents.is_document(path):
            document = documents.open_document(path)
            try:
                rgba = document.render(0, UPLOAD_LIMIT)
                known = document.image_boxes(0) if document.kind != "Image" else None
                facts = rules.read_facts(path, document, 0, rgba, name)
            except Exception:
                document.close()
                raise
            label = document.name if document.page_count == 1 else f"{document.name}, {document.page_names[0]}"
            return _Opened(path, document, rgba, label, known, facts, document.note)
        loaded = load_image(path)
        return _Opened(path, None, loaded.rgba, loaded.name, None, rules.read_facts(path, None, 0, loaded.rgba, loaded.name))

    def _apply_opened(self, opened: _Opened) -> None:
        self.close_document()
        self._document = opened.document
        self._upload_path, self._upload_page = opened.path, 0
        self.last_dir = str(Path(opened.path).parent)
        self._syncing = True
        self.page_spin.setRange(1, opened.document.page_count if opened.document is not None else 1)
        self.page_spin.setValue(1)
        self._syncing = False
        self._set_picture(opened.rgba, opened.name, opened.known, opened.facts)
        message = f"{Path(opened.path).name} is ready. Try palettes on it, then export."
        self.status.emit(f"{message} {opened.note}".strip(), False)

    def _open_failed_message(self, text: str) -> None:
        self.status.emit(text, True)
        if self._layers is None:
            self.empty_message.setText(text)
            self.empty_message.show()

    def load_upload(self, path) -> bool:
        """Open a design file or picture now (the plain method behind Open file…)."""
        try:
            opened = self._read(str(path))
        except ImageError as error:
            self._open_failed_message(str(error))
            return False
        self._apply_opened(opened)
        return True

    def open_file(self, path) -> None:
        """Open a file in the background; the window stays responsive. A newer open wins."""
        path = str(path)
        name = Path(path).name

        def work(progress, cancelled):
            progress(0.05, f"Opening {name}…")
            return self._read(path, progress, cancelled)

        task = self._start(work, self._opened, self._open_failed, self._open_tasks)
        self._latest_open = task.task_id
        self.status.emit(f"Opening {name}…", False)

    def _opened(self, task_id: int, opened) -> None:
        self._finish(task_id, self._open_tasks)
        if task_id != self._latest_open:
            if opened.document is not None:
                opened.document.close()  # an older open, replaced by a newer one
            return
        self._apply_opened(opened)

    def _open_failed(self, task_id: int, error) -> None:
        self._finish(task_id, self._open_tasks)
        if task_id != self._latest_open:
            return
        if error is None:
            self.status.emit("Cancelled.", False)
        elif isinstance(error, ImageError):
            self._open_failed_message(str(error))
        elif isinstance(error, MemoryError):
            self._open_failed_message("There was not enough memory to open this file.")
        else:
            self._open_failed_message(f"This file could not be opened: {error}")

    def open_patterns(self) -> str:
        extensions = list(rules.OPEN_EXTENSIONS) + [".indd"]
        if self._idml_readable():
            extensions.append(".idml")
        return " ".join(f"*{extension}" for extension in extensions)

    def open_dialog(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self,
            "Open your design file",
            self.last_dir,
            f"Design files and pictures ({self.open_patterns()});;All files (*)",
        )
        if path:
            self.open_file(path)

    def open_sample(self) -> None:
        """The app's sample picture, for trying the tab without a file of your own."""
        self.set_upload(sample_image(), SAMPLE_NAME)
        self.status.emit("This is the sample. Open your own file whenever you are ready.", False)

    def paste(self) -> None:
        """A picture or a file from the clipboard."""
        clipboard = QGuiApplication.clipboard()
        mime = clipboard.mimeData()
        if mime is not None and mime.hasUrls():
            files = [url.toLocalFile() for url in mime.urls() if url.isLocalFile()]
            if files:
                self.open_file(files[0])
                return
        if mime is not None and mime.hasImage():
            image = clipboard.image()
            if not image.isNull():
                self.set_upload(qimage_to_rgba(image), "Pasted image")
                return
        self.notify.emit("The clipboard has no picture or file.")

    def set_drop_active(self, active: bool) -> None:
        self.drop_zone.set_active(active)

    # ----------------------------------------------------------------------------------
    # Colors in your file
    # ----------------------------------------------------------------------------------

    def jobs(self) -> list[str]:
        return list(self._jobs)

    def set_job(self, position: int, job: str) -> None:
        """Choose what replaces one main color of the file: a role name, or "" to keep it."""
        if 0 <= position < len(self._jobs) and (job == "" or job in designer.ROLE_NAMES or job == designer.BLEND):
            self._jobs[position] = job
            self._sync_combos()
            self._show()

    def suggest_jobs(self) -> None:
        if self._layers is not None:
            self._jobs = designer.suggest_targets(self._layers, self.roles() or designer.assign_roles(["#FFFFFF", "#000000"]))
            self._sync_combos()
            self._show()

    def keep_all(self) -> None:
        """Keep every original color (the roles of the palette stay as they are)."""
        if self._layers is not None:
            self._jobs = [""] * len(self._jobs)
            self._sync_combos()
            self._show()

    def _sync_combos(self) -> None:
        self._syncing = True
        for combo, job in zip(self.map_combos, self._jobs):
            combo.setCurrentIndex(max(0, combo.findData(job)))
        self._syncing = False

    def _build_mapping(self) -> None:
        if self.mapping_table is not None:
            self.mapping_table.hide()
            self.mapping_table.deleteLater()
        self.mapping_table = None
        self.map_combos, self._results, self._small_rows = [], [], []
        if self._layers is None:
            self.small_toggle.hide()
            return
        table = QWidget(self.mapping_box)
        grid = QGridLayout(table)
        grid.setContentsMargins(0, 0, 0, 0)
        grid.setHorizontalSpacing(6)
        grid.setVerticalSpacing(5)
        grid.addWidget(field_label("Colors in your file"), 0, 0, 1, 4)
        becomes = field_label("becomes")
        grid.addWidget(becomes, 0, 4, 1, 3)
        mono = theme.mono_font(9)
        hex_width = QFontMetrics(mono).horizontalAdvance("#DDDDDD") + 4
        small = set(rules.small_details(self._layers.shares))
        self._syncing = True
        for position, color in enumerate(self._layers.colors):
            row = position + 1
            share = self._layers.shares[position]
            block = ColorBlock(22)
            block.set_color(color)
            covers = f"{color} covers {share * 100:.0f}% of the picture" if share >= 0.005 else f"{color} (a small detail)"
            block.setToolTip(covers)
            code = QLabel(color)
            code.setFont(mono)
            code.setMinimumWidth(hex_width)  # a HEX code is never cut short
            amount = QLabel(f"{share * 100:.0f}%" if share >= 0.005 else "<1%")
            amount.setProperty("small", True)
            amount.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
            amount.setMinimumWidth(QFontMetrics(theme.small_font(9)).horizontalAdvance("100%") + 4)
            combo = QComboBox()
            combo.addItem(KEEP, "")
            blend = next((b for b in self._layers.blends if b.index == position), None)
            if blend is not None:  # an in-between shade: it can follow the two colors at its ends
                combo.addItem(SHADING, designer.BLEND)
            for name in designer.ROLE_NAMES:
                combo.addItem(name, name)
            combo.setCurrentIndex(max(0, combo.findData(self._jobs[position])))
            combo.setToolTip(
                f"Which palette role replaces {color}. Keep leaves it as it is."
                + (
                    f" {color} is shading between {self._layers.colors[blend.low]} and {self._layers.colors[blend.high]}:"
                    " Shading keeps it between whatever those two become, so soft shading stays smooth."
                    if blend is not None
                    else ""
                )
            )
            combo.setMinimumWidth(112)
            combo.currentIndexChanged.connect(lambda _=0, p=position, c=combo: self._job_chosen(p, c))
            arrow = QLabel("→")
            arrow.setProperty("small", True)
            result = ColorBlock(22)
            result_code = QLabel()
            result_code.setFont(mono)
            result_code.setMinimumWidth(hex_width)
            widgets = [block, code, amount, combo, arrow, result, result_code]
            for column, widget in enumerate(widgets):
                grid.addWidget(widget, row, column)
            if position in small:
                self._small_rows.append(widgets)
            self.map_combos.append(combo)
            self._results.append((result, result_code))
        grid.setColumnStretch(3, 1)
        self._syncing = False
        self.mapping_holder.addWidget(table)
        self.mapping_table = table
        self._apply_small_rows()

    def _apply_small_rows(self) -> None:
        for widgets in self._small_rows:
            for widget in widgets:
                widget.setVisible(self._show_small)
        count = len(self._small_rows)
        self.small_toggle.setVisible(bool(count))
        detail = "small detail" if count == 1 else "small details"
        self.small_toggle.setText(f"Hide the {detail}" if self._show_small else f"+ {count} {detail}")

    def _toggle_small(self) -> None:
        self._show_small = not self._show_small
        self._apply_small_rows()

    def _job_chosen(self, position: int, combo: QComboBox) -> None:
        if not self._syncing and 0 <= position < len(self._jobs):
            self._jobs[position] = combo.currentData() or ""
            self._show()

    def targets(self) -> list[Optional[str]]:
        """What each main color becomes (None keeps it)."""
        if self._layers is None:
            return []
        if self._everything_left_out:
            return [None] * len(self._layers.colors)
        roles = self.roles()
        table = roles.as_dict() if roles is not None else {}
        return designer.job_targets(self._layers.colors, self._jobs, table, self._layers.blends)

    def recolored(self) -> Optional[np.ndarray]:
        """The picture with the chosen roles applied (RGBA), or None when no file is open."""
        if self._layers is None:
            return None
        return designer.recolor(self._layers, self.targets())

    # ----------------------------------------------------------------------------------
    # Photo areas
    # ----------------------------------------------------------------------------------

    def left_out(self) -> list:
        """The areas that keep their colors (boxes as fractions of the picture)."""
        return list(self._left_out)

    def leave_out(self, box) -> None:
        """Leave an area alone: what dragging a box over the picture does."""
        if self._source is not None:
            self._manual.append(tuple(float(value) for value in box))
            self._analyze()

    def forget_boxes(self) -> None:
        if self._manual:
            self._manual = []
            self._analyze()

    def _explain_boxes(self) -> None:
        self.set_before(False)
        self.notify.emit("Drag a box over the picture. What is inside keeps its colors.")

    # ----------------------------------------------------------------------------------
    # Before and after
    # ----------------------------------------------------------------------------------

    def showing_before(self) -> bool:
        return self._before

    def set_before(self, before: bool) -> None:
        """Show the original (True) or the result (False). No choice is changed."""
        self._before = bool(before) and self._layers is not None
        self._show()

    def toggle_before_after(self) -> None:
        if self._layers is not None:
            self.set_before(not self._before)

    # ----------------------------------------------------------------------------------
    # Showing
    # ----------------------------------------------------------------------------------

    def _show(self) -> None:
        loaded = self._layers is not None
        self.stack.setCurrentWidget(self.work_page if loaded else self.empty_page)
        roles = self.roles()
        busy = bool(self._export_tasks)
        for action in (self.copy_preview_action, self.save_preview_action):
            action.setEnabled(loaded)
        self.forget_boxes_action.setEnabled(bool(self._manual))
        self.keep_all_action.setEnabled(loaded)
        self.suggest_action.setEnabled(loaded)
        self.leave_area_action.setEnabled(loaded)
        if not loaded:
            self.canvas.set_picture(None)
            self.export_button.setEnabled(False)
            self.export_arrow.setEnabled(False)
            return
        metrics = QFontMetrics(self.title_label.font())
        self.title_label.setText(metrics.elidedText(self._upload_name, Qt.TextElideMode.ElideMiddle, 460))
        self.title_label.setToolTip(self._upload_path or self._upload_name)
        self.facts_label.setText(self._facts.line())
        several = self._document is not None and self._document.page_count > 1
        self.page_label.setVisible(several)
        self.page_spin.setVisible(several)
        self.before_button.setChecked(self._before)
        self.after_button.setChecked(not self._before)
        self.canvas.set_picture(self._before_image if self._before else _qimage(self.recolored()))
        self._show_swatches()
        self._show_results(roles)
        share = float(designer.boxes_mask(self._source.shape[:2], self._left_out).mean()) if self._left_out else 0.0
        exact = self._known is not None
        self.photo_note.setText(
            rules.photo_line(share, len(self._left_out), exact, bool(self._manual), self.change_photos.isChecked(), self._whole)
        )
        self.change_photos.setEnabled(True)
        self.contrast_label.setText(rules.contrast_line(roles.text, roles.background) if roles is not None else "Choose a palette to see the contrast.")
        source, app = self.source_format(), self.app_found()
        self.accuracy_label.setText(rules.accuracy_line(source, app))
        self.export_button.setText(rules.export_label(source, app))
        self.export_button.setToolTip(self._export_tip(source, app))
        self.export_button.setEnabled(not busy and (app or source != "indd"))
        self.export_arrow.setEnabled(not busy)
        self._rebuild_export_menu(source, app)

    def _show_swatches(self) -> None:
        shown = (tuple(self._colors), tuple(self._names))
        if shown == self._shown_swatches:
            return
        self._shown_swatches = shown
        self.swatches.clear()
        for position, color in enumerate(self._colors):
            name = self._names[position] if position < len(self._names) else ""
            chip = Chip(color, color)
            chip.setToolTip(f"{name}  {color}\nClick to copy" if name else f"{color}\nClick to copy")
            chip.clicked.connect(lambda _=False, c=color: self._copy_hex(c))
            self.swatches.add(chip)

    def _copy_hex(self, color: str) -> None:
        QGuiApplication.clipboard().setText(color)
        self.notify.emit(f"{color} copied")

    def _show_results(self, roles: Optional[designer.Roles]) -> None:
        targets = self.targets()
        for (block, code), color, target in zip(self._results, self._layers.colors, targets):
            final = target or color
            block.set_color(final)
            code.setText(final)
            code.setProperty("small", not target)
            block.setToolTip(f"{color} becomes {final}" if target else f"{color} keeps its color")
            refresh_style(code)

    def _export_tip(self, source: str, app: bool) -> str:
        if app:
            program = rules.APP_NAMES[source]
            return f"{program} on this computer writes a recolored copy beside your file. Your original is never changed."
        if source == "ai":
            return "Illustrator's own format can only be written by Illustrator, so this saves a PDF that Illustrator opens."
        if source in ("psd", "psb"):
            return "Save a recolored copy of your Photoshop file, with its layers. Your original is never changed."
        return "Save the recolored file at full size, in the format it came in. Your original is never changed."

    def _rebuild_export_menu(self, source: str, app: bool) -> None:
        menu = self.export_menu
        menu.clear()
        formats = self.export_formats()
        others = formats if app else formats[1:]
        for key in others:
            action = menu.addAction(f"Export as {file_export.SHORT_NAMES[key]}…  ({file_export.label(key, source)})")
            action.triggered.connect(lambda _=False, k=key: self.export_file_dialog(k))
        menu.addSeparator()
        swatches = menu.addAction("Export palette swatches (.ase)…")
        swatches.setEnabled(bool(self._colors))
        swatches.triggered.connect(self.export_swatches_dialog)

    # ----------------------------------------------------------------------------------
    # Copying and saving the preview
    # ----------------------------------------------------------------------------------

    def preview_image(self) -> QImage:
        """The recolored picture at the size it is worked on."""
        pixels = self.recolored()
        return _qimage(pixels) if pixels is not None else QImage()

    def copy_preview(self) -> None:
        if self._layers is not None:
            QGuiApplication.clipboard().setImage(self.preview_image())
            self.notify.emit("Preview copied as a picture")

    def save_preview(self, path) -> bool:
        if self._layers is None:
            return False
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
        stem = Path(self._upload_path).stem if self._upload_path else "preview"
        start = str(Path(self.last_dir or str(Path.home())) / f"{stem} - preview.png")
        path, _ = QFileDialog.getSaveFileName(self, "Save preview as PNG", start, "PNG picture (*.png)")
        if path:
            self.save_preview(path)

    # ----------------------------------------------------------------------------------
    # App links: Photoshop, Illustrator or InDesign writes the file
    # ----------------------------------------------------------------------------------

    def set_app_links(self, links: Optional[Mapping[str, Any]]) -> None:
        """Let design programs write the exported file. See `core.project.run_app_link`.

        `links` maps a source format ("psd", "psb", "ai", "indd", "idml") to a link; None or
        an empty dict removes them all. `available()` is asked once here.
        """
        self._links = {str(key).lower().lstrip("."): link for key, link in dict(links or {}).items() if link is not None}
        self._link_ok = {key: rules.link_available(link) for key, link in self._links.items()}
        self._show()

    def app_link(self) -> Any:
        """The link for the open file's format, when there is one that can be used now."""
        source = self.source_format()
        return self._links.get(source) if self._link_ok.get(source) and self._upload_path else None

    def app_found(self) -> bool:
        return self.app_link() is not None

    # ----------------------------------------------------------------------------------
    # Exporting
    # ----------------------------------------------------------------------------------

    def export_formats(self) -> list[str]:
        """The formats Colorroom itself writes for this file; the first is the one it came in."""
        return rules.export_formats(self.source_format())

    def _recipe(self) -> file_export.Recipe:
        return file_export.Recipe(
            list(self._layers.colors), self.targets(), list(self._left_out), list(self._layers.blends), list(self._layers.flat)
        )

    def _export_work(self, path, key: Optional[str] = None):
        """(target, work): `work(progress, cancelled)` saves the file and returns its report."""
        formats = self.export_formats()
        target = Path(str(path))
        typed = file_export.source_format(target)
        if typed in formats:
            key = typed  # the ending the person typed wins
        elif key not in formats:
            key = formats[0]
        if typed != key:
            target = target.with_name(target.name + file_export.FORMATS[key][1])
        recipe = self._recipe()
        source, page = self._upload_path, self._upload_page
        fallback = None if source else self._source

        def work(progress=None, cancelled=None):
            return file_export.export_upload(source, recipe, target, key, page, fallback, progress, cancelled)

        return target, work

    def export_file(self, path, key: Optional[str] = None):
        """Save the recolored file to `path` now (the format follows its ending, or `key`)."""
        if self._layers is None:
            return None
        _, work = self._export_work(path, key)
        try:
            report = work()
        except ImageError as error:
            self.status.emit(str(error), True)
            return None
        self._export_done(report)
        return report

    def export_primary(self) -> None:
        """The Export button (and Ctrl+E): the app writes the file, or Colorroom asks where."""
        if self._layers is None or self._export_tasks:
            return
        if self.app_found():
            self.export_with_app()
        elif self.source_format() != "indd":
            self.export_file_dialog()

    def export_file_dialog(self, key: Optional[str] = None) -> None:
        if self._layers is None or self._export_tasks:
            return
        source = file_export.source_format(self._upload_path)
        formats = self.export_formats()
        first = key if key in formats else formats[0]
        patterns = {"jpg": "*.jpg *.jpeg", "tiff": "*.tif *.tiff"}
        filters = {key: f"{file_export.label(key, source)} ({patterns.get(key, '*' + file_export.FORMATS[key][1])})" for key in formats}
        order = [first, *(key for key in formats if key != first)]
        start = file_export.default_target(self._upload_path, first, self.last_dir)
        path, chosen = QFileDialog.getSaveFileName(
            self, "Export the recolored file", str(start), ";;".join(filters[key] for key in order), filters[first]
        )
        if not path:
            return
        picked = next((key for key, text in filters.items() if text == chosen), first)
        target, work = self._export_work(path, picked)
        self._start(work, self._export_finished, self._export_failed, self._export_tasks)
        self.status.emit(f"Saving {target.name}…", False)
        self._show()

    def export_request(self) -> Optional[rules.ExportRequest]:
        """What an app link is given (see `core.project.ExportRequest`)."""
        if self._layers is None or not self._upload_path:
            return None
        roles = self.roles()
        return rules.ExportRequest(
            self._upload_path,
            self.source_format(),
            self._upload_page,
            self._recipe(),
            rules.suggested_target(self._upload_path),
            tuple(self._colors),
            roles.as_dict() if roles is not None else {},
        )

    def export_with_app(self) -> None:
        link, request = self.app_link(), self.export_request()
        if link is None or request is None or self._export_tasks:
            return
        program = rules.APP_NAMES.get(request.source_format, "The program")
        if self.confirm_app is not None and not self.confirm_app(program, Path(request.source_path).name):
            return

        def work(progress, cancelled):
            progress(0.02, f"{program} is recoloring a copy of {Path(request.source_path).name}…")
            return rules.run_app_link(link, request, progress, cancelled)

        self._start(work, self._export_finished, self._export_failed, self._export_tasks)
        self.status.emit(f"Sending the colors to {program}…", False)
        self._show()

    def _export_done(self, report) -> None:
        self.last_report = report
        summary, _, saved, ok = rules.report_parts(report)
        if saved:
            self.last_dir = str(Path(saved).parent)
        self.status.emit(summary.splitlines()[0] if summary else "Export finished.", not ok)
        self.show_report(report)

    def _export_finished(self, task_id: int, report) -> None:
        self._finish(task_id, self._export_tasks)
        self._export_done(report)
        self._show()

    def _export_failed(self, task_id: int, error) -> None:
        self._finish(task_id, self._export_tasks)
        self._show()
        if error is None:
            self.status.emit("Export cancelled.", False)
        else:
            known = isinstance(error, ImageError) or type(error).__name__.endswith(("BridgeError", "NotAvailable", "AppBusy"))
            self.status.emit(str(error) if known else f"The file could not be saved: {error}", True)

    def export_swatches(self, path) -> bool:
        """Save the palette being tried as Adobe swatches (.ase), with the existing palette code."""
        if not self._colors:
            return False
        target = Path(path)
        if target.suffix.lower() != ".ase":
            target = target.with_name(target.name + ".ase")
        try:
            save_palette(self._colors, target, self.palette_label() or "Colorroom palette", self._names)
        except (OSError, ValueError) as error:
            self.status.emit(f"The swatches could not be saved: {error}", True)
            return False
        self.last_dir = str(target.parent)
        self.status.emit(f"Swatches saved: {target}  To load them in Photoshop: Window > Swatches, then the panel menu > Import Swatches.", False)
        self.notify.emit(f"Saved {target.name}")
        return True

    def export_swatches_dialog(self) -> None:
        if not self._colors:
            return
        name = designer.slug(self.palette_label(), "palette")
        start = str(Path(self.last_dir or str(Path.home())) / f"{name}.ase")
        path, _ = QFileDialog.getSaveFileName(self, "Export palette swatches", start, "Adobe Swatch Exchange (*.ase)")
        if path:
            self.export_swatches(path)

    # ----------------------------------------------------------------------------------
    # The report card
    # ----------------------------------------------------------------------------------

    def show_report(self, report) -> None:
        summary, details, saved, ok = rules.report_parts(report)
        lines = summary.splitlines() or ["Export finished."]
        self.report_title.setText(lines[0])
        self.report_summary.setText("\n".join(lines[1:]))
        self.report_summary.setVisible(len(lines) > 1)
        extra = details if details and details.strip() != summary.strip() else ""
        self.report_details.setText(extra)
        self.report_details_button.setVisible(bool(extra))
        self.report_details_button.setChecked(False)
        self.show_folder_button.setEnabled(bool(saved))
        self.report_card.setProperty("problem", not ok)
        refresh_style(self.report_card)
        self.report_card.show()

    def hide_report(self) -> None:
        self.report_card.hide()

    def _toggle_report_details(self, open_: bool) -> None:
        self.report_scroll.setVisible(open_)
        self.report_details_button.setText("Hide details" if open_ else "Show details")

    def report_text(self) -> str:
        if self.last_report is None:
            return ""
        summary, details, _, _ = rules.report_parts(self.last_report)
        return details or summary

    def copy_report(self) -> None:
        if self.last_report is not None:
            QGuiApplication.clipboard().setText(self.report_text())
            self.notify.emit("Report copied")

    def show_in_folder(self) -> bool:
        """Open the folder of the exported file in the file manager."""
        if self.last_report is None:
            return False
        saved = rules.report_parts(self.last_report)[2]
        if not saved:
            return False
        return QDesktopServices.openUrl(QUrl.fromLocalFile(str(Path(saved).parent)))

    # ----------------------------------------------------------------------------------
    # Background work
    # ----------------------------------------------------------------------------------

    def _start(self, work, finished, failed, pool: dict) -> Task:
        self._task_id += 1
        task = Task(self._task_id, work)
        task.signals.finished.connect(finished)
        task.signals.failed.connect(failed)
        task.signals.progress.connect(lambda _id, fraction, text: self.progress.emit(fraction, text))
        pool[task.task_id] = task
        self.busy.emit(True)
        QThreadPool.globalInstance().start(task)
        return task

    def _finish(self, task_id: int, pool: dict) -> None:
        pool.pop(task_id, None)
        if not self.is_busy():
            self.busy.emit(False)

    def is_busy(self) -> bool:
        return bool(self._export_tasks or self._open_tasks)

    def cancel_work(self) -> None:
        for task in (*self._export_tasks.values(), *self._open_tasks.values()):
            task.cancel()
