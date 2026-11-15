"""The main window.

Four tabs share it, and it opens on the first:
    Your project  (ui/project_tab.py) open your own design file, try palettes on it and
                  export the same file type back
    Image         the source image, color table, design palette and shade inspector
    My palette    (ui/studio.py) the colors you collect and the tools that check them
    Library       (ui/library.py) palettes saved to come back to
The header's Open..., Try sample and Paste, and dropped files, follow the tab: on Your project
they open the file there, everywhere else they go to the Image tab as before.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Callable, Optional

from collections import OrderedDict

from PySide6.QtCore import QObject, QSettings, Qt, QThreadPool, Signal, Slot
from PySide6.QtGui import QAction, QActionGroup, QGuiApplication, QKeySequence, QShortcut
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
    QStackedWidget,
    QTabBar,
    QVBoxLayout,
    QWidget,
)

from .. import APP_NAME, __version__
from ..core import documents, palettes
from ..core.designer import Library, Project
from ..core.exporters import PALETTE_FORMATS, palette_text, save_palette, write_inventory_csv
from ..core.index import MAX_PIXELS, AnalysisCancelled, ColorIndex, ImageError, Point
from ..core.loader import IMAGE_EXTENSIONS, LoadedImage, load_image
from ..core.sample import SAMPLE_FILE_NAME, SAMPLE_NAME, sample_image
from .document_view import DocumentDialog
from .image_view import ImageView, Marker, qimage_to_rgba
from .inspector import InspectorPanel
from .inventory import InventoryView, format_share
from .library import LibraryPage
from .page_strip import PageStrip
from .palette_panel import PalettePanel
from .project_tab import ProjectPage
from .studio import StudioPage
from .tasks import Task
from . import theme
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
_PAGE_CACHE = 3  # analyzed pages kept ready, so going back to one is instant
_SORTS = (("variety", "Color variety"), ("frequency", "Most frequent"), ("hex", "HEX ascending"))
_TABLE_HINT = "Click a row for related shades. Ctrl-click or Shift-click selects several to add to My palette."

#: The tabs, in order. The window opens on Your project.
TAB_PROJECT, TAB_IMAGE, TAB_PALETTE, TAB_LIBRARY = range(4)
MAX_MIN_HEIGHT = 640  # the window fits a 1280 × 720 screen (taskbar and title bar included)


class _PageSignals(QObject):
    """Carries each page summary from the background thread to the window."""

    ready = Signal(object, int, object)  # document, page, PageSummary


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
        self.document: Optional[documents.Document] = None  # the open multi-page file, if any
        self.page = 0
        self._summaries: list = []  # one PageSummary (or None) per page of the document
        self._page_cache: OrderedDict = OrderedDict()  # page -> (LoadedImage, ColorIndex)
        self._page_signals = _PageSignals()
        self._document_dialog: Optional[DocumentDialog] = None
        self.pool = QThreadPool.globalInstance()

        self._build_ui()
        self._build_menus()
        self._restore_settings()
        if persist and not os.environ.get("COLORROOM_NO_APPS"):
            self._connect_design_programs()
        self._reset()
        self._sync_project_sources()
        self.show_tab(TAB_PROJECT)

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
        header.addSpacing(14)
        self.tab_bar = QTabBar()
        self.tab_bar.setObjectName("mainTabs")
        self.tab_bar.setExpanding(False)
        self.tab_bar.setDrawBase(False)
        self.tab_bar.addTab("Your project")
        self.tab_bar.addTab("Image")
        self.tab_bar.addTab("My palette")
        self.tab_bar.addTab("Library")
        self.tab_bar.setTabToolTip(TAB_PROJECT, "Recolor your own design file and export the same file type (Ctrl+1)")
        self.tab_bar.setTabToolTip(TAB_IMAGE, "Find the colors in an image (Ctrl+2)")
        self.tab_bar.setTabToolTip(TAB_PALETTE, "The colors you have collected, with contrast, color-vision and export tools (Ctrl+3)")
        self.tab_bar.setTabToolTip(TAB_LIBRARY, "Palettes you saved, in folders, with your favorites (Ctrl+4)")
        header.addWidget(self.tab_bar)
        header.addStretch(1)
        self.theme_button = QPushButton("Dark mode")
        self.theme_button.setProperty("link", True)
        self.theme_button.setToolTip("Switch between the light and the dark look (Ctrl+D)")
        header.addWidget(self.theme_button)
        self.open_button = QPushButton("Open…")
        self.open_button.setProperty("primary", True)
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
        self.highlight_button = QPushButton("Highlight")
        self.highlight_button.setCheckable(True)
        self.highlight_button.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
        self.highlight_button.setToolTip("Dim the image except for the pixels of the inspected color (H)")
        self.fit_button = QPushButton("Fit")
        self.fit_button.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
        self.fit_button.setToolTip("Show the whole image again. Scroll to zoom, drag to move, double-click to fit.")
        self.fit_button.setEnabled(False)
        self.source_panel.header.addWidget(self.fit_button, 0, Qt.AlignmentFlag.AlignTop)
        self.source_panel.header.addWidget(self.highlight_button, 0, Qt.AlignmentFlag.AlignTop)
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
        self.add_selected_button = QPushButton("Add selected to My palette")
        self.add_selected_button.setToolTip("Add the selected rows to the My palette tab (Ctrl+Enter)")
        self.inventory_panel.header.addWidget(self.add_selected_button, 0, Qt.AlignmentFlag.AlignTop)
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
        self.detail_slider.setMinimumWidth(80)
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
        self.source_panel.setMinimumSize(430, 220)  # low enough for a 1280 × 720 screen
        self.inventory_panel.setMinimumWidth(430)
        self.palette_panel.setMinimumSize(390, 220)
        self.inspector.setMinimumSize(390, 250)
        self.left_splitter.setSizes([440, 360])
        self.right_splitter.setSizes([414, 386])
        self.main_splitter.setSizes([850, 560])
        self.main_splitter.setStretchFactor(0, 3)
        self.main_splitter.setStretchFactor(1, 2)
        self.left_splitter.setStretchFactor(0, 1)
        self.left_splitter.setStretchFactor(1, 1)
        self.right_splitter.setStretchFactor(0, 1)
        self.right_splitter.setStretchFactor(1, 1)
        self.studio = StudioPage()
        self.project = ProjectPage()
        self.pages = QStackedWidget()
        self.pages.addWidget(self.project)
        self.page_strip = PageStrip()
        image_page = QWidget()
        image_row = QHBoxLayout(image_page)
        image_row.setContentsMargins(0, 0, 0, 0)
        image_row.setSpacing(8)
        image_row.addWidget(self.page_strip)
        image_row.addWidget(self.main_splitter, 1)
        self.pages.addWidget(image_page)
        self.pages.addWidget(self.studio)
        self.library_page = LibraryPage(self.studio.library)
        self.pages.addWidget(self.library_page)
        root.addWidget(self.pages, 1)

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
        self.open_button.clicked.connect(self.open_clicked)
        self.paste_button.clicked.connect(self.paste_clicked)
        self.sample_button.clicked.connect(self.sample_clicked)
        self.cancel_button.clicked.connect(self.cancel_clicked)
        self.image_view.openRequested.connect(self.open_dialog)
        self.image_view.pixelClicked.connect(self._pixel_clicked)
        self.pick_button.toggled.connect(self.set_picking)
        self.page_strip.pageChosen.connect(self.show_page)
        self.page_strip.overviewRequested.connect(self.show_whole_document)
        self._page_signals.ready.connect(self._page_summarized)
        for key, step in ((Qt.Key.Key_PageUp, -1), (Qt.Key.Key_PageDown, 1)):
            QShortcut(QKeySequence(key), self).activated.connect(lambda s=step: self.step_page(s))
        self.highlight_button.toggled.connect(self.set_highlighting)
        self.fit_button.clicked.connect(self.image_view.reset_zoom)
        self.image_view.zoomChanged.connect(self._zoom_changed)
        self.tab_bar.currentChanged.connect(self.show_tab)
        self.add_selected_button.clicked.connect(self.add_selected_to_studio)
        self.table.selectionCountChanged.connect(self._selection_count_changed)
        self.palette_panel.keepRequested.connect(self.add_palette_to_studio)
        self.studio.notify.connect(self.notify)
        self.studio.status.connect(self.message)
        self.studio.changed.connect(self._studio_changed)
        self.studio.libraryChanged.connect(self._library_changed)
        self.studio.libraryRequested.connect(lambda: self.show_tab(TAB_LIBRARY))
        self.studio.projectRequested.connect(lambda: self.show_tab(TAB_PROJECT))
        self.project.notify.connect(self.notify)
        self.project.status.connect(self.message)
        self.project.busy.connect(self._project_busy)
        self.project.progress.connect(self._project_progress)
        self.library_page.changed.connect(self._library_changed)
        self.library_page.notify.connect(self.notify)
        self.library_page.openRequested.connect(self._open_from_library)
        self.theme_button.clicked.connect(self.toggle_theme)

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
        file_menu.addAction(action("&Open File…", self.open_clicked, QKeySequence.StandardKey.Open))
        file_menu.addAction(action("Open &Sample", self.sample_clicked))
        file_menu.addAction(action("&Paste Image", self.paste_clicked, QKeySequence.StandardKey.Paste))
        self.recent_menu = file_menu.addMenu("Open &Recent")
        file_menu.addSeparator()
        file_menu.addAction(action("Open Palette &File…", self.studio.open_project_dialog, "Ctrl+Shift+O"))
        file_menu.addAction(action("Sa&ve Palette File…", self.studio.save_project_dialog, "Ctrl+S"))
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

        self.add_selected_action = action("Add Selected Colors to &My Palette", self.add_selected_to_studio, "Ctrl+Return")
        edit_menu.addAction(self.add_selected_action)
        self.keep_palette_action = action("Add &Design Palette to My Palette", self.add_palette_to_studio, "Ctrl+Shift+Return")
        edit_menu.addAction(self.keep_palette_action)

        view_menu = self.menuBar().addMenu("&View")
        view_menu.addAction(action("Your &Project", lambda: self.show_tab(TAB_PROJECT), "Ctrl+1"))
        view_menu.addAction(action("&Image", lambda: self.show_tab(TAB_IMAGE), "Ctrl+2"))
        view_menu.addAction(action("&My Palette", lambda: self.show_tab(TAB_PALETTE), "Ctrl+3"))
        view_menu.addAction(action("&Library", lambda: self.show_tab(TAB_LIBRARY), "Ctrl+4"))
        view_menu.addSeparator()
        self.whole_document_action = action("&Whole Document…", self.show_whole_document, "Ctrl+Shift+D")
        self.whole_document_action.setEnabled(False)
        view_menu.addAction(self.whole_document_action)
        self.highlight_action = QAction("&Highlight Inspected Color", self)
        self.highlight_action.setCheckable(True)
        self.highlight_action.setShortcut(QKeySequence("H"))
        self.highlight_action.triggered.connect(self.highlight_button.setChecked)
        view_menu.addAction(self.highlight_action)
        self.zoom_in_action = action("Zoom &In", lambda: self.image_view.zoom_by(1.5), QKeySequence.StandardKey.ZoomIn)
        self.zoom_out_action = action("Zoom &Out", lambda: self.image_view.zoom_by(1 / 1.5), QKeySequence.StandardKey.ZoomOut)
        self.zoom_fit_action = action("&Fit Image", self.image_view.reset_zoom, "Ctrl+0")
        for item in (self.zoom_in_action, self.zoom_out_action, self.zoom_fit_action):
            view_menu.addAction(item)
        view_menu.addSeparator()
        view_menu.addAction(action("Switch &Dark Mode On or Off", self.toggle_theme, "Ctrl+D"))
        theme_menu = view_menu.addMenu("&Theme")
        self.theme_actions: dict[str, QAction] = {}
        group = QActionGroup(self)
        for mode, label in (("light", "&Light"), ("dark", "&Dark")):
            item = QAction(label, self)
            item.setCheckable(True)
            item.setChecked(theme.MODE == mode)
            item.triggered.connect(lambda _=False, m=mode: self.set_theme(m))
            group.addAction(item)
            theme_menu.addAction(item)
            self.theme_actions[mode] = item

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

    def _connect_design_programs(self) -> None:
        """Let Photoshop, Illustrator and InDesign write exported files, when they are installed.

        Checking starts nothing; a program is started only when the person exports, after
        `_confirm_design_program` asked once.
        """
        try:
            from ..core.app_links import program_links

            self.project.set_app_links(program_links(start=True))
        except Exception:
            self.project.set_app_links(None)  # Colorroom still writes files itself
            return
        self.project.confirm_app = self._confirm_design_program

    def _confirm_design_program(self, program: str, name: str) -> bool:
        """Ask once per program before Colorroom starts and controls it."""
        key = f"apps/{program.lower()}_allowed"
        if self.settings is not None and str(self.settings.value(key, "")) == "true":
            return True
        from PySide6.QtWidgets import QCheckBox, QMessageBox

        stem = Path(name).stem
        suffix = ".indd" if program == "InDesign" else Path(name).suffix
        box = QMessageBox(self)
        box.setWindowTitle(f"Export to {program}")
        box.setIcon(QMessageBox.Icon.Question)
        box.setText(
            f"Colorroom will open {program}, change the colors in a copy of {name}, check every color, "
            f"and save {stem} - Colorroom A{suffix} next to it.\n\nYour original is not changed. "
            f"Please don't use {program} until it is done."
        )
        remember = QCheckBox("Don't ask again")
        box.setCheckBox(remember)
        box.setStandardButtons(QMessageBox.StandardButton.Cancel | QMessageBox.StandardButton.Ok)
        box.button(QMessageBox.StandardButton.Ok).setText("Export")
        if box.exec() != QMessageBox.StandardButton.Ok:
            return False
        if remember.isChecked() and self.settings is not None:
            self.settings.setValue(key, "true")
        return True

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
        self.studio.last_dir = self._last_dir
        self.project.last_dir = str(self.settings.value("files/project_dir", "") or "")
        saved = self.settings.value("studio/project")
        if saved:
            try:
                self.studio.project = Project.from_json(str(saved))
                self.studio.refresh()
            except ValueError:
                pass  # an unreadable saved palette is simply not restored
        self._restore_library()
        count = len(self.studio.library.entries)
        self.tab_bar.setTabText(TAB_LIBRARY, f"Library ({count})" if count else "Library")
        state = self.settings.value("window/studio_splitter")
        if state is not None:
            self.studio.splitter.restoreState(state)
        mode = str(self.settings.value("window/theme", "light") or "light")
        if mode != theme.MODE:
            self.set_theme(mode)
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
        self.settings.setValue("files/project_dir", self.project.last_dir)
        self.settings.setValue("studio/project", self.studio.project.to_json())
        self.settings.setValue("window/studio_splitter", self.studio.splitter.saveState())
        self.settings.setValue("window/theme", theme.MODE)
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
        self.toast.lift = 20
        footer = self.project.footer
        if self.on_project() and footer.isVisible():  # never over the Export button
            top = footer.mapTo(self.centralWidget(), footer.rect().topLeft()).y()
            self.toast.lift = max(20, self.centralWidget().height() - top + 10)
        self.toast.show_message(text)

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        self.toast.reposition()

    # ==================================================================================
    # The header follows the tab
    # ==================================================================================

    def on_project(self) -> bool:
        return self.pages.currentIndex() == TAB_PROJECT

    def open_clicked(self) -> None:
        """Open... and Ctrl+O: on Your project the file opens there, elsewhere on the Image tab."""
        if self.on_project():
            self.project.open_dialog()
        else:
            self.open_dialog()

    def sample_clicked(self) -> None:
        if self.on_project():
            self.project.open_sample()
        else:
            self.open_sample()

    def paste_clicked(self) -> None:
        if self.on_project():
            self.project.paste()
        else:
            self.paste_image()

    def cancel_clicked(self) -> None:
        self.cancel_work()
        self.project.cancel_work()

    def _project_busy(self, busy: bool) -> None:
        self._show_busy(busy or bool(self._tasks))

    def _project_progress(self, fraction: float, text: str) -> None:
        self.progress.setValue(round(fraction * 1000))
        if text and text != "Done":
            self.message(text)

    def _sync_header(self) -> None:
        """One primary button at a time: on Your project the tab's own button is the primary one."""
        project = self.on_project()
        self.open_button.setText("Open file…" if project else "Open…")
        self.open_button.setToolTip(
            "Open your own design file here: PSD, AI, PDF, PNG or JPG (Ctrl+O)"
            if project
            else "Open an image, a PDF, an Illustrator or a Photoshop file (Ctrl+O). You can also drop one anywhere on the window."
        )
        if bool(self.open_button.property("primary")) == project:
            self.open_button.setProperty("primary", not project)
            refresh_style(self.open_button)

    def _sync_project_sources(self) -> None:
        self.project.set_sources(self.studio.project, self.studio.library)

    # ==================================================================================
    # Opening images
    # ==================================================================================

    def open_dialog(self) -> None:
        patterns = " ".join(f"*{extension}" for extension in IMAGE_EXTENSIONS)
        papers = " ".join(f"*{extension}" for extension in documents.DOCUMENT_EXTENSIONS)
        path, _ = QFileDialog.getOpenFileName(
            self,
            "Open an image or a document",
            self._last_dir,
            f"Images and documents ({patterns} {papers});;Images ({patterns});;PDF, Illustrator, Photoshop ({papers});;All files (*)",
        )
        if path:
            self.open_path(path)

    def open_path(self, path: str) -> None:
        path = str(path)
        if documents.is_document(path):
            self.open_document(path)
        else:
            self._start_load(lambda: load_image(path), Path(path).name)

    # ==================================================================================
    # Documents with pages (PDF, Illustrator, images with several frames)
    # ==================================================================================

    def open_document(self, path: str) -> None:
        """Open a PDF or Illustrator file: page 1 appears first, the rest fill in behind it."""
        self.cancel_work()
        self._reset()
        if self.on_project():
            self.show_tab(TAB_IMAGE)  # an image opened for analysis: show where it goes
        name = Path(path).name
        self.message(f"Opening {name}…")
        self._run_task("document", lambda progress, cancelled: documents.open_document(path))

    def _document_opened(self, document: documents.Document) -> None:
        self._close_document()
        self.document = document
        self.page = 0
        self._summaries = [None] * document.page_count
        self._page_cache.clear()
        word = "Artboards" if document.kind == "Illustrator" else ("Frames" if document.page_names[0].startswith("Frame") else "Pages")
        self.page_strip.set_pages(document.page_names, word)
        self.whole_document_action.setEnabled(True)
        self._remember(document.path)
        self.show_page(0)
        signals = self._page_signals

        def work(progress, cancelled):
            documents.summarize(document, cancelled=cancelled, each=lambda page, summary: signals.ready.emit(document, page, summary))
            return document

        self._run_task("pages", work)

    def _close_document(self) -> None:
        if self.document is not None:
            self.document.close()
        self.document = None
        self.page = 0
        self._summaries = []
        self._page_cache.clear()
        self.page_strip.clear()
        self.whole_document_action.setEnabled(False)
        if self._document_dialog is not None:
            self._document_dialog.hide()

    @Slot(object, int, object)
    def _page_summarized(self, document, page: int, summary) -> None:
        if document is not self.document or not 0 <= page < len(self._summaries):
            return  # a page of a document that has been closed since
        self._summaries[page] = summary
        self.page_strip.set_summary(page, summary)
        if page == self.page:
            self._show_defined_colors()
        if self._document_dialog is not None and self._document_dialog.isVisible():
            self._document_dialog.set_document(document.name, document.page_count, self._summaries)

    def show_page(self, page: int) -> None:
        """Show one page of the open document in the Image tab."""
        document = self.document
        if document is None or not 0 <= page < document.page_count:
            return
        self.page = page
        self.page_strip.set_current(page)
        self.show_tab(TAB_IMAGE)
        cached = self._page_cache.get(page)
        if cached is not None:
            self.cancel_work(keep=("pages",))
            self._reset(keep_document=True)
            self._page_cache.move_to_end(page)
            self._loaded(*cached)
            return
        label = f"{document.name} · {document.page_names[page]}"

        def render() -> LoadedImage:
            return LoadedImage(document.render(page), label, document.path, document.page_count, page, False)

        self._start_load(render, document.page_names[page], keep_document=True)

    def step_page(self, by: int) -> None:
        if self.document is not None and self.document.page_count > 1 and self.pages.currentIndex() == TAB_IMAGE:
            self.show_page(max(0, min(self.document.page_count - 1, self.page + by)))

    def _show_defined_colors(self) -> None:
        """Mention the exact colors the file defines for the page being shown."""
        summary = self._summaries[self.page] if self.document is not None and self.page < len(self._summaries) else None
        if summary is None or not summary.defined or self.index is None:
            return
        base = self.source_panel.subtitle.text().split(" · exact colors")[0]
        shown = ", ".join(summary.defined[:5]) + (" …" if len(summary.defined) > 5 else "")
        self.source_panel.subtitle.setText(f"{base} · exact colors in the file: {shown}")

    def show_whole_document(self) -> None:
        """Open the window with one palette across every page."""
        if self.document is None:
            self.notify("Open a PDF, an Illustrator file or an image with several pages first.")
            return
        if self._document_dialog is None:
            dialog = DocumentDialog(self)
            dialog.addRequested.connect(lambda colors: self.studio.add_colors(colors))
            dialog.pageRequested.connect(self.show_page)
            dialog.notify.connect(self.notify)
            self._document_dialog = dialog
        self._document_dialog.set_document(self.document.name, self.document.page_count, self._summaries)
        self._document_dialog.show()
        self._document_dialog.raise_()

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

    def _start_load(
        self, loader: Callable[[], LoadedImage], description: str, sample: bool = False, keep_document: bool = False
    ) -> None:
        self.cancel_work(keep=("pages",) if keep_document else ())
        self._reset(keep_document=keep_document)
        if self.on_project():
            self.show_tab(TAB_IMAGE)  # an image opened for analysis: show where it goes
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

    def cancel_work(self, keep: tuple = ()) -> None:
        """Stop whatever is running in the background (except the kinds of work in `keep`)."""
        for task_id, task in self._tasks.items():
            if self._task_kinds.get(task_id) not in keep:
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
        if self._task_kinds.get(task_id) == "pages":
            return
        self.progress.setValue(round(fraction * 1000))
        if text and text != "Done":
            self.message(text)

    def _finish_task(self, task_id: int) -> Optional[str]:
        self._tasks.pop(task_id, None)
        kind = self._task_kinds.pop(task_id, None)
        if not self._tasks and not self.project.is_busy():
            self._show_busy(False)
        return kind

    @Slot(int, object)
    def _task_finished(self, task_id: int, result) -> None:
        kind = self._finish_task(task_id)
        if kind == "load":
            if task_id == self._load_task:
                self._loaded(*result)
        elif kind == "document":
            self._document_opened(result)
        elif kind == "pages":
            pass  # each page was already delivered as it became ready
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
        if kind == "pages":
            return  # thumbnails are a bonus; the page being shown reports its own problems
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

    def _reset(self, keep_document: bool = False) -> None:
        """Return to the empty state (what you see before any image is opened)."""
        if not keep_document:
            self._close_document()
        self.index = None
        self.loaded = None
        self.palette = None
        self.picks = []
        self.inspected = None
        self.highlighted = None
        self.stem = "colorroom"
        self.pick_button.setChecked(False)
        self.highlight_button.setChecked(False)
        self.image_view.set_image(None)
        self.studio.set_image(None)
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
            self.highlight_button,
        ):
            widget.setEnabled(ready)
        self.add_selected_button.setEnabled(ready and bool(self.table.selected_rows()))
        self.palette_panel.set_ready(ready)
        for item in (
            self.export_csv_action,
            self.pick_action,
            self.clear_picks_action,
            self.clear_filters_action,
            self.copy_palette_action,
            self.highlight_action,
            self.zoom_in_action,
            self.zoom_out_action,
            self.zoom_fit_action,
            self.add_selected_action,
            self.keep_palette_action,
            *self.export_actions,
        ):
            item.setEnabled(ready)

    def _loaded(self, loaded: LoadedImage, index: ColorIndex) -> None:
        self.loaded = loaded
        self.index = index
        document = self.document
        if document is not None:
            self._page_cache[loaded.frame] = (loaded, index)
            self._page_cache.move_to_end(loaded.frame)
            while len(self._page_cache) > _PAGE_CACHE:
                self._page_cache.popitem(last=False)
            base = re.sub(r"[^\w.-]+", "-", Path(document.name).stem)[:70].strip("-") or "colorroom"
            self.stem = f"{base}-page-{loaded.frame + 1}" if document.page_count > 1 else base
        else:
            cleaned = re.sub(r"[^\w.-]+", "-", Path(loaded.name).stem if loaded.path else loaded.name)[:80]
            self.stem = Path(SAMPLE_FILE_NAME).stem if self._load_is_sample else (cleaned.strip("-") or "colorroom")
        self.image_view.set_image(index.rgba)
        self.studio.set_image(index.rgba, self.stem)
        self.file_label.setText(loaded.name)
        summary = f"{index.width:,} × {index.height:,} · {index.visible_pixels:,} visible pixels analyzed"
        if loaded.color_managed:
            summary += " · color profile converted to sRGB"
        if document is not None and document.page_count > 1:
            summary += f" · {document.page_names[loaded.frame]} of {document.page_count}"
        elif loaded.frames > 1:
            summary += f" · frame {loaded.frame + 1} of {loaded.frames}"
        self.source_panel.subtitle.setText(summary)
        self.setWindowTitle(f"{loaded.name} · {APP_NAME}")
        self._set_ready(True)
        self._setup_detail()
        self.update_palette()
        self.update_inventory()
        if loaded.path:
            self._remember(loaded.path)
        if document is None and loaded.frames > 1 and loaded.path:
            self._adopt_frames(loaded, index)
        self._show_defined_colors()
        if self._load_is_sample:
            self.message("Sample loaded. Open your own image whenever you’re ready.")
        else:
            self.message("Image analyzed. Your palette and shades are ready.")

    def _adopt_frames(self, loaded: LoadedImage, index: ColorIndex) -> None:
        """An image with several frames or pages: show the page strip for it."""
        document = documents.FramesDocument(loaded.path, loaded.frames)
        self.document = document
        self.page = loaded.frame
        self._summaries = [None] * document.page_count
        self._page_cache.clear()
        self._page_cache[loaded.frame] = (loaded, index)
        self.page_strip.set_pages(document.page_names, "Frames" if document.page_names[0].startswith("Frame") else "Pages")
        self.page_strip.set_current(self.page)
        self.whole_document_action.setEnabled(True)
        signals = self._page_signals

        def work(progress, cancelled):
            documents.summarize(document, cancelled=cancelled, each=lambda page, summary: signals.ready.emit(document, page, summary))
            return document

        self._run_task("pages", work)

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
            if self.on_project():
                self.project.set_drop_active(True)
            else:
                self.image_view.set_drop_active(True)

    def dragLeaveEvent(self, event) -> None:
        self.image_view.set_drop_active(False)
        self.project.set_drop_active(False)

    def dropEvent(self, event) -> None:
        self.dragLeaveEvent(event)
        self.drop(event.mimeData())
        event.acceptProposedAction()

    def drop(self, mime) -> None:
        """A dropped file or picture: Your project opens it there, the other tabs on the Image tab."""
        path = self._dropped_file(mime)
        if path:
            if self.on_project():
                self.project.open_file(path)
            else:
                self.open_path(path)
        elif mime.hasImage():
            if self.on_project():
                image = mime.imageData()
                if image is not None and not image.isNull():
                    self.project.set_upload(qimage_to_rgba(image), "Dropped image")
            else:
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
        swatches = target.suffix.lower() == ".ase"
        how = "  To load it in Photoshop: Window > Swatches, then the panel menu > Import Swatches." if swatches else ""
        self.message(f"Palette saved: {target}{how}")
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
        self._sync_highlight()
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
    # Tabs, My palette, zoom, highlight and theme
    # ==================================================================================

    def show_tab(self, position: int) -> None:
        """TAB_PROJECT (0) Your project, TAB_IMAGE Image, TAB_PALETTE My palette, TAB_LIBRARY Library."""
        position = max(0, min(TAB_LIBRARY, int(position)))
        if self.tab_bar.currentIndex() != position:
            self.tab_bar.setCurrentIndex(position)
        self.pages.setCurrentIndex(position)
        if position != TAB_IMAGE:
            self.pick_button.setChecked(False)
        self._sync_header()

    def _restore_library(self) -> None:
        """Load the saved library; palettes saved as "presets" by version 2.1 are brought along."""
        library = Library()
        saved = str(self.settings.value("studio/library", "") or "")
        if saved:
            try:
                library = Library.from_json(saved)
            except ValueError:
                library = Library()
        old = str(self.settings.value("studio/presets", "") or "")
        if old and not saved:
            try:
                for palette in Project.from_json(old).palettes:
                    if palette.colors:
                        library.add(palette)
            except ValueError:
                pass
        self.studio.set_library(library)
        self.library_page.set_library(library)
        self._sync_project_sources()

    def _library_changed(self) -> None:
        """Keep both tabs in step and write the library to disk straight away."""
        self.studio._rebuild_presets_menu()
        if self.sender() is not self.library_page:
            self.library_page.refresh()
        count = len(self.studio.library.entries)
        self.tab_bar.setTabText(TAB_LIBRARY, f"Library ({count})" if count else "Library")
        self._sync_project_sources()
        if self.settings is not None:
            self.settings.setValue("studio/library", self.studio.library.to_json())
            self.settings.sync()

    def _open_from_library(self, entry) -> None:
        self.studio.open_entry(entry)
        self.show_tab(TAB_PALETTE)

    def toggle_theme(self) -> None:
        self.set_theme("light" if theme.MODE == "dark" else "dark")

    def _studio_changed(self) -> None:
        count = len(self.studio.colors())
        self.tab_bar.setTabText(TAB_PALETTE, f"My palette ({count})" if count else "My palette")
        self._sync_project_sources()

    def _selection_count_changed(self, count: int) -> None:
        self.add_selected_button.setEnabled(self.index is not None and count > 0)
        self.add_selected_button.setText(f"Add {count} selected to My palette" if count > 1 else "Add selected to My palette")

    def add_selected_to_studio(self) -> None:
        """Send the rows selected in the color table to the My palette tab."""
        if self.index is None:
            return
        colors = self.table.selected_colors()
        if not colors and self.inspected is not None:
            colors = [self.inspected.color]
        if not colors:
            self.notify("Select one or more rows in the color table first.")
            return
        self.studio.add_colors(colors)

    def add_palette_to_studio(self) -> None:
        """Send the current design palette to the My palette tab."""
        if self.palette is not None and self.palette.entries:
            self.studio.add_colors(self.palette.colors)

    def set_highlighting(self, on: bool) -> None:
        if self.highlight_button.isChecked() != on:
            self.highlight_button.setChecked(on)
        self.highlight_action.setChecked(on)
        if on and self.inspected is None and self.index is not None:
            self.inspect(int(self.index.frequency_order[0]))  # something to highlight
        self._sync_highlight()
        if on and self.inspected is not None:
            self.message(f"Showing only the pixels of {self.inspected.color}. Click another color to move the highlight.")
        elif not on and self.index is not None:
            self.message("Highlight off.")

    def _sync_highlight(self) -> None:
        wanted = self.inspected.color if (self.highlight_button.isChecked() and self.inspected is not None) else None
        self.image_view.set_highlight(wanted)

    def _zoom_changed(self, zoom: float) -> None:
        zoomed = zoom > 1.001
        self.fit_button.setEnabled(zoomed)
        if zoomed:
            self.message(f"Zoom {zoom * 100:.0f}%. Drag to move around, scroll to zoom, double-click or press Fit to see the whole image.")
        elif self.index is not None:
            self.message("Showing the whole image.")

    def set_theme(self, mode: str) -> None:
        """Switch between the light and the dark look."""
        app = QApplication.instance()
        if app is None or mode not in theme.MODES:
            return
        theme.apply_theme(app, mode)
        for key, item in self.theme_actions.items():
            item.setChecked(key == mode)
        self.theme_button.setText("Light mode" if mode == "dark" else "Dark mode")

    # ==================================================================================
    # Misc
    # ==================================================================================

    def show_about(self) -> None:
        QMessageBox.about(
            self,
            f"About {APP_NAME}",
            f"<h3>{APP_NAME} {__version__}</h3>"
            "<p>Recolor your own design on the Your project tab and export the same file type back. "
            "Find the colors in any image: every distinct HEX shade, where it appears, "
            "its related shades, and palettes built from them. Collect colors on the My palette tab to check "
            "contrast and color vision, build tints and shades, and send them to your design apps.</p>"
            "<p>Everything runs on this computer. Images are never uploaded.</p>"
            "<p>HEX values are sRGB; embedded color profiles are converted to sRGB. "
            "Fully transparent pixels are skipped. For animated images the first frame is read.</p>",
        )

    def closeEvent(self, event) -> None:
        self._save_settings()
        self.cancel_work()
        self.project.cancel_work()
        self.pool.waitForDone(3000)
        self._close_document()
        self.project.close_document()
        super().closeEvent(event)
