from pathlib import Path
import unittest

from solidstudio import model

from PySide6.QtCore import QPoint, Qt, QTimer
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QDialog, QDialogButtonBox, QDoubleSpinBox

from solidstudio.ui import STYLE, Studio


class WorkspaceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.application = QApplication.instance() or QApplication([])
        cls.application.setStyle("Fusion")
        cls.application.setStyleSheet(STYLE)

    def setUp(self):
        self.window = Studio()
        self.window.show()
        self.application.processEvents()

    def tearDown(self):
        self.window.cancel_sketch()
        self.window.saved = self.window.features
        self.window.close()

    def test_create_sketch_and_extrude_through_controls(self):
        window = self.window
        viewport = window.viewport
        draft = window.new_feature("Sketch") | {"plane": "XY", "profiles": []}
        window.begin_sketch(draft, None)
        window.commands["Rectangle"].click()
        self.application.processEvents()
        center = viewport.rect().center()
        for corner in (center - QPoint(70, 70), center + QPoint(70, 70)):
            QTest.mouseClick(viewport, Qt.MouseButton.LeftButton, pos=corner)
        window.commands["Finish Sketch"].click()
        self.assertEqual(len(window.state.sketches), 1)

        def accept_extrude():
            dialog = window.findChild(QDialog)
            dialog.findChild(QDoubleSpinBox).setValue(6)
            buttons = dialog.findChild(QDialogButtonBox)
            buttons.button(QDialogButtonBox.StandardButton.Ok).click()

        QTimer.singleShot(220, accept_extrude)
        window.commands["Extrude"].click()
        body, = window.state.bodies.values()
        self.assertAlmostEqual(body.Volume(), 2400)
        self.assertEqual(window.timeline.count(), 2)
        window.roll(1)
        self.assertFalse(window.state.bodies)
        window.roll(2)
        self.assertTrue(window.state.bodies)

    def test_example_renders_and_visibility_shortcut_updates_view(self):
        path = Path(__file__).resolve().parents[1] / "examples" / "mounting-plate.solidstudio"
        self.window.replace_document(path, model.load(path))
        self.application.processEvents()
        self.assertTrue(self.window.viewport.picking)
        self.window.select("plate")
        self.window.toggle_visibility()
        self.application.processEvents()
        self.assertFalse(self.window.viewport.picking)

    def test_create_sketch_by_clicking_a_face(self):
        path = Path(__file__).resolve().parents[1] / "examples" / "mounting-plate.solidstudio"
        window = self.window
        window.replace_document(path, model.load(path)[:2])
        window.viewport.set_view("TOP")
        window.commands["Create Sketch"].click()
        self.application.processEvents()
        point = window.viewport.project((0, 0, 10))[0].toPoint()
        QTest.mouseClick(window.viewport, Qt.MouseButton.LeftButton, pos=point)
        self.assertIsNotNone(window.draft)
        self.assertEqual(window.draft["support"]["body"], "plate")
        self.assertAlmostEqual(model.sketch_frame(window.draft)[0][2], 10)
        window.viewport.choose_tool("Circle")
        center = window.viewport.rect().center()
        for point in (center, center + QPoint(35, 0)):
            QTest.mouseClick(window.viewport, Qt.MouseButton.LeftButton, pos=point)
        window.commands["Finish Sketch"].click()
        self.assertIsNone(window.draft)
        self.assertNotIn("frame", window.features[-1])
        self.assertFalse(window.state.errors)

    def test_sketch_undo_redo_and_construction(self):
        window = self.window
        window.start_on_plane("XY")
        window.viewport.add_profile("circle", [[0, 0], [5, 0]])
        window.toggle_construction()
        self.assertFalse(model.closed_profiles(window.draft))
        window.undo()
        self.assertEqual(len(model.closed_profiles(window.draft)), 1)
        window.undo()
        self.assertEqual(window.draft["profiles"], [])
        window.redo()
        window.redo()
        self.assertTrue(window.draft["profiles"][0]["construction"])
