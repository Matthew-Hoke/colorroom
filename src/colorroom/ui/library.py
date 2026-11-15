"""The "Library" tab: palettes saved to come back to, organised in folders.

Left: folders and the palettes in them (drag a palette onto a folder to move it), with a
search box and a favorites filter. Right: the selected palette, its HEX codes one click
away, and what you can do with it.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Optional

from PySide6.QtCore import QSize, Qt, Signal
from PySide6.QtGui import QFont, QGuiApplication
from PySide6.QtWidgets import (
    QAbstractItemView,
    QHBoxLayout,
    QInputDialog,
    QLineEdit,
    QMenu,
    QPushButton,
    QScrollArea,
    QSplitter,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from ..core.designer import Library, LibraryEntry
from ..core.exporters import palette_text
from . import theme
from .studio import strip_icon
from .widgets import FlowArea, Panel, SwatchGrid, small_label

UNFILED = "Not in a folder"
_ROLE = Qt.ItemDataRole.UserRole


class _Tree(QTreeWidget):
    """Folders with palettes inside. Dropping palettes on a folder moves them there."""

    moved = Signal(list, str)  # entries, target folder ("" = not in a folder)

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.setHeaderHidden(True)
        self.setIconSize(QSize(96, 18))
        self.setIndentation(18)
        self.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.setDragDropMode(QAbstractItemView.DragDropMode.InternalMove)
        self.setDefaultDropAction(Qt.DropAction.MoveAction)
        self.setVerticalScrollMode(QAbstractItemView.ScrollMode.ScrollPerPixel)
        self.setExpandsOnDoubleClick(False)
        self.setMinimumHeight(180)

    def selected_entries(self) -> list:
        return [item.data(0, _ROLE)[1] for item in self.selectedItems() if item.data(0, _ROLE)[0] == "entry"]

    def dropEvent(self, event) -> None:
        target = self.itemAt(event.position().toPoint())
        entries = self.selected_entries()
        event.ignore()  # the tree is rebuilt from the library instead of letting Qt move items
        if target is None or not entries:
            return
        kind, value = target.data(0, _ROLE)
        folder = value if kind == "folder" else value.folder
        self.moved.emit(entries, folder)


class LibraryPage(QWidget):
    notify = Signal(str)
    changed = Signal()  # something in the library changed and should be saved
    openRequested = Signal(object)  # a LibraryEntry to open on the My palette tab

    def __init__(self, library: Library, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.library = library
        self._entry: Optional[LibraryEntry] = None
        self._folder: Optional[str] = None
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

        self.panel = Panel("Library", "Palettes you saved. Drag one onto a folder to file it.")
        self.new_folder_button = QPushButton("New folder")
        self.panel.header.addWidget(self.new_folder_button, 0, Qt.AlignmentFlag.AlignTop)
        row = QHBoxLayout()
        row.setSpacing(8)
        self.search = QLineEdit()
        self.search.setPlaceholderText("Search names, folders or HEX codes")
        self.search.setClearButtonEnabled(True)
        self.favorites_button = QPushButton("★ Favorites")
        self.favorites_button.setCheckable(True)
        self.favorites_button.setToolTip("Show only the palettes you marked as favorites")
        row.addWidget(self.search, 1)
        row.addWidget(self.favorites_button)
        self.panel.body.addLayout(row)
        self.tree = _Tree()
        self.panel.body.addWidget(self.tree, 1)
        self.count_label = small_label()
        self.panel.body.addWidget(self.count_label)
        self.splitter.addWidget(self.panel)

        self.detail = Panel("Nothing selected", "")
        content = QWidget()
        column = QVBoxLayout(content)
        column.setContentsMargins(0, 0, 4, 0)
        column.setSpacing(10)
        self.grid = SwatchGrid()
        self.grid.MAX_COLUMNS = 8
        column.addWidget(self.grid)
        self.codes = small_label()
        self.codes.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self.codes.setFont(theme.mono_font(9.5))
        column.addWidget(self.codes)
        column.addStretch(1)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        scroll.setWidget(content)
        scroll.viewport().setAutoFillBackground(False)
        content.setAutoFillBackground(False)
        self.detail.body.addWidget(scroll, 1)

        actions = FlowArea(6)
        self.open_button = QPushButton("Open in My palette")
        self.open_button.setProperty("primary", True)
        self.open_button.setToolTip("Open a copy on the My palette tab to check, preview and export it")
        self.copy_button = QPushButton("Copy HEX codes")
        self.favorite_button = QPushButton("★ Favorite")
        self.favorite_button.setCheckable(True)
        self.rename_button = QPushButton("Rename…")
        self.move_button = QPushButton("Move to")
        self.move_menu = QMenu(self.move_button)
        self.move_button.setMenu(self.move_menu)
        self.duplicate_button = QPushButton("Duplicate")
        self.delete_button = QPushButton("Delete")
        for button in (
            self.open_button,
            self.copy_button,
            self.favorite_button,
            self.rename_button,
            self.move_button,
            self.duplicate_button,
            self.delete_button,
        ):
            actions.add(button)
        self.detail.body.addWidget(actions)
        self.splitter.addWidget(self.detail)
        self.panel.setMinimumWidth(340)
        self.detail.setMinimumWidth(480)
        self.splitter.setSizes([440, 960])
        self.splitter.setStretchFactor(0, 0)
        self.splitter.setStretchFactor(1, 1)

        self.new_folder_button.clicked.connect(self.new_folder_dialog)
        self.search.textChanged.connect(lambda _text: self.refresh())
        self.favorites_button.toggled.connect(lambda _on: self.refresh())
        self.tree.itemSelectionChanged.connect(self._selection_changed)
        self.tree.itemDoubleClicked.connect(self._double_clicked)
        self.tree.moved.connect(self.move_entries)
        self.grid.swatchClicked.connect(lambda swatch: self._copy(swatch.color, f"{swatch.color} copied"))
        self.open_button.clicked.connect(self.open_selected)
        self.copy_button.clicked.connect(self.copy_selected)
        self.favorite_button.clicked.connect(self.toggle_favorite)
        self.rename_button.clicked.connect(self.rename_dialog)
        self.duplicate_button.clicked.connect(self.duplicate_selected)
        self.delete_button.clicked.connect(self.delete_selected)

    # ----------------------------------------------------------------------------------
    # Showing the library
    # ----------------------------------------------------------------------------------

    def set_library(self, library: Library) -> None:
        self.library = library
        self._entry = self._folder = None
        self.refresh()

    def shown_entries(self) -> list:
        """The palettes that pass the search box and the favorites filter."""
        entries = self.library.search(self.search.text())
        if self.favorites_button.isChecked():
            entries = [entry for entry in entries if entry.favorite]
        return entries

    def refresh(self) -> None:
        """Rebuild the tree from the library, keeping the selection if it still exists."""
        shown = self.shown_entries()
        filtering = bool(self.search.text().strip()) or self.favorites_button.isChecked()
        keep_entry, keep_folder = self._entry, self._folder
        self.tree.blockSignals(True)
        self.tree.clear()
        bold = QFont(self.font())
        bold.setWeight(QFont.Weight.DemiBold)
        groups = [(name, name) for name in self.library.folders]
        loose = [entry for entry in shown if entry.folder == ""]
        if loose or not self.library.folders:
            groups.append((UNFILED, ""))
        selected_item = None
        for label, folder in groups:
            members = [entry for entry in shown if entry.folder == folder]
            if filtering and not members:
                continue
            top = QTreeWidgetItem([f"{label}   ({len(members)})"])
            top.setData(0, _ROLE, ("folder", folder))
            top.setFont(0, bold)
            top.setFlags((top.flags() | Qt.ItemFlag.ItemIsDropEnabled) & ~Qt.ItemFlag.ItemIsDragEnabled)
            self.tree.addTopLevelItem(top)
            for entry in members:
                star = "★ " if entry.favorite else ""
                child = QTreeWidgetItem([f"{star}{entry.palette.name}   ({len(entry.palette.colors)})"])
                child.setIcon(0, strip_icon(entry.palette.hexes()))
                child.setData(0, _ROLE, ("entry", entry))
                child.setToolTip(0, palette_text(entry.palette.hexes()))
                child.setFlags(child.flags() | Qt.ItemFlag.ItemIsDragEnabled)
                top.addChild(child)
                if entry is keep_entry:
                    selected_item = child
            top.setExpanded(True)
            if keep_entry is None and keep_folder is not None and folder == keep_folder and folder != "":
                selected_item = top
        self.tree.blockSignals(False)
        if selected_item is not None:
            self.tree.setCurrentItem(selected_item)
        total, folders = len(self.library.entries), len(self.library.folders)
        if not total and not folders:
            self.count_label.setText("Nothing saved yet. On the My palette tab choose Library > Save this palette to the library.")
        elif filtering:
            self.count_label.setText(f"{len(shown)} of {total} palettes shown")
        else:
            self.count_label.setText(
                f"{total} {'palette' if total == 1 else 'palettes'} in {folders} {'folder' if folders == 1 else 'folders'}"
            )
        self._selection_changed()

    def _selection_changed(self) -> None:
        items = self.tree.selectedItems()
        self._entry = self._folder = None
        if len(items) == 1:
            kind, value = items[0].data(0, _ROLE)
            if kind == "entry":
                self._entry = value
            elif value != "":
                self._folder = value
        self._show_detail()

    def _show_detail(self) -> None:
        entry, folder, several = self._entry, self._folder, self.tree.selected_entries()
        palette_buttons = (self.open_button, self.copy_button, self.favorite_button, self.duplicate_button)
        if entry is not None:
            palette = entry.palette
            self.detail.title.setText(palette.name)
            where = entry.folder or UNFILED
            self.detail.subtitle.setText(f"{len(palette.colors)} colors · {where} · click a swatch to copy its HEX code")
            self.grid.set_empty_text("")
            self.grid.set_entries(
                [SimpleNamespace(color=c.hex, role=c.name, exact=True, source_color=c.hex) for c in palette.colors]
            )
            self.codes.setText(palette_text(palette.hexes()))
            self.favorite_button.setChecked(entry.favorite)
            self.favorite_button.setText("★ Favorite" if entry.favorite else "☆ Favorite")
        else:
            self.grid.set_entries(())
            self.codes.setText("")
            self.favorite_button.setChecked(False)
            self.favorite_button.setText("☆ Favorite")
            if folder is not None:
                held = len(self.library.in_folder(folder))
                self.detail.title.setText(folder)
                self.detail.subtitle.setText(f"A folder with {held} {'palette' if held == 1 else 'palettes'}.")
                self.grid.set_empty_text("Select a palette in this folder to see its colors.")
            elif len(several) > 1:
                self.detail.title.setText(f"{len(several)} palettes selected")
                self.detail.subtitle.setText("Move them to a folder, or delete them.")
                self.grid.set_empty_text("")
            else:
                self.detail.title.setText("Nothing selected")
                self.detail.subtitle.setText("")
                self.grid.set_empty_text("Select a palette on the left to see its colors and HEX codes.")
        for button in palette_buttons:
            button.setEnabled(entry is not None)
        self.rename_button.setEnabled(entry is not None or folder is not None)
        self.rename_button.setText("Rename folder…" if folder is not None else "Rename…")
        self.move_button.setEnabled(bool(several))
        self.delete_button.setEnabled(bool(several) or folder is not None)
        self.delete_button.setText("Delete folder" if folder is not None else "Delete")
        self.move_menu.clear()
        for name in [*self.library.folders, ""]:
            action = self.move_menu.addAction(name or UNFILED)
            action.triggered.connect(lambda _=False, target=name: self.move_entries(self.tree.selected_entries(), target))
        self.move_menu.addSeparator()
        self.move_menu.addAction("New folder…").triggered.connect(self._move_to_new_folder)

    def select_entry(self, entry: Optional[LibraryEntry]) -> None:
        """Select one palette in the tree (used after saving, and by tests)."""
        self._entry, self._folder = entry, None
        self.refresh()

    def select_folder(self, folder: str) -> None:
        self._entry, self._folder = None, folder
        self.refresh()

    def _double_clicked(self, item, _column) -> None:
        if item.data(0, _ROLE)[0] == "entry":
            self.open_selected()

    # ----------------------------------------------------------------------------------
    # Actions
    # ----------------------------------------------------------------------------------

    def _copy(self, text: str, confirmation: str) -> None:
        QGuiApplication.clipboard().setText(text)
        self.notify.emit(confirmation)

    def _saved(self) -> None:
        self.refresh()
        self.changed.emit()

    def open_selected(self) -> None:
        if self._entry is not None:
            self.openRequested.emit(self._entry)

    def copy_selected(self) -> None:
        if self._entry is not None:
            self._copy(palette_text(self._entry.palette.hexes()), f"HEX codes of {self._entry.palette.name} copied")

    def toggle_favorite(self) -> None:
        if self._entry is not None:
            self._entry.favorite = not self._entry.favorite
            self.notify.emit("Added to favorites" if self._entry.favorite else "Removed from favorites")
            self._saved()

    def new_folder(self, name: str = "New folder") -> str:
        created = self.library.add_folder(name)
        self._entry, self._folder = None, created
        self._saved()
        return created

    def new_folder_dialog(self) -> None:
        name, accepted = QInputDialog.getText(self, "New folder", "Folder name (for example a client, a project or a style):")
        if accepted and name.strip():
            self.new_folder(name)

    def _move_to_new_folder(self) -> None:
        entries = self.tree.selected_entries()
        name, accepted = QInputDialog.getText(self, "New folder", "Folder name:")
        if accepted and name.strip():
            created = self.library.add_folder(name)
            self.move_entries(entries, created)

    def move_entries(self, entries, folder: str) -> None:
        """Move palettes into a folder ("" takes them out of any folder)."""
        entries = [entry for entry in entries if entry in self.library.entries]
        if not entries:
            return
        for entry in entries:
            self.library.move(entry, folder)
        self._entry = entries[0] if len(entries) == 1 else None
        self._folder = None
        count = len(entries)
        self.notify.emit(f"{count} {'palette' if count == 1 else 'palettes'} moved to {folder or UNFILED}")
        self._saved()

    def rename(self, name: str) -> None:
        if self._entry is not None:
            self.library.rename(self._entry, name)
        elif self._folder is not None:
            self._folder = self.library.rename_folder(self._folder, name)
        else:
            return
        self._saved()

    def rename_dialog(self) -> None:
        current = self._entry.palette.name if self._entry is not None else (self._folder or "")
        name, accepted = QInputDialog.getText(self, "Rename", "New name:", text=current)
        if accepted and name.strip():
            self.rename(name)

    def duplicate_selected(self) -> None:
        if self._entry is not None:
            copy = self.library.add(self._entry.palette, self._entry.folder, f"{self._entry.palette.name} copy")
            self._entry = copy
            self._saved()

    def delete_selected(self) -> None:
        """Delete the selected palettes, or the selected folder (its palettes move out of it)."""
        entries = self.tree.selected_entries()
        if entries:
            for entry in entries:
                self.library.remove(entry)
            count = len(entries)
            self.notify.emit(f"{count} {'palette' if count == 1 else 'palettes'} deleted from the library")
        elif self._folder is not None:
            held = self.library.delete_folder(self._folder)
            note = f" Its {held} {'palette is' if held == 1 else 'palettes are'} now under {UNFILED}." if held else ""
            self.notify.emit(f"Folder deleted.{note}")
        else:
            return
        self._entry = self._folder = None
        self._saved()
