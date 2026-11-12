"""The main window: source image, color table, design palette and shade inspector."""

from __future__ import annotations

import re
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Callable, Optional

from PySide6.QtCore import QSettings, Qt, QThreadPool, Slot
from PySide6.QtGui import QAction, QGuiApplication, QKeySequence, QShortcut
from PySide6.QtWidgets import (
    QApplication,
    QComboBox,
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QSizePolicy,
    QSlider,
    QSpinBox,
    QSplitter,
    QVBoxLayout,
    QWidget,
)

from .. import APP_NAME, __version__
from ..core import palettes
from ..core.exporters import PALETTE_FORMATS, palette_text, save_palette, write_inventory_csv
from ..core.index import MAX_PIXELS, AnalysisCancelled, ColorIndex, ImageError, Point
from ..core.loader import IMAGE_EXTENSIONS, LoadedImage, load_image
from ..core.sample import SAMPLE_FILE_NAME, SAMPLE_NAME, sample_image
from .image_view import ImageView, Marker, qimage_to_rgba
from .inspector import InspectorPanel
from .inventory import InventoryView, format_share
from .palette_panel import PalettePanel
from .tasks import Task
from .widgets import (
    BrandMark,
    ElidedLabel,
    Panel,
    ResponsiveRow,
    Toast,
    field_label,
    refresh_style,
)

_MAX_RECENT = 8
_SORTS = (("variety", "Color variety"), ("frequency", "Most frequent"), ("hex", "HEX ascending"))
_TABLE_HINT = "Hover a row to locate it in the image. Click it for related shades. Use Pick to build a palette."


@dataclass(frozen=True)
class Located:
    """A color pinned to a spot in the image: the inspected color or the hovered one."""

    index: int  # distinct index in the ColorIndex
    color: str  # HEX of the image color
    point: Point  # where the marker goes
    palette_color: Optional[str] = None  # the palette swatch this came from, if any
    related: bool = False  # True for a shade listed in the inspector


class MainWindow(QMainWindow):
    def __init__(self, persist: bool = True) -> None:
        super().__init__()
        self.setWindowTitle(APP_NAME)
        self.setAcceptDrops(True)
        self.settings: Optional[QSettings] = (
            QSettings(QSettings.Format.IniFormat, QSettings.Scope.UserScope, "ColorroomFABLE", "ColorroomFABLE")
            if persist
            else None
        )

        # --- state ---------------------------------------------------------------------
        self.index: Optional[ColorIndex] = None
        self.loaded: Optional[LoadedImage] = None
        self.palette: Optional[palettes.Palette] = None
        self.stem = "colorroom"
        self.picks: list[str] = []
        self.picking = False
        self.inspected: Optional[Located] = None
        self.highlighted: Optional[Located] = None
        self.detail_steps: list[int] = [1]
        self.start_maximized = False
        self._syncing = False  # True while the code (not the user) changes the filter controls
        self._recent: list[str] = []
        self._last_dir = ""
        self._tasks: dict[int, Task] = {}
        self._task_kinds: dict[int, str] = {}
        self._task_id = 0
        self._load_task = 0  # id of the newest image-loading task
        self._load_is_sample = False
        self.pool = QThreadPool.globalInstance()

        self._build_ui()
        self._build_menus()
        self._restore_settings()
        self._reset()

    # ==================================================================================
    # Construction
    # ==================================================================================

    def _build_ui(self) -> None:
        central = QWidget()
        self.setCentralWidget(central)
        root = QVBoxLayout(central)
        root.setContentsMargins(14, 8, 14, 4)
        root.setSpacing(10)

        # --- header --------------------------------------------------------------------
        header = QHBoxLayout()
        header.setSpacing(10)
        header.addWidget(BrandMark(28))
        brand = QLabel("Colorroom")
        brand.setObjectName("brand")
        header.addWidget(brand)
        badge = QLabel(f"FABLE {__version__.split('a')[0]} · Runs on your device")
        badge.setObjectName("badge")
        header.addWidget(badge)
        header.addStretch(1)
        self.open_button = QPushButton("Open image…")
        self.open_button.setProperty("primary", True)
        self.open_button.setToolTip("Choose an image file (Ctrl+O). You can also drop one anywhere on the window.")
        self.paste_button = QPushButton("Paste")
        self.paste_button.setToolTip("Analyze the image on the clipboard (Ctrl+V)")
        self.sample_button = QPushButton("Try sample")
        self.sample_button.setProperty("link", True)
        for button in (self.open_button, self.paste_button, self.sample_button):
            header.addWidget(button)
        root.addLayout(header)

        # --- source image --------------------------------------------------------------
        self.source_panel = Panel("Source image", "Open an image to get started.")
        self.pick_button = QPushButton("Pick from image")
        self.pick_button.setCheckable(True)
        self.pick_button.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
        self.pick_button.setToolTip("Turn the pointer into an eyedropper (P). Click a spot to pick its color. Esc stops.")
        self.source_panel.header.addWidget(self.pick_button, 0, Qt.AlignmentFlag.AlignTop)
        self.image_view = ImageView()
        self.source_panel.body.addWidget(self.image_view, 1)
        meta = QHBoxLayout()
        meta.setSpacing(14)
        self.file_label = ElidedLabel()
        self.file_label.setProperty("strong", True)
        self.readout = QLabel()
        self.readout.setObjectName("readout")
        self.readout.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        meta.addWidget(self.file_label, 1)
        meta.addWidget(self.readout)
        self.source_panel.body.addLayout(meta)

        # --- palette and inspector -----------------------------------------------------
        self.palette_panel = PalettePanel()
        self.inspector = InspectorPanel()

        # --- inventory -----------------------------------------------------------------
        self.inventory_panel = Panel("Image colors", "Every distinct HEX value in the image.")
        self.export_csv_button = QPushButton("Export all as CSV…")
        self.inventory_panel.header.addWidget(self.export_csv_button, 0, Qt.AlignmentFlag.AlignTop)

        detail = QWidget()
        detail_row = QHBoxLayout(detail)
        detail_row.setContentsMargins(0, 0, 0, 0)
        detail_row.setSpacing(8)
        detail_row.addWidget(field_label("Shades to show"))
        self.limit_spin = QSpinBox()
        self.limit_spin.setGroupSeparatorShown(True)
        self.limit_spin.lineEdit().setTextMargins(5, 0, 5, 0)
        self.limit_spin.setFixedWidth(104)
        self.limit_spin.setRange(1, 1)
        self.limit_spin.setToolTip("List only the first N colors of the variety order")
        detail_row.addWidget(self.limit_spin)
        self.detail_slider = QSlider(Qt.Orientation.Horizontal)
        self.detail_slider.setRange(0, 0)
        self.detail_slider.setMinimumWidth(110)
        self.detail_slider.setToolTip(
            "Left: fewer, more distinct shades. Right: every shade.\n"
            "The slider narrows the list and new picks. Existing palette picks stay selected."
        )
        detail_row.addWidget(self.detail_slider, 1)

        filters = QWidget()
        filter_row = QHBoxLayout(filters)
        filter_row.setContentsMargins(0, 0, 0, 0)
        filter_row.setSpacing(8)
        filter_row.addWidget(field_label("Find HEX"))
        self.filter_edit = QLineEdit()
        self.filter_edit.setPlaceholderText("e.g. #B47")
        self.filter_edit.setMaxLength(7)
        self.filter_edit.setClearButtonEnabled(True)
        self.filter_edit.setFixedWidth(116)
        filter_row.addWidget(self.filter_edit)
        filter_row.addWidget(field_label("Sort"))
        self.sort_combo = QComboBox()
        for key, label in _SORTS:
            self.sort_combo.addItem(label, key)
        filter_row.addWidget(self.sort_combo)
        self.clear_filters_button = QPushButton("Clear filters")
        filter_row.addWidget(self.clear_filters_button)
        filter_row.addStretch(1)
        self.inventory_panel.body.addWidget(ResponsiveRow(detail, filters))
        self.table = InventoryView()
        self.inventory_panel.body.addWidget(self.table, 1)

        # --- layout: image above the color table, palette above the inspector ----------
        self.left_splitter = QSplitter(Qt.Orientation.Vertical)
        self.right_splitter = QSplitter(Qt.Orientation.Vertical)
        self.main_splitter = QSplitter(Qt.Orientation.Horizontal)
        for splitter in (self.left_splitter, self.right_splitter, self.main_splitter):
            splitter.setHandleWidth(12)
            splitter.setChildrenCollapsible(False)
        self.left_splitter.addWidget(self.source_panel)
        self.left_splitter.addWidget(self.inventory_panel)
        self.right_splitter.addWidget(self.palette_panel)
        self.right_splitter.addWidget(self.inspector)
        self.main_splitter.addWidget(self.left_splitter)
        self.main_splitter.addWidget(self.right_splitter)
        self.source_panel.setMinimumSize(430, 270)
        self.inventory_panel.setMinimumWidth(430)
        self.palette_panel.setMinimumSize(390, 270)
        self.inspector.setMinimumSize(390, 310)
        self.left_splitter.setSizes([440, 360])
        self.right_splitter.setSizes([414, 386])
        self.main_splitter.setSizes([850, 560])
        self.main_splitter.setStretchFactor(0, 3)
        self.main_splitter.setStretchFactor(1, 2)
        self.left_splitter.setStretchFactor(0, 1)
        self.left_splitter.setStretchFactor(1, 1)
        self.right_splitter.setStretchFactor(0, 1)
        self.right_splitter.setStretchFactor(1, 1)
        root.addWidget(self.main_splitter, 1)

        # --- status bar ----------------------------------------------------------------
        self.status_label = QLabel()
        self.status_label.setObjectName("status")
        self.progress = QProgressBar()
        self.progress.setRange(0, 1000)
        self.progress.setTextVisible(False)
        self.progress.setFixedWidth(190)
        self.cancel_button = QPushButton("Cancel")
        self.cancel_button.setProperty("link", True)
        note = QLabel("HEX values are sRGB · fully transparent pixels are skipped")
        note.setProperty("small", True)
        bar = self.statusBar()
        bar.setSizeGripEnabled(False)
        bar.setContentsMargins(12, 0, 12, 3)
        bar.addWidget(self.status_label, 1)
        bar.addPermanentWidget(self.progress)
        bar.addPermanentWidget(self.cancel_button)
        bar.addPermanentWidget(note)
        self.progress.hide()
        self.cancel_button.hide()
        self.toast = Toast(central)

        # --- wiring --------------------------------------------------------------------
        self.open_button.clicked.connect(self.open_dialog)
        self.paste_button.clicked.connect(self.paste_image)
        self.sample_button.clicked.connect(self.open_sample)
        self.cancel_button.clicked.connect(self.cancel_work)
        self.image_view.openRequested.connect(self.open_dialog)
        self.image_view.pixelClicked.connect(self._pixel_clicked)
        self.pick_button.toggled.connect(self.set_picking)

        self.palette_panel.settingsChanged.connect(self.update_palette)
        self.palette_panel.swatchHovered.connect(self._swatch_hovered)
        self.palette_panel.swatchClicked.connect(self._swatch_clicked)
        self.palette_panel.pickRemoved.connect(lambda color: self.select_color(color, toggle=True))
        self.palette_panel.picksCleared.connect(self.clear_picks)
        self.palette_panel.copyRequested.connect(self.copy_palette)
        self.palette_panel.exportRequested.connect(self.export_palette)

        self.inspector.shadeHovered.connect(self._shade_hovered)
        self.inspector.shadeChosen.connect(self._shade_chosen)
        self.inspector.copyRequested.connect(self.copy_text)
        self.inspector.pickRequested.connect(lambda color: self.select_color(color, toggle=True))

        self.limit_spin.valueChanged.connect(self._limit_typed)
        self.detail_slider.valueChanged.connect(self._detail_moved)
        self.filter_edit.textChanged.connect(self.update_inventory)
        self.sort_combo.currentIndexChanged.connect(self.update_inventory)
        self.clear_filters_button.clicked.connect(self.clear_filters)
        self.export_csv_button.clicked.connect(self.export_csv)
        self.table.rowHovered.connect(self._row_hovered)
        self.table.rowActivated.connect(lambda distinct: self.inspect(distinct, reveal=False))
        self.table.pickToggled.connect(lambda color: self.select_color(color, toggle=True))

        escape = QShortcut(QKeySequence(Qt.Key.Key_Escape), self)
        escape.activated.connect(lambda: self.pick_button.setChecked(False))

        # A comfortable default size; small screens start maximized instead.
        width, height = 1460, 940
        screen = QGuiApplication.primaryScreen()
        if screen is not None:
            available = screen.availableGeometry()
            self.start_maximized = available.width() < width + 60 or available.height() < height + 60
            width = min(width, int(available.width() * 0.96))
            height = min(height, int(available.height() * 0.92))
        self.resize(width, height)

    def _build_menus(self) -> None:
        def action(text: str, slot: Callable, shortcut=None) -> QAction:
            item = QAction(text, self)
            if shortcut is not None:
                item.setShortcut(QKeySequence(shortcut))
            item.triggered.connect(lambda _=False: slot())
            return item

        file_menu = self.menuBar().addMenu("&File")
        file_menu.addAction(action("&Open Image…", self.open_dialog, QKeySequence.StandardKey.Open))
        file_menu.addAction(action("Open &Sample", self.open_sample))
        file_menu.addAction(action("&Paste Image", self.paste_image, QKeySequence.StandardKey.Paste))
        self.recent_menu = file_menu.addMenu("Open &Recent")
        file_menu.addSeparator()
        self.export_actions: list[QAction] = []
        export_menu = file_menu.addMenu("&Export Palette")
        for key, (label, extension) in PALETTE_FORMATS.items():
            item = action(f"{label}  ({extension})", lambda k=key: self.export_palette(k))
            export_menu.addAction(item)
            self.export_actions.append(item)
        self.export_csv_action = action("Export &All Colors as CSV…", self.export_csv, "Ctrl+Shift+E")
        file_menu.addAction(self.export_csv_action)
        file_menu.addSeparator()
        file_menu.addAction(action("E&xit", self.close, "Ctrl+Q"))

        edit_menu = self.menuBar().addMenu("&Edit")
        self.copy_palette_action = action("&Copy Palette", self.copy_palette, "Ctrl+Shift+C")
        edit_menu.addAction(self.copy_palette_action)
        self.pick_action = QAction("&Pick from Image", self)
        self.pick_action.setCheckable(True)
        self.pick_action.setShortcut(QKeySequence("P"))
        self.pick_action.triggered.connect(self.pick_button.setChecked)
        edit_menu.addAction(self.pick_action)
        self.clear_picks_action = action("Clear Picked C&olors", self.clear_picks)
        edit_menu.addAction(self.clear_picks_action)
        self.clear_filters_action = action("Clear &Filters", self.clear_filters)
        edit_menu.addAction(self.clear_filters_action)

        help_menu = self.menuBar().addMenu("&Help")
        help_menu.addAction(action(f"&About {APP_NAME}", self.show_about))

    def show_initial(self) -> None:
        """Show the window the way it was last left; on small screens start maximized."""
        if self.start_maximized:
            self.showMaximized()
        else:
            self.show()

    # ==================================================================================
    # Settings
    # ==================================================================================

    def _splitters(self):
        return (("main", self.main_splitter), ("left", self.left_splitter), ("right", self.right_splitter))

    def _restore_settings(self) -> None:
        if self.settings is None:
            self._rebuild_recent()
            return
        geometry = self.settings.value("window/geometry")
        if geometry is not None and self.restoreGeometry(geometry):
            self.start_maximized = False  # restoreGeometry remembers a maximized window itself
        for name, splitter in self._splitters():
            state = self.settings.value(f"window/{name}_splitter")
            if state is not None:
                splitter.restoreState(state)
        self._last_dir = str(self.settings.value("files/last_dir", "") or "")
        recent = self.settings.value("files/recent", []) or []
        self._recent = [recent] if isinstance(recent, str) else [str(path) for path in recent]
        self._rebuild_recent()

    def _save_settings(self) -> None:
        if self.settings is None:
            return
        self.settings.setValue("window/geometry", self.saveGeometry())
        for name, splitter in self._splitters():
            self.settings.setValue(f"window/{name}_splitter", splitter.saveState())
        self.settings.setValue("files/last_dir", self._last_dir)
        self.settings.setValue("files/recent", self._recent)

    def _remember(self, path: str) -> None:
        path = str(Path(path))
        self._last_dir = str(Path(path).parent)
        self._recent = [path, *(item for item in self._recent if item != path)][:_MAX_RECENT]
        self._rebuild_recent()

    def _rebuild_recent(self) -> None:
        self.recent_menu.clear()
        for path in self._recent:
            item = self.recent_menu.addAction(Path(path).name)
            item.setToolTip(path)
            item.triggered.connect(lambda _=False, target=path: self.open_path(target))
        self.recent_menu.setEnabled(bool(self._recent))

    # ==================================================================================
    # Messages
    # ==================================================================================

    def message(self, text: str, error: bool = False) -> None:
        """The persistent status line at the bottom of the window."""
        self.status_label.setText(text)
        self.status_label.setProperty("error", error)
        refresh_style(self.status_label)

    def notify(self, text: str) -> None:
        """A short pop-up message that disappears by itself."""
        self.toast.show_message(text)

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        self.toast.reposition()

    # ==================================================================================
    # Opening images
    # ==================================================================================

    def open_dialog(self) -> None:
        patterns = " ".join(f"*{extension}" for extension in IMAGE_EXTENSIONS)
        path, _ = QFileDialog.getOpenFileName(self, "Open image", self._last_dir, f"Images ({patterns});;All files (*)")
        if path:
            self.open_path(path)

    def open_path(self, path: str) -> None:
        path = str(path)
        self._start_load(lambda: load_image(path), Path(path).name)

    def open_sample(self) -> None:
        def make() -> LoadedImage:
            return LoadedImage(sample_image(), SAMPLE_NAME, "", 1, 0, False)

        self._start_load(make, "the sample image", sample=True)

    def paste_image(self) -> None:
        clipboard = QGuiApplication.clipboard()
        mime = clipboard.mimeData()
        if mime is not None and mime.hasImage():
            image = clipboard.image()
            if not image.isNull():
                self._open_qimage(image, "Pasted image")
                return
        if mime is not None and mime.hasUrls():
            files = [url.toLocalFile() for url in mime.urls() if url.isLocalFile()]
            if files:
                self.open_path(files[0])
                return
        self.notify("The clipboard has no image.")

    def _open_qimage(self, image, name: str) -> None:
        if image is None or image.isNull():
            self.notify("That image could not be read.")
            return
        if image.width() * image.height() > MAX_PIXELS:
            self.message("Choose an image with 24 megapixels or fewer.", error=True)
            return
        rgba = qimage_to_rgba(image)
        self._start_load(lambda: LoadedImage(rgba, name, "", 1, 0, False), f"the {name.lower()}")

    def _start_load(self, loader: Callable[[], LoadedImage], description: str, sample: bool = False) -> None:
        self.cancel_work()
        self._reset()
        self.message(f"Reading {description}…")

        def work(progress, cancelled):
            progress(0.0, f"Reading {description}…")
            loaded = loader()
            if cancelled():
                raise AnalysisCancelled()
            index = ColorIndex.build(
                loaded.rgba, progress=lambda fraction, text: progress(0.05 + 0.95 * fraction, text), cancelled=cancelled
            )
            return loaded, index

        self._load_is_sample = sample
        self._load_task = self._run_task("load", work)

    def _run_task(self, kind: str, work: Callable) -> int:
        self._task_id += 1
        task = Task(self._task_id, work)
        task.signals.finished.connect(self._task_finished)
        task.signals.failed.connect(self._task_failed)
        task.signals.progress.connect(self._task_progress)
        self._tasks[task.task_id] = task
        self._task_kinds[task.task_id] = kind
        self._show_busy(True)
        self.pool.start(task)
        return task.task_id

    def cancel_work(self) -> None:
        """Stop whatever is running in the background."""
        for task in self._tasks.values():
            task.cancel()

    def is_busy(self) -> bool:
        return bool(self._tasks)

    def _show_busy(self, busy: bool) -> None:
        self.progress.setVisible(busy)
        self.cancel_button.setVisible(busy)
        if busy:
            self.progress.setValue(0)

    @Slot(int, float, str)
    def _task_progress(self, task_id: int, fraction: float, text: str) -> None:
        if task_id not in self._tasks:
            return
        if self._task_kinds.get(task_id) == "load" and task_id != self._load_task:
            return
        self.progress.setValue(round(fraction * 1000))
        if text and text != "Done":
            self.message(text)

    def _finish_task(self, task_id: int) -> Optional[str]:
        self._tasks.pop(task_id, None)
        kind = self._task_kinds.pop(task_id, None)
        if not self._tasks:
            self._show_busy(False)
        return kind

    @Slot(int, object)
    def _task_finished(self, task_id: int, result) -> None:
        kind = self._finish_task(task_id)
        if kind == "load":
            if task_id == self._load_task:
                self._loaded(*result)
        elif kind == "csv":
            self.export_csv_button.setEnabled(self.index is not None)
            self.message(f"Shade CSV saved: {result}")
            self.notify("Shade CSV saved")

    @Slot(int, object)
    def _task_failed(self, task_id: int, error) -> None:
        kind = self._finish_task(task_id)
        if kind == "csv":
            self.export_csv_button.setEnabled(self.index is not None)
        if kind == "load" and task_id != self._load_task:
            return  # an older load that was replaced by a newer one
        if error is None:
            if kind == "csv" and self.is_busy():
                return  # an export interrupted by opening another image
            self.message("Cancelled.")
        elif isinstance(error, ImageError):
            self.message(str(error), error=True)
        elif isinstance(error, MemoryError):
            self.message("There was not enough memory to analyze this image.", error=True)
        elif isinstance(error, OSError):
            self.message(f"The file could not be read or saved: {error}", error=True)
        else:
            self.message(f"Unable to process this image: {error}", error=True)

    def _reset(self) -> None:
        """Return to the empty state (what you see before any image is opened)."""
        self.index = None
        self.loaded = None
        self.palette = None
        self.picks = []
        self.inspected = None
        self.highlighted = None
        self.stem = "colorroom"
        self.pick_button.setChecked(False)
        self.image_view.set_image(None)
        self.image_view.set_markers(None, None)
        self.source_panel.subtitle.setText("Open an image to get started.")
        self.file_label.setText("")
        self.readout.setText("")
        self.palette_panel.clear()
        self.inspector.clear()
        self.table.inventory.set_rows(None, None)
        self.table.inventory.set_picks(())
        self.table.set_empty_text("No colors loaded.")
        self.inventory_panel.subtitle.setText("Every distinct HEX value in the image.")
        self._syncing = True
        self.filter_edit.clear()
        self.limit_spin.setRange(1, 1)
        self.detail_slider.setRange(0, 0)
        self._syncing = False
        self._mark_filter(valid=True)
        self._set_ready(False)
        self.setWindowTitle(APP_NAME)

    def _set_ready(self, ready: bool) -> None:
        for widget in (
            self.pick_button,
            self.limit_spin,
            self.detail_slider,
            self.filter_edit,
            self.sort_combo,
            self.clear_filters_button,
            self.export_csv_button,
        ):
            widget.setEnabled(ready)
        self.palette_panel.set_ready(ready)
        for item in (
            self.export_csv_action,
            self.pick_action,
            self.clear_picks_action,
            self.clear_filters_action,
            self.copy_palette_action,
            *self.export_actions,
        ):
            item.setEnabled(ready)

    def _loaded(self, loaded: LoadedImage, index: ColorIndex) -> None:
        self.loaded = loaded
        self.index = index
        cleaned = re.sub(r"[^\w.-]+", "-", Path(loaded.name).stem if loaded.path else loaded.name)[:80]
        self.stem = Path(SAMPLE_FILE_NAME).stem if self._load_is_sample else (cleaned.strip("-") or "colorroom")
        self.image_view.set_image(index.rgba)
        self.file_label.setText(loaded.name)
        summary = f"{index.width:,} × {index.height:,} · {index.visible_pixels:,} visible pixels analyzed"
        if loaded.color_managed:
            summary += " · color profile converted to sRGB"
        if loaded.frames > 1:
            summary += f" · frame {loaded.frame + 1} of {loaded.frames}"
        self.source_panel.subtitle.setText(summary)
        self.setWindowTitle(f"{loaded.name} · {APP_NAME}")
        self._set_ready(True)
        self._setup_detail()
        self.update_palette()
        self.update_inventory()
        if loaded.path:
            self._remember(loaded.path)
        if self._load_is_sample:
            self.message("Sample loaded. Open your own image whenever you’re ready.")
        else:
            self.message("Image analyzed. Your palette and shades are ready.")

    # ==================================================================================
    # Drag and drop
    # ==================================================================================

    @staticmethod
    def _dropped_file(mime) -> Optional[str]:
        if mime.hasUrls():
            for url in mime.urls():
                if url.isLocalFile():
                    return url.toLocalFile()
        return None

    def dragEnterEvent(self, event) -> None:
        mime = event.mimeData()
        if self._dropped_file(mime) or mime.hasImage():
            event.acceptProposedAction()
            self.image_view.set_drop_active(True)

    def dragLeaveEvent(self, event) -> None:
        self.image_view.set_drop_active(False)

    def dropEvent(self, event) -> None:
        self.image_view.set_drop_active(False)
        mime = event.mimeData()
        path = self._dropped_file(mime)
        if path:
            event.acceptProposedAction()
            self.open_path(path)
        elif mime.hasImage():
            event.acceptProposedAction()
            self._open_qimage(mime.imageData(), "Dropped image")

    # ==================================================================================
    # Palette
    # ==================================================================================

    def update_palette(self) -> None:
        if self.index is None:
            return
        slow = self.index.distinct > 400_000
        if slow:
            QApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)
        try:
            self.palette = palettes.build_palette(
                self.index,
                self.palette_panel.current_mode(),
                self.palette_panel.current_size(),
                self.palette_panel.current_theme(),
                self.picks,
            )
        finally:
            if slow:
                QApplication.restoreOverrideCursor()
        self.palette_panel.show_palette(self.palette, self.picks)
        colors = set(self.palette.colors)
        if self.highlighted is not None and self.highlighted.palette_color:
            self.highlighted = None
        if self.inspected is not None and self.inspected.palette_color and self.inspected.palette_color not in colors:
            # The swatch this inspection came from is gone: keep the color, drop the link.
            self.inspected = replace(self.inspected, palette_color=None)
            self.inspector.set_subject(self.index, self.inspected.index, None, self.inspected.color in self.picks)
        self.palette_panel.grid.set_selected(self.inspected.palette_color if self.inspected else None)
        self._update_markers()

    def select_color(self, color: str, toggle: bool = False) -> None:
        """Add a color to the picks (or remove it again when `toggle` is set)."""
        if self.index is None:
            return
        if color in self.picks:
            if not toggle:
                self.notify(f"{color} is already selected.")
                return
            self.picks = [value for value in self.picks if value != color]
        else:
            if len(self.picks) >= palettes.MAX_PICKS:
                self.notify(f"You can select up to {palettes.MAX_PICKS} source colors.")
                return
            self.picks = [*self.picks, color]
            if self.palette_panel.current_mode() in palettes.IMAGE_MODES:
                self.palette_panel.set_mode("custom")
        self._picks_changed()

    def clear_picks(self) -> None:
        if self.picks:
            self.picks = []
            self._picks_changed()

    def _picks_changed(self) -> None:
        self.table.inventory.set_picks(self.picks)
        if self.inspected is not None:
            self.inspector.set_picked(self.inspected.color in self.picks)
        self.update_palette()

    def copy_text(self, text: str, confirmation: str) -> None:
        QGuiApplication.clipboard().setText(text)
        self.notify(confirmation)

    def copy_palette(self) -> None:
        if self.palette is not None and self.palette.entries:
            self.copy_text(palette_text(self.palette.colors), "Palette copied")

    def export_palette(self, kind: str = "css") -> None:
        if self.palette is None or not self.palette.entries:
            return
        filters = {key: f"{label} (*{extension})" for key, (label, extension) in PALETTE_FORMATS.items()}
        extension = PALETTE_FORMATS[kind][1]
        start = str(Path(self._last_dir or str(Path.home())) / f"{self.stem}-palette{extension}")
        path, chosen = QFileDialog.getSaveFileName(self, "Export palette", start, ";;".join(filters.values()), filters[kind])
        if not path:
            return
        target = Path(path)
        if not target.suffix:
            chosen_key = next((key for key, text in filters.items() if text == chosen), kind)
            target = target.with_suffix(PALETTE_FORMATS[chosen_key][1])
        roles = [entry.role for entry in self.palette.entries]
        try:
            save_palette(self.palette.colors, target, f"{self.stem} palette", roles)
        except (OSError, ValueError) as error:
            self.message(f"The palette could not be saved: {error}", error=True)
            return
        self._last_dir = str(target.parent)
        self.message(f"Palette saved: {target}")
        self.notify(f"Palette saved as {target.name}")

    # ==================================================================================
    # Inventory
    # ==================================================================================

    def _setup_detail(self) -> None:
        """Slider stops: exact for small counts, then steps of 12% up to every shade."""
        distinct = self.index.distinct
        steps, count = [1], 1
        while count < distinct:
            count = min(distinct, count + 1 if count < 24 else -(-count * 112 // 100))
            steps.append(count)
        self.detail_steps = steps
        self._syncing = True
        self.limit_spin.setRange(1, distinct)
        self.limit_spin.setValue(distinct)
        self.detail_slider.setRange(0, len(steps) - 1)
        self.detail_slider.setValue(len(steps) - 1)
        self.sort_combo.setCurrentIndex(0)
        self._syncing = False
        self.detail_slider.setEnabled(distinct > 1)

    def shade_limit(self) -> int:
        return int(self.limit_spin.value())

    def _limit_typed(self, limit: int) -> None:
        if self._syncing:
            return
        steps = self.detail_steps
        upper = next((position for position, value in enumerate(steps) if value >= limit), len(steps) - 1)
        nearer_lower = upper > 0 and limit - steps[upper - 1] < steps[upper] - limit
        self._syncing = True
        self.detail_slider.setValue(upper - 1 if nearer_lower else upper)
        self._syncing = False
        self.update_inventory()

    def _detail_moved(self, position: int) -> None:
        if self._syncing:
            return
        self._syncing = True
        self.limit_spin.setValue(self.detail_steps[position])
        self._syncing = False
        self.update_inventory()

    def clear_filters(self) -> None:
        if self.index is None:
            return
        self._syncing = True
        self.filter_edit.clear()
        self.limit_spin.setValue(self.index.distinct)
        self.detail_slider.setValue(len(self.detail_steps) - 1)
        self._syncing = False
        self.update_inventory()

    def _mark_filter(self, valid: bool) -> None:
        if bool(self.filter_edit.property("invalid")) == valid:
            self.filter_edit.setProperty("invalid", not valid)
            refresh_style(self.filter_edit)

    def update_inventory(self, *_) -> None:
        if self.index is None or self._syncing:
            return
        index = self.index
        prefix = self.filter_edit.text().strip().lstrip("#")
        valid = re.fullmatch(r"[0-9a-fA-F]{0,6}", prefix) is not None
        self._mark_filter(valid)
        if not valid:
            self.table.inventory.set_rows(index, None)
            self.table.set_empty_text("Enter up to six HEX characters (0–9 or A–F).")
            self.inventory_panel.subtitle.setText(f"{index.distinct:,} distinct shades")
            return
        order = index.order(self.sort_combo.currentData(), self.shade_limit(), prefix)
        self.table.inventory.set_rows(index, order)
        self.table.scrollToTop()
        self.table.set_empty_text("No shades match the current filters.")
        total = int(order.size)
        if total == index.distinct:
            count = f"{total:,} distinct {'shade' if total == 1 else 'shades'}"
        else:
            count = f"{total:,} of {index.distinct:,} shades match"
        self.inventory_panel.subtitle.setText(f"{count} · {_TABLE_HINT}")
        if self.inspected is not None:
            self.table.select_distinct(self.inspected.index)

    def export_csv(self) -> None:
        if self.index is None:
            return
        start = str(Path(self._last_dir or str(Path.home())) / f"{self.stem}-all-shades.csv")
        path, _ = QFileDialog.getSaveFileName(self, "Export all colors", start, "CSV (*.csv)")
        if not path:
            return
        target = Path(path)
        if not target.suffix:
            target = target.with_suffix(".csv")
        self._last_dir = str(target.parent)
        index = self.index
        self.export_csv_button.setEnabled(False)
        self.message("Preparing the full shade CSV…")

        def work(progress, cancelled):
            write_inventory_csv(index, target, progress, cancelled)
            return str(target)

        self._run_task("csv", work)

    # ==================================================================================
    # Inspecting and locating colors
    # ==================================================================================

    def inspect(
        self,
        distinct: int,
        palette_color: Optional[str] = None,
        point: Optional[Point] = None,
        reveal: bool = True,
    ) -> None:
        """Open a color in the shade inspector and mark it on the image.

        `point` overrides where the marker goes (used when a pixel was clicked);
        `reveal` scrolls the table to the color's row.
        """
        if self.index is None:
            return
        color = self.index.hex(distinct)
        if palette_color == color:
            palette_color = None
        self.inspected = Located(distinct, color, point or self.index.locate(distinct), palette_color)
        slow = self.index.distinct > 400_000
        if slow:
            QApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)
        try:
            self.inspector.set_subject(self.index, distinct, palette_color, color in self.picks)
        finally:
            if slow:
                QApplication.restoreOverrideCursor()
        self.palette_panel.grid.set_selected(palette_color)
        self.table.select_distinct(distinct, scroll=reveal)
        self._update_markers()

    def set_picking(self, picking: bool) -> None:
        was_picking = self.picking
        self.picking = bool(picking) and self.index is not None
        if self.pick_button.isChecked() != self.picking:
            self.pick_button.setChecked(self.picking)
        self.pick_action.setChecked(self.picking)
        self.image_view.set_picking(self.picking)
        if self.picking and not was_picking:
            self.message("Eyedropper on: click a spot in the image to pick its color. Press Esc to stop.")
        elif was_picking and not self.picking:
            self.message("Eyedropper off.")

    def _pixel_clicked(self, x: float, y: float) -> None:
        if self.index is None or not self.picking:
            return
        pick = self.index.pick_pixel(x, y, self.shade_limit())
        if pick is None:
            self.notify("That pixel is fully transparent.")
            return
        self.inspect(pick.index, point=pick.point)
        if pick.allowed:
            self.select_color(pick.color)
        else:
            self.notify(f"{pick.color} is outside the current list. Move the Shades to show slider right to add it.")

    def _swatch_hovered(self, entry) -> None:
        if entry is None:
            if self.highlighted is not None and self.highlighted.palette_color and not self.highlighted.related:
                self.highlighted = None
        else:
            self.highlighted = Located(entry.source_index, entry.source_color, entry.point, entry.color)
        self._update_markers()

    def _swatch_clicked(self, entry) -> None:
        self.inspect(entry.source_index, palette_color=entry.color)

    def _shade_hovered(self, entry) -> None:
        if entry is None:
            if self.highlighted is not None and self.highlighted.related:
                self.highlighted = None
        else:
            source = self.inspected.palette_color if self.inspected else None
            self.highlighted = Located(entry.index, entry.color, entry.point, source, related=True)
        self._update_markers()

    def _shade_chosen(self, distinct: int) -> None:
        source = self.inspected.palette_color if self.inspected else None
        self.highlighted = None
        self.inspect(distinct, palette_color=source)

    def _row_hovered(self, distinct: int) -> None:
        if self.index is None:
            return
        if distinct < 0:
            if self.highlighted is not None and not self.highlighted.palette_color and not self.highlighted.related:
                self.highlighted = None
        else:
            self.highlighted = Located(distinct, self.index.hex(distinct), self.index.locate(distinct))
        self._update_markers()

    def _update_markers(self) -> None:
        inspected = Marker(self.inspected.point, self.inspected.color) if self.inspected else None
        highlighted = Marker(self.highlighted.point, self.highlighted.color) if self.highlighted else None
        self.image_view.set_markers(inspected, highlighted)
        match = self.highlighted or self.inspected
        if match is None or self.index is None:
            self.readout.setText("")
        elif match.related:
            self.readout.setText(f"Related image pixel {match.color}")
        elif match.palette_color and match.palette_color != match.color:
            self.readout.setText(f"Palette {match.palette_color} → nearby image pixel {match.color}")
        else:
            pixels = f"{self.index.count(match.index):,} px · {format_share(self.index.share(match.index))}"
            self.readout.setText(f"Image pixel {match.color} · {pixels}")

    # ==================================================================================
    # Misc
    # ==================================================================================

    def show_about(self) -> None:
        QMessageBox.about(
            self,
            f"About {APP_NAME}",
            f"<h3>{APP_NAME} {__version__}</h3>"
            "<p>Find the colors in any image: every distinct HEX shade, where it appears, "
            "its related shades, and palettes built from them.</p>"
            "<p>Everything runs on this computer. Images are never uploaded.</p>"
            "<p>HEX values are sRGB; embedded color profiles are converted to sRGB. "
            "Fully transparent pixels are skipped. For animated images the first frame is read.</p>",
        )

    def closeEvent(self, event) -> None:
        self._save_settings()
        self.cancel_work()
        self.pool.waitForDone(3000)
        super().closeEvent(event)
