"""Fusion-style desktop controls backed by the Solid Studio feature model."""

from copy import deepcopy
import math
from pathlib import Path
import sys
from uuid import uuid4

from . import model

from PySide6.QtCore import QSize, Qt, QTimer
from PySide6.QtGui import QAction, QKeySequence
from PySide6.QtWidgets import (
    QApplication, QCheckBox, QComboBox, QDialog, QDialogButtonBox, QDoubleSpinBox,
    QFileDialog, QFormLayout, QFrame, QHBoxLayout, QInputDialog, QLabel, QListWidget,
    QListWidgetItem, QMainWindow, QMessageBox, QMenu, QPushButton,
    QSplitter, QToolButton, QTreeWidget, QTreeWidgetItem, QVBoxLayout, QWidget,
)

from .icons import icon
from .viewport import Viewport


STYLE = """
QMainWindow, QWidget { color: #263645; font-size: 12px; }
QMainWindow, QDialog { background: #f8fafc; }
QMenuBar { background: #fff; padding: 3px 8px; border-bottom: 1px solid #dce2e8; }
QMenu { background: #fff; border: 1px solid #cbd5df; padding: 4px; }
QMenu::item { padding: 5px 25px 5px 8px; }
QMenu::item:selected { background: #e5f1fa; }
QFrame#strip { background: #f8fafc; border-bottom: 1px solid #d4dde5; }
QFrame#browser, QFrame#palette { background: #fcfdff; border: 1px solid #d7dfe7; }
QFrame#timeline { background: #f4f7fa; border-top: 1px solid #ccd6df; }
QFrame#navigation { background: #edf2f6; border-top: 1px solid #d7e0e7; }
QLabel#section { color: #4b5d6d; font-weight: 600; font-size: 11px; }
QLabel#workspace { font-weight: 700; color: #2c546d; background: #e7eff6; padding: 7px 13px; }
QLabel#activeTab { font-weight: 700; color: #30617e; border-bottom: 2px solid #4a8bb5; padding: 6px 12px; }
QLabel#document { background: #fff; padding: 5px 14px; border: 1px solid #d7dfe7; }
QToolButton { background: transparent; border: 1px solid transparent; padding: 3px; }
QToolButton:hover, QPushButton:hover { background: #e3eff8; border-color: #abc8dd; }
QToolButton:checked { background: #d7e9f5; border-color: #9bbcd2; }
QToolButton:disabled { color: #8d9ba7; }
QPushButton { background: #f3f7fa; border: 1px solid #bfcdd8; border-radius: 3px; padding: 5px 12px; }
QPushButton#primary { background: #317dab; color: white; border-color: #317dab; font-weight: 600; }
QTreeWidget, QListWidget { background: transparent; border: 0; outline: 0; }
QTreeWidget::item { padding: 4px; }
QTreeWidget::item:selected, QListWidget::item:selected { color: #23465d; background: #dcecf8; }
QListWidget::item { padding: 4px 8px; border: 1px solid #d6e0e8; border-radius: 3px; margin: 2px; }
QStatusBar { background: #fff; border-top: 1px solid #dce2e8; }
QComboBox, QDoubleSpinBox { background: white; border: 1px solid #c1ced9; border-radius: 3px; padding: 4px; min-width: 136px; }
"""


def frame(name, layout_type, margins=(8, 5, 8, 5), spacing=4):
    widget = QFrame()
    widget.setObjectName(name)
    layout = layout_type(widget)
    layout.setContentsMargins(*margins)
    layout.setSpacing(spacing)
    return widget, layout


def label(text, name="section"):
    widget = QLabel(text)
    widget.setObjectName(name)
    return widget


class Studio(QMainWindow):
    def __init__(self):
        super().__init__()
        self.resize(1280, 820)
        self.setMinimumSize(840, 560)
        self.setWindowIcon(icon("Bodies"))
        self.features = []
        self.state = model.evaluate([])
        self.cursor = 0
        self.selected = ""
        self.path = None
        self.saved = []
        self.undo_stack = []
        self.redo_stack = []
        self.draft = None
        self.draft_index = None
        self.picking_sketch_plane = False
        self.sketch_history = []
        self.sketch_redo = []
        self.commands = {}
        self.build()
        self.refresh()

    def action(self, menu, name, callback, shortcut=None):
        item = QAction(icon(name), name, self)
        item.triggered.connect(callback)
        if shortcut:
            item.setShortcut(QKeySequence(shortcut))
        menu.addAction(item)
        return item

    def tool(self, name, callback, *, glyph=None, large=False, checkable=False):
        button = QToolButton()
        button.setText(name)
        button.setIcon(icon(glyph or name))
        button.setIconSize(QSize(28, 28) if large else QSize(19, 19))
        button.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextUnderIcon if large else Qt.ToolButtonStyle.ToolButtonIconOnly)
        button.setToolTip(name)
        button.setAccessibleName(name)
        button.setCheckable(checkable)
        button.clicked.connect(callback)
        return button

    def group(self, parent, name, items):
        container, group_layout = frame("", QVBoxLayout, (4, 1, 4, 0), 1)
        row = QHBoxLayout()
        for title, command, glyph in items:
            button = self.tool(title, command, glyph=glyph, large=True)
            row.addWidget(button)
            self.commands[title] = button
        group_layout.addLayout(row)
        group_title = label(name)
        group_title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        group_layout.addWidget(group_title)
        parent.addWidget(container)
        divider = QFrame()
        divider.setFrameShape(QFrame.Shape.VLine)
        divider.setStyleSheet("color: #d4dde5;")
        parent.addWidget(divider)

    def build(self):
        menu = self.menuBar().addMenu("&File")
        self.action(menu, "New", self.new, "Ctrl+N")
        self.action(menu, "Open", self.open, "Ctrl+O")
        self.action(menu, "Save", self.save, "Ctrl+S")
        self.action(menu, "Save As…", lambda: self.save(True), "Ctrl+Shift+S")
        menu.addSeparator()
        self.action(menu, "Export STEP", self.export)
        edit = self.menuBar().addMenu("&Edit")
        self.undo_action = self.action(edit, "Undo", self.undo, "Ctrl+Z")
        self.redo_action = self.action(edit, "Redo", self.redo, "Ctrl+Shift+Z")
        self.action(edit, "Change Parameters…", self.change_parameters)
        design = self.menuBar().addMenu("&Design")
        self.action(design, "Create Sketch", self.create_sketch)
        self.action(design, "Extrude", lambda: self.feature_dialog("Extrude"), "E")
        for kind in ("Fillet", "Chamfer", "Combine"):
            self.action(design, kind, lambda checked=False, k=kind: self.feature_dialog(k), "F" if kind == "Fillet" else None)
        self.action(design, "Compute All", self.refresh, "Ctrl+B")
        view = self.menuBar().addMenu("&View")
        self.action(view, "Fit", lambda: self.viewport.fit(), "Home")
        for name in ("Top", "Front", "Right", "Home"):
            self.action(view, name, lambda checked=False, n=name: self.viewport.set_view(n.upper()))
        self.action(view, "Sketch / Layout Grid", lambda: self.toggle_grid(not self.viewport.grid))
        self.action(view, "Show / Hide Browser", lambda: self.browser.setVisible(self.browser.isHidden()), "Ctrl+Alt+B")
        self.action(view, "Show / Hide ViewCube", self.toggle_cube, "Ctrl+Alt+V")
        self.action(view, "Show / Hide Navigation Bar",
                    lambda: self.navigation_bar.setVisible(self.navigation_bar.isHidden()), "Ctrl+Alt+N")
        self.action(view, "Toggle Visibility", self.toggle_visibility, "V")
        self.action(design, "Model Toolbox", self.toolbox, "S")
        self.action(self.menuBar().addMenu("&Help"), "Controls", self.help, "F1")

        root, layout = frame("", QVBoxLayout, (0, 0, 0, 0), 0)
        self.setCentralWidget(root)
        quick, row = frame("strip", QHBoxLayout)
        brand = QLabel("  SOLID STUDIO  ")
        brand.setStyleSheet("font-weight: 800; color: #2b607f;")
        row.addWidget(brand)
        for name, callback in (("New", self.new), ("Open", self.open), ("Save", self.save),
                               ("Undo", self.undo), ("Redo", self.redo)):
            row.addWidget(self.tool(name, callback))
        row.addSpacing(16)
        self.document_label = label("Untitled", "document")
        row.addWidget(self.document_label)
        row.addStretch()
        layout.addWidget(quick)

        tabs, tab_row = frame("strip", QHBoxLayout, (8, 1, 8, 0), 8)
        tab_row.addWidget(label("DESIGN", "workspace"))
        self.tab = label("SOLID", "activeTab")
        tab_row.addWidget(self.tab)
        tab_row.addStretch()
        layout.addWidget(tabs)

        self.solid_strip, row = frame("strip", QHBoxLayout, (8, 5, 8, 4))
        self.group(row, "CREATE", [("Create Sketch", self.create_sketch, "Sketch"),
                                  ("Extrude", lambda: self.feature_dialog("Extrude"), "Extrude")])
        self.group(row, "MODIFY", [(k, lambda checked=False, kind=k: self.feature_dialog(kind), k)
                                  for k in ("Fillet", "Chamfer", "Combine")])
        self.group(row, "INSPECT", [("Properties", self.properties, "View")])
        self.group(row, "INSERT / EXPORT", [("Export STEP", self.export, "Export STEP")])
        self.group(row, "SELECT", [("Select", lambda: self.select(""), "Select")])
        row.addStretch()
        layout.addWidget(self.solid_strip)

        self.sketch_strip, row = frame("strip", QHBoxLayout, (8, 5, 8, 4))
        self.group(row, "CREATE", [(k, lambda checked=False, kind=k: self.viewport.choose_tool(kind), k)
                                  for k in ("Line", "Rectangle", "Circle", "Arc")])
        self.group(row, "MODIFY", [("Offset", self.offset_profile, "Sketch"),
                                   ("Trim", lambda: self.viewport.choose_tool("Trim"), "Trim"),
                                   ("Delete", lambda: self.viewport.remove_profile(), "Undo")])
        self.group(row, "CONSTRAIN", [("Dimension", self.dimensions, "Sketch"),
                                      ("Horizontal / Vertical", self.line_rule, "Line")])
        self.group(row, "PROJECT", [("Project", self.project_geometry, "Project")])
        self.group(row, "SELECT", [("Sketch Select", lambda: self.viewport.choose_tool("Select"), "Select")])
        row.addStretch()
        self.group(row, "SKETCH", [("Finish Sketch", self.finish_sketch, "Finish Sketch")])
        layout.addWidget(self.sketch_strip)
        self.sketch_strip.hide()

        split = QSplitter(Qt.Orientation.Horizontal)
        browser, left = frame("browser", QVBoxLayout, (8, 9, 8, 8))
        self.browser = browser
        left.addWidget(label("BROWSER"))
        self.tree = QTreeWidget()
        self.tree.setHeaderHidden(True)
        self.tree.setIndentation(15)
        self.tree.itemClicked.connect(lambda item, column: self.select(item.data(0, Qt.ItemDataRole.UserRole) or ""))
        self.tree.itemClicked.connect(self.origin_plane_clicked)
        self.tree.itemDoubleClicked.connect(lambda item, column: self.edit_feature(item.data(0, Qt.ItemDataRole.UserRole)))
        left.addWidget(self.tree)
        browser.setMinimumWidth(160)
        split.addWidget(browser)

        canvas, center = frame("", QVBoxLayout, (0, 0, 0, 0), 0)
        self.viewport = Viewport()
        self.viewport.body_selected.connect(self.select)
        self.viewport.face_selected.connect(self.use_sketch_face)
        self.viewport.sketch_notice.connect(lambda text: self.statusBar().showMessage(text))
        self.viewport.coordinates.connect(lambda value: self.coords.setText(value))
        self.viewport.sketch_changed.connect(self.update_palette)
        center.addWidget(self.viewport, 1)
        navigation, nav = frame("navigation", QHBoxLayout, (8, 2, 8, 2))
        self.navigation_bar = navigation
        nav.addStretch()
        self.nav_buttons = {}
        for name in ("Orbit", "Pan", "Zoom"):
            button = self.tool(name, lambda checked, n=name: self.navigation(n, checked), checkable=True)
            self.nav_buttons[name] = button
            nav.addWidget(button)
        nav.addWidget(self.tool("Fit", self.viewport.fit))
        grid = QCheckBox("Layout Grid")
        grid.setChecked(True)
        grid.toggled.connect(self.toggle_grid)
        self.grid_checkbox = grid
        nav.addWidget(grid)
        nav.addStretch()
        center.addWidget(navigation)
        split.addWidget(canvas)
        split.setStretchFactor(1, 1)
        split.setSizes([215, 1030])
        layout.addWidget(split, 1)

        self.plane_picker, plane_row = frame("palette", QHBoxLayout, (12, 5, 12, 5))
        plane_row.addWidget(label("SELECT A PLANE OR PLANAR FACE"))
        for plane in model.PLANES:
            button = QPushButton(plane)
            button.clicked.connect(lambda checked=False, p=plane: self.start_on_plane(p))
            plane_row.addWidget(button)
        plane_row.addStretch()
        cancel_plane = QPushButton("Cancel")
        cancel_plane.clicked.connect(self.stop_plane_picker)
        plane_row.addWidget(cancel_plane)
        layout.addWidget(self.plane_picker)
        self.plane_picker.hide()

        self.palette, palette = frame("palette", QHBoxLayout, (12, 5, 12, 5))
        palette.addWidget(label("SKETCH PALETTE"))
        self.sketch_status = QLabel()
        palette.addWidget(self.sketch_status, 1)
        snap = QCheckBox("Snap")
        snap.setChecked(True)
        snap.toggled.connect(lambda value: setattr(self.viewport, "snap", value))
        palette.addWidget(snap)
        construction = QPushButton("Construction")
        construction.setToolTip("Toggle selected geometry (X)")
        construction.clicked.connect(self.toggle_construction)
        palette.addWidget(construction)
        cancel = QPushButton("Cancel Sketch")
        cancel.clicked.connect(self.cancel_sketch)
        palette.addWidget(cancel)
        finish = QPushButton("Finish Sketch")
        finish.setObjectName("primary")
        finish.clicked.connect(self.finish_sketch)
        palette.addWidget(finish)
        layout.addWidget(self.palette)
        self.palette.hide()

        timeline, row = frame("timeline", QHBoxLayout, (10, 4, 10, 4))
        row.addWidget(label("TIMELINE"))
        for text, hint, action in [("|‹", "Roll to Start", lambda: self.roll(0)),
                                   ("‹", "Previous Feature", lambda: self.roll(self.cursor - 1)),
                                   ("›", "Next Feature", lambda: self.roll(self.cursor + 1)),
                                   ("›|", "Roll to End", lambda: self.roll(len(self.features)))]:
            button = QPushButton(text)
            button.setToolTip(hint)
            button.setAccessibleName(hint)
            button.clicked.connect(action)
            row.addWidget(button)
        self.timeline = QListWidget()
        self.timeline.setFlow(QListWidget.Flow.LeftToRight)
        self.timeline.setWrapping(False)
        self.timeline.setFixedHeight(45)
        self.timeline.setIconSize(QSize(19, 19))
        self.timeline.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.timeline.itemClicked.connect(lambda item: self.select(item.data(Qt.ItemDataRole.UserRole)))
        self.timeline.itemDoubleClicked.connect(lambda item: self.edit_feature(item.data(Qt.ItemDataRole.UserRole)))
        self.timeline.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.timeline.customContextMenuRequested.connect(self.timeline_menu)
        row.addWidget(self.timeline, 1)
        self.timeline_count = QLabel()
        row.addWidget(self.timeline_count)
        layout.addWidget(timeline)
        self.coords = QLabel("mm")
        self.statusBar().addPermanentWidget(self.coords)

        escape = QAction(self)
        escape.setShortcut(QKeySequence("Esc"))
        escape.triggered.connect(self.escape)
        self.addAction(escape)
        for shortcut, tool in (("L", "Line"), ("R", "Rectangle"), ("C", "Circle")):
            key = QAction(self)
            key.setShortcut(QKeySequence(shortcut))
            key.triggered.connect(lambda checked=False, t=tool: self.viewport.choose_tool(t) if self.draft else None)
            self.addAction(key)
        self.action(design, "Sketch Dimension", lambda: self.dimensions() if self.draft else None, "D")
        self.action(design, "Normal / Construction", self.toggle_construction, "X")
        self.action(design, "Sketch Offset", self.offset_profile, "O")
        self.action(design, "Sketch Trim", lambda: self.viewport.choose_tool("Trim") if self.draft else None, "T")
        self.action(design, "Project Geometry", self.project_geometry, "P")
        self.action(design, "Delete Sketch Geometry",
                    lambda: self.viewport.remove_profile() if self.draft else None, "Delete")

    def toggle_grid(self, checked):
        self.viewport.grid = checked
        self.grid_checkbox.setChecked(checked)
        self.viewport.update()

    def toggle_cube(self):
        self.viewport.show_cube = not self.viewport.show_cube
        self.viewport.update()

    def toggle_visibility(self):
        if self.draft or self.selected not in self.state.bodies:
            return
        self.viewport.hidden.symmetric_difference_update({self.selected})
        self.refresh()

    def toolbox(self):
        names = ("Line", "Rectangle", "Circle", "Arc", "Offset", "Trim", "Dimension", "Project",
                 "Horizontal / Vertical", "Finish Sketch") if self.draft else (
            "Create Sketch", "Extrude", "Fillet", "Chamfer", "Combine", "Properties", "Export STEP")
        names = [name for name in names if self.commands[name].isEnabled()]
        if names:
            name, ok = QInputDialog.getItem(self, "Model Toolbox", "Command", names, editable=False)
            if ok:
                self.commands[name].click()

    def navigation(self, name, checked):
        for key, button in self.nav_buttons.items():
            button.setChecked(checked and name == key)
        self.viewport.navigation = name if checked else ""

    def escape(self):
        if self.picking_sketch_plane:
            self.stop_plane_picker()
        elif self.draft and self.viewport.points:
            self.viewport.finish_chain()
        elif self.draft:
            self.viewport.choose_tool("Select")
        else:
            self.navigation("", False)
            self.select("")

    def refresh(self):
        self.state = model.evaluate(self.features, self.cursor)
        self.viewport.set_model(self.state)
        self.tree.clear()
        names = {f["id"]: f["name"] for f in self.features}
        title = self.path.stem if self.path else "Untitled"
        dirty = self.features != self.saved
        self.document_label.setText(title + (" *" if dirty else ""))
        self.setWindowTitle(f"{title}{' *' if dirty else ''} — Solid Studio")
        root = QTreeWidgetItem(self.tree, [title])
        root.setIcon(0, icon("Bodies", 18))
        origin = QTreeWidgetItem(root, ["Origin"])
        origin.setIcon(0, icon("Origin", 18))
        for plane in model.PLANES:
            item = QTreeWidgetItem(origin, [f"{plane} Plane"])
            item.setData(0, Qt.ItemDataRole.UserRole + 1, plane)
        for name, objects in (("Bodies", self.state.bodies), ("Sketches", self.state.sketches)):
            group = QTreeWidgetItem(root, [name])
            group.setIcon(0, icon(name if name == "Bodies" else "Sketch", 18))
            for ident in objects:
                item = QTreeWidgetItem(group, [names[ident]])
                item.setIcon(0, icon(name if name == "Bodies" else "Sketch", 16))
                item.setData(0, Qt.ItemDataRole.UserRole, ident)
                if ident in self.viewport.hidden:
                    item.setForeground(0, Qt.GlobalColor.gray)
                if ident == self.selected:
                    self.tree.setCurrentItem(item)
            group.setExpanded(True)
        root.setExpanded(True)
        self.timeline.clear()
        for index, feature in enumerate(self.features):
            fid = feature["id"]
            text = feature["name"] + ("  !" if fid in self.state.errors else "")
            item = QListWidgetItem(icon(feature["kind"], 19), text)
            item.setData(Qt.ItemDataRole.UserRole, fid)
            item.setToolTip(self.state.errors.get(fid, f"{feature['kind']} · Double-click to edit"))
            if index >= self.cursor:
                item.setForeground(Qt.GlobalColor.gray)
            self.timeline.addItem(item)
            if fid == self.selected:
                self.timeline.setCurrentItem(item)
        self.timeline_count.setText(f"{self.cursor} / {len(self.features)}")
        self.viewport.selected = self.selected
        self.update_undo_actions()
        at_end = self.cursor == len(self.features) and self.draft is None
        for name in ("Create Sketch", "Extrude", "Fillet", "Chamfer", "Combine"):
            self.commands[name].setEnabled(at_end and {"Extrude": any(map(model.closed_profiles, self.state.sketches.values())),
                "Fillet": bool(self.state.bodies), "Chamfer": bool(self.state.bodies),
                "Combine": len(self.state.bodies) >= 2}.get(name, True))
        self.commands["Export STEP"].setEnabled(bool(self.state.bodies) and self.draft is None)
        self.statusBar().showMessage(next(iter(self.state.errors.values()), "Ready"))

    def select(self, ident):
        self.selected = ident
        self.viewport.selected = ident
        self.viewport.update()
        for index in range(self.timeline.count()):
            item = self.timeline.item(index)
            if item.data(Qt.ItemDataRole.UserRole) == ident:
                self.timeline.setCurrentItem(item)
                return
        self.timeline.clearSelection()

    def roll(self, cursor):
        if self.draft is None:
            if self.picking_sketch_plane:
                self.stop_plane_picker()
            self.cursor = max(0, min(len(self.features), cursor))
            self.refresh()

    def timeline_menu(self, point):
        item = self.timeline.itemAt(point)
        if item is None or self.draft:
            return
        fid = item.data(Qt.ItemDataRole.UserRole)
        index = self.feature_index(fid)
        menu = QMenu(self)
        menu.addAction("Edit Feature", lambda: self.edit_feature(fid))
        menu.addAction("Roll Before", lambda: self.roll(index))
        menu.addAction("Roll After", lambda: self.roll(index + 1))
        menu.addAction("Delete", lambda: self.delete_feature(index))
        menu.exec(self.timeline.mapToGlobal(point))

    def feature_index(self, fid):
        return next((i for i, f in enumerate(self.features) if f["id"] == fid), None)

    def commit(self, feature, index=None):
        updated = deepcopy(self.features)
        if index is None:
            updated.append(feature)
        else:
            updated[index] = feature
        result = model.evaluate(updated)
        if feature["id"] in result.errors:
            QMessageBox.warning(self, feature["kind"], result.errors[feature["id"]])
            return False
        first_body = not self.state.bodies and bool(result.bodies)
        self.record(updated)
        self.selected = feature["id"]
        self.refresh()
        if first_body:
            self.viewport.fit()
        return True

    def record(self, updated):
        self.undo_stack.append(deepcopy(self.features))
        self.redo_stack.clear()
        self.features = updated
        self.cursor = len(updated)

    def delete_feature(self, index):
        if QMessageBox.question(self, "Delete Feature", "Delete this feature from the timeline?") != QMessageBox.StandardButton.Yes:
            return
        self.record(self.features[:index] + self.features[index + 1:])
        self.selected = ""
        self.refresh()

    def undo(self):
        if self.draft:
            if self.viewport.points:
                self.viewport.points.pop()
                self.viewport.update()
                self.update_undo_actions()
            else:
                self.restore_sketch(self.sketch_history, self.sketch_redo)
            return
        if not self.undo_stack:
            return
        self.redo_stack.append(deepcopy(self.features))
        self.features = self.undo_stack.pop()
        self.roll(len(self.features))

    def redo(self):
        if self.draft:
            self.restore_sketch(self.sketch_redo, self.sketch_history)
            return
        if not self.redo_stack:
            return
        self.undo_stack.append(deepcopy(self.features))
        self.features = self.redo_stack.pop()
        self.roll(len(self.features))

    def update_undo_actions(self):
        self.undo_action.setEnabled(bool(self.sketch_history or self.viewport.points) if self.draft else bool(self.undo_stack))
        self.redo_action.setEnabled(bool(self.sketch_redo) if self.draft else bool(self.redo_stack))

    def restore_sketch(self, source, destination):
        if not source:
            return
        destination.append(deepcopy(self.draft["profiles"]))
        self.draft["profiles"] = source.pop()
        self.sketch_snapshot = deepcopy(self.draft["profiles"])
        self.viewport.select_profile(None)
        self.update_palette()

    def new_feature(self, kind):
        used = {f["name"] for f in self.features}
        number = 1
        while f"{kind} {number}" in used:
            number += 1
        return {"id": uuid4().hex, "name": f"{kind} {number}", "kind": kind}

    def create_sketch(self):
        if self.draft or self.cursor != len(self.features):
            return
        self.picking_sketch_plane = self.viewport.pick_face = True
        self.navigation("", False)
        self.plane_picker.show()
        self.statusBar().showMessage("Select an origin plane or click a flat face in the viewport.")

    def stop_plane_picker(self):
        self.picking_sketch_plane = self.viewport.pick_face = False
        self.plane_picker.hide()
        self.statusBar().clearMessage()

    def start_on_plane(self, plane):
        self.stop_plane_picker()
        self.begin_sketch(self.new_feature("Sketch") | {"plane": plane, "profiles": []}, None)

    def origin_plane_clicked(self, item, column):
        plane = item.data(0, Qt.ItemDataRole.UserRole + 1)
        if self.picking_sketch_plane and plane:
            self.start_on_plane(plane)

    def use_sketch_face(self, support):
        if support is None:
            self.statusBar().showMessage("Choose a visible planar face.")
            return
        self.stop_plane_picker()
        self.begin_sketch(self.new_feature("Sketch") | {"support": support, "profiles": []}, None)

    def begin_sketch(self, draft, index):
        before = model.evaluate(self.features, index) if index is not None else self.state
        try:
            draft = model.resolve_sketch(draft, before.bodies)
        except (ValueError, KeyError) as exc:
            QMessageBox.warning(self, "Edit Sketch", str(exc))
            return
        self.camera = (self.viewport.center, self.viewport.offset, self.viewport.scale)
        self.draft, self.draft_index = draft, index
        self.sketch_history.clear()
        self.sketch_redo.clear()
        self.sketch_snapshot = deepcopy(draft["profiles"])
        self.tab.setText("SKETCH")
        self.solid_strip.hide()
        self.sketch_strip.show()
        self.palette.show()
        self.navigation("", False)
        self.viewport.sketch_mode(draft)
        self.viewport.set_model(before)
        self.viewport.choose_tool("Select")
        self.update_palette()

    def update_palette(self):
        if self.draft:
            profiles = self.draft["profiles"]
            if profiles != self.sketch_snapshot:
                self.sketch_history.append(self.sketch_snapshot)
                self.sketch_redo.clear()
                self.sketch_snapshot = deepcopy(profiles)
            plane = self.draft.get("plane")
            support = f"{plane} Plane" if plane else "Planar Face"
            self.sketch_status.setText(f"{support}   ·   {len(model.closed_profiles(self.draft))} closed profiles")
            self.update_undo_actions()

    def selected_profile(self):
        if not self.draft or not self.draft["profiles"]:
            return None
        index = self.viewport.sketch_selection
        if index is not None:
            return self.draft["profiles"][index]
        return next((p for p in reversed(self.draft["profiles"]) if not p.get("deleted")), None)

    def line_rule(self):
        profile = self.selected_profile()
        if profile is None or profile["type"] != "polyline" or len(profile["points"]) != 2:
            self.statusBar().showMessage("Select an individual straight line.")
            return
        value, ok = QInputDialog.getItem(self, "Sketch Constraint", "Direction", ["Horizontal", "Vertical"], editable=False)
        if ok:
            model.apply_line_rule(profile, value)
            self.update_palette()
            self.viewport.update()

    def project_geometry(self):
        if not self.draft:
            return
        bodies = self.viewport.bodies
        if not bodies:
            self.statusBar().showMessage("There are no bodies to project at this timeline position.")
            return
        names = {feature["id"]: feature["name"] for feature in self.features}
        labels = {names[fid]: fid for fid in bodies}
        label, ok = QInputDialog.getItem(self, "Project", "Body to project as construction lines", list(labels), editable=False)
        if ok:
            projected = model.project_lines(self.draft, bodies[labels[label]])
            self.draft["profiles"].extend(projected)
            self.update_palette()
            self.viewport.update()

    def toggle_construction(self):
        profile = self.selected_profile()
        if profile:
            profile["construction"] = not profile.get("construction", False)
            self.update_palette()
            self.viewport.update()

    def offset_profile(self):
        profile = self.selected_profile()
        if profile is None or profile["type"] not in {"circle", "rectangle"}:
            self.statusBar().showMessage("Select a circle or rectangle to offset.")
            return
        distance, ok = QInputDialog.getDouble(self, "Sketch Offset", "Offset (mm)", 2, -100000, 100000, 3)
        if not ok or distance == 0:
            return
        a, b = profile["points"]
        if profile["type"] == "circle":
            radius = math.dist(a, b) + distance
            points = [a[:], [a[0] + radius, a[1]]] if radius > 0 else None
        else:
            lo = [min(x, y) - distance for x, y in zip(a, b)]
            hi = [max(x, y) + distance for x, y in zip(a, b)]
            points = [lo, hi] if all(x < y for x, y in zip(lo, hi)) else None
        if points:
            self.viewport.add_profile(profile["type"], points)
            self.viewport.update()
        else:
            self.statusBar().showMessage("The offset would collapse the profile.")

    def dimensions(self):
        profile = self.selected_profile()
        if profile is None:
            self.statusBar().showMessage("Draw or select sketch geometry first.")
            return
        points = profile["points"]
        if profile["type"] == "circle":
            value, ok = QInputDialog.getDouble(self, "Sketch Dimension", "Diameter (mm)", 2 * math.dist(*points), 0.01, 100000, 2)
            if ok:
                points[1] = [points[0][0] + value / 2, points[0][1]]
        elif profile["type"] == "rectangle":
            dialog = QDialog(self)
            dialog.setWindowTitle("Sketch Dimensions")
            form = QFormLayout(dialog)
            width = self.number(form, "Width (mm)", abs(points[1][0] - points[0][0]), 0.01)
            height = self.number(form, "Height (mm)", abs(points[1][1] - points[0][1]), 0.01)
            self.buttons(dialog, form)
            if dialog.exec() == QDialog.DialogCode.Accepted:
                points[1] = [points[0][0] + width.value() * (1 if points[1][0] >= points[0][0] else -1),
                             points[0][1] + height.value() * (1 if points[1][1] >= points[0][1] else -1)]
        elif profile["type"] == "polyline" and len(points) == 2:
            length = math.dist(*points)
            value, ok = QInputDialog.getDouble(self, "Sketch Dimension", "Length (mm)", length, 0.01, 100000, 3)
            if ok:
                if profile.get("rule"):
                    model.apply_line_rule(profile, profile["rule"], value)
                else:
                    a, b = points
                    points[1] = [x + (y - x) * value / length for x, y in zip(a, b)]
        else:
            self.statusBar().showMessage("Dimensions are available for circles, rectangles, and individual lines.")
        self.update_palette()
        self.viewport.update()

    def finish_sketch(self):
        if self.draft is None:
            return
        self.viewport.finish_chain()
        if not any(not p.get("deleted") for p in self.draft["profiles"]):
            QMessageBox.information(self, "Finish Sketch", "Draw some sketch geometry before finishing.")
            return
        draft, index = self.draft, self.draft_index
        if self.commit({k: v for k, v in draft.items() if k != "frame"}, index):
            self.cancel_sketch()

    def cancel_sketch(self):
        if self.draft is None:
            return
        self.draft = None
        self.viewport.sketch_mode(None)
        self.viewport.center, self.viewport.offset, self.viewport.scale = self.camera
        self.viewport.update()
        self.tab.setText("SOLID")
        self.sketch_strip.hide()
        self.palette.hide()
        self.solid_strip.show()
        self.refresh()

    def edit_feature(self, fid):
        index = self.feature_index(fid)
        if self.draft or index is None:
            return
        feature = deepcopy(self.features[index])
        if feature["kind"] == "Sketch":
            self.begin_sketch(feature, index)
        else:
            self.feature_dialog(feature["kind"], index)

    def change_parameters(self):
        if self.draft or not self.features:
            return
        names = [feature["name"] for feature in self.features]
        name, ok = QInputDialog.getItem(self, "Change Parameters", "Feature", names, editable=False)
        if ok:
            self.edit_feature(self.features[names.index(name)]["id"])

    def number(self, form, text, value, minimum=-100000):
        widget = QDoubleSpinBox()
        widget.setRange(minimum, 100000)
        widget.setDecimals(3)
        widget.setValue(value)
        form.addRow(text, widget)
        return widget

    def combo(self, form, title, items, current=None):
        widget = QComboBox()
        for text, value in items:
            widget.addItem(text, value)
        index = widget.findData(current)
        if index >= 0:
            widget.setCurrentIndex(index)
        form.addRow(title, widget)
        return widget

    def buttons(self, dialog, form):
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(dialog.accept)
        buttons.rejected.connect(dialog.reject)
        form.addRow(buttons)

    def feature_dialog(self, kind, index=None):
        if self.draft and kind == "Extrude":
            self.finish_sketch()
        if self.draft or self.picking_sketch_plane or (index is None and self.cursor != len(self.features)):
            return
        before = model.evaluate(self.features, index)
        feature = deepcopy(self.features[index]) if index is not None else self.new_feature(kind)
        available_sketches = {fid: sketch for fid, sketch in before.sketches.items() if model.closed_profiles(sketch)}
        if (kind == "Extrude" and not available_sketches) or (kind != "Extrude" and not before.bodies) or (kind == "Combine" and len(before.bodies) < 2):
            self.statusBar().showMessage("Create the required sketch or bodies before using this command.")
            return
        names = {f["id"]: f["name"] for f in self.features}
        bodies = [(names[fid], fid) for fid in before.bodies]
        dialog = QDialog(self)
        dialog.setWindowTitle(kind)
        dialog.setWindowFlags(Qt.WindowType.Tool | Qt.WindowType.WindowTitleHint | Qt.WindowType.WindowCloseButtonHint)
        form = QFormLayout(dialog)
        ops = [(name, name) for name in ("Join", "Cut", "Intersect")]
        if kind == "Extrude":
            sketches = [(f["name"], fid) for fid, f in available_sketches.items()]
            sketch = self.combo(form, "Sketch", sketches, feature.get("sketch", self.selected))
            profile = QComboBox()
            form.addRow("Profile", profile)

            def populate():
                profile.clear()
                for i, p in model.closed_profiles(available_sketches[sketch.currentData()]):
                    profile.addItem(f"{i + 1} · {p['type'].title()}", i)

            sketch.currentIndexChanged.connect(populate)
            populate()
            index_in_menu = profile.findData(feature.get("profile", 0))
            if index_in_menu >= 0:
                profile.setCurrentIndex(index_in_menu)
            distance = self.number(form, "Distance (mm)", feature.get("distance", 10))
            operation = self.combo(form, "Operation", [("New Body", "New Body")] + (ops if bodies else []), feature.get("operation"))
            target = self.combo(form, "Target Body", bodies, feature.get("target", self.selected))
            operation.currentIndexChanged.connect(lambda: target.setEnabled(operation.currentData() != "New Body"))
            target.setEnabled(operation.currentData() != "New Body")
        else:
            target = self.combo(form, "Target Body", bodies, feature.get("target", self.selected))
            if kind == "Combine":
                tool = self.combo(form, "Tool Body", bodies, feature.get("tool"))
                if index is None and tool.currentData() == target.currentData():
                    tool.setCurrentIndex((target.currentIndex() + 1) % len(bodies))
                operation = self.combo(form, "Operation", ops, feature.get("operation"))
                keep_tool = QCheckBox()
                keep_tool.setChecked(feature.get("keep_tool", False))
                form.addRow("Keep Tools", keep_tool)
            else:
                edge_groups = [(name, name) for name in ("Top edges", "Bottom edges", "All edges")]
                edges = self.combo(form, "Edges", edge_groups, feature.get("edges"))
                size = self.number(form, "Radius (mm)" if kind == "Fillet" else "Distance (mm)", feature.get("size", 1), 0.001)
        self.buttons(dialog, form)
        preview = QCheckBox("Show preview")
        preview.setChecked(True)
        form.insertRow(form.rowCount() - 1, preview)
        feedback = QLabel()
        feedback.setWordWrap(True)
        feedback.setMaximumWidth(240)
        form.insertRow(form.rowCount() - 1, feedback)

        def read_values():
            candidate = feature | {"target": target.currentData()}
            if kind == "Extrude":
                candidate.update(sketch=sketch.currentData(), profile=profile.currentData(), distance=distance.value(),
                                 operation=operation.currentData())
            elif kind == "Combine":
                candidate.update(tool=tool.currentData(), operation=operation.currentData(), keep_tool=keep_tool.isChecked())
            else:
                candidate.update(edges=edges.currentData(), size=size.value())
            return candidate

        def show_preview():
            if not preview.isChecked():
                self.viewport.set_model(self.state)
                feedback.clear()
                return
            position = len(self.features) if index is None else index
            result = model.evaluate(self.features[:position] + [read_values()])
            error = result.errors.get(feature["id"])
            feedback.setText(error or "")
            self.viewport.set_model(before if error else result)

        timer = QTimer(dialog)
        timer.setInterval(160)
        timer.setSingleShot(True)
        timer.timeout.connect(show_preview)
        for widget in dialog.findChildren(QComboBox):
            widget.currentIndexChanged.connect(lambda: timer.start())
        for widget in dialog.findChildren(QDoubleSpinBox):
            widget.valueChanged.connect(lambda: timer.start())
        for widget in dialog.findChildren(QCheckBox):
            widget.toggled.connect(lambda: timer.start())
        dialog.adjustSize()
        anchor = self.viewport.mapToGlobal(self.viewport.rect().topRight())
        dialog.move(anchor.x() - dialog.width() - 15, anchor.y() + 116)
        while True:
            timer.start()
            accepted = dialog.exec() == QDialog.DialogCode.Accepted
            timer.stop()
            if not accepted:
                self.viewport.set_model(self.state)
                break
            if self.commit(read_values(), index):
                break

    def properties(self):
        if not self.state.bodies:
            self.statusBar().showMessage("No solid bodies at this timeline position.")
            return
        active = self.state.bodies.get(self.selected)
        shapes = [active] if active is not None else list(self.state.bodies.values())
        QMessageBox.information(self, "Properties", f"Volume: {sum(s.Volume() for s in shapes):,.3f} mm³\n"
                                f"Surface area: {sum(s.Area() for s in shapes):,.3f} mm²\n"
                                f"Solid count: {sum(len(s.Solids()) for s in shapes)}")

    def confirm_discard(self):
        if self.draft:
            QMessageBox.information(self, "Sketch Active", "Finish or cancel the sketch first.")
            return False
        if self.features == self.saved:
            return True
        answer = QMessageBox.question(self, "Unsaved Changes", "Save changes to this design?",
                                      QMessageBox.StandardButton.Save | QMessageBox.StandardButton.Discard | QMessageBox.StandardButton.Cancel)
        return answer == QMessageBox.StandardButton.Discard or (answer == QMessageBox.StandardButton.Save and self.save())

    def replace_document(self, path, features):
        self.path, self.features, self.saved = path, features, deepcopy(features)
        self.selected = ""
        self.viewport.hidden.clear()
        self.undo_stack.clear()
        self.redo_stack.clear()
        self.roll(len(features))
        self.viewport.set_view("HOME")
        self.viewport.fit()

    def new(self):
        if self.confirm_discard():
            self.replace_document(None, [])

    def open(self):
        if not self.confirm_discard():
            return
        path, _ = QFileDialog.getOpenFileName(self, "Open Design", "", "Solid Studio (*.solidstudio)")
        if path:
            try:
                self.replace_document(Path(path), model.load(path))
            except (OSError, ValueError) as exc:
                QMessageBox.warning(self, "Open Design", str(exc))

    def save(self, as_new=False):
        if self.draft:
            self.statusBar().showMessage("Finish the sketch before saving.")
            return False
        path = None if as_new else self.path
        if path is None:
            selected, _ = QFileDialog.getSaveFileName(self, "Save Design", "Untitled.solidstudio", "Solid Studio (*.solidstudio)")
            if not selected:
                return False
            path = Path(selected)
            if path.suffix.lower() != ".solidstudio":
                path = path.with_suffix(".solidstudio")
        try:
            model.save(path, self.features)
        except (OSError, ValueError) as exc:
            QMessageBox.warning(self, "Save Design", str(exc))
            return False
        self.path, self.saved = path, deepcopy(self.features)
        self.refresh()
        return True

    def export(self):
        if self.draft or not self.state.bodies:
            return
        path, _ = QFileDialog.getSaveFileName(self, "Export STEP", "Design.step", "STEP (*.step *.stp)")
        if path:
            path = Path(path)
            if path.suffix.lower() not in {".step", ".stp"}:
                path = path.with_suffix(".step")
            try:
                model.export_step(path, self.state)
                self.statusBar().showMessage(f"Exported {path.name}")
            except Exception as exc:
                QMessageBox.warning(self, "Export STEP", str(exc))

    def help(self):
        QMessageBox.information(self, "Controls", "Middle drag: pan\nShift + middle drag: orbit\n"
                                "Wheel: zoom\nHome: fit\nView cube: standard views\n"
                                "E: extrude · F: fillet\nL / R / C: line / rectangle / circle in a sketch\n"
                                "Double-click a timeline feature to edit it. Right-click to roll before or after it.")

    def closeEvent(self, event):
        if self.confirm_discard():
            event.accept()
        else:
            event.ignore()


def main():
    application = QApplication(sys.argv)
    application.setApplicationName("Solid Studio")
    application.setStyle("Fusion")
    application.setStyleSheet(STYLE)
    window = Studio()
    window.show()
    if len(application.arguments()) == 2:
        path = Path(application.arguments()[1])
        try:
            window.replace_document(path, model.load(path))
        except (OSError, ValueError) as exc:
            QMessageBox.warning(window, "Open Design", str(exc))
    return application.exec()
