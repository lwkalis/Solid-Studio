from copy import deepcopy
from pathlib import Path
import tempfile
import unittest
import math

import cadquery as cq

from solidstudio import model


EXAMPLE = Path(__file__).resolve().parents[1] / "examples" / "mounting-plate.solidstudio"


class SolidModelTests(unittest.TestCase):
    def test_nested_profiles_and_arc_chain(self):
        sketch = {"plane": "XY", "profiles": [
            {"type": "rectangle", "points": [[-10, -10], [10, 10]]},
            {"type": "circle", "points": [[0, 0], [3, 0]]},
            {"type": "circle", "points": [[0, 0], [2, 0]], "construction": True},
        ]}
        self.assertAlmostEqual(model.extrude(sketch, 5, 0).Volume(), (400 - 9 * math.pi) * 5)
        sketch["profiles"] = [
            {"type": "open_arc", "points": [[-5, 0], [0, 5], [5, 0]]},
            {"type": "polyline", "points": [[5, 0], [-5, 0]]},
        ]
        self.assertAlmostEqual(model.extrude(sketch, 2, 0).Volume(), 25 * math.pi)

    def test_planar_face_sketch_tracks_pad_height(self):
        features = model.load(EXAMPLE)[:2]
        plate = model.evaluate(features).bodies["plate"]
        top = max(plate.Faces(), key=lambda face: face.Center().z)
        features += [
            {"kind": "Sketch", "id": "face-sketch", "name": "Face Sketch",
             "support": model.face_support("plate", top),
             "profiles": [{"type": "circle", "points": [[0, 0], [3, 0]]}]},
            {"kind": "Extrude", "id": "hole", "name": "Hole", "sketch": "face-sketch",
             "operation": "Cut", "target": "plate", "distance": -4, "profile": 0},
        ]
        for height in (10, 15):
            features[1]["distance"] = height
            result = model.evaluate(features)
            self.assertFalse(result.errors)
            self.assertAlmostEqual(model.sketch_frame(result.sketches["face-sketch"])[0][2], height)
            self.assertAlmostEqual(result.bodies["plate"].Volume(), 2160 * height - 36 * math.pi)

    def test_trim_constraints_and_projection(self):
        profiles = [
            {"type": "polyline", "points": [[0, 0], [10, 0]]},
            {"type": "polyline", "points": [[3, -2], [3, 2]]},
            {"type": "polyline", "points": [[7, -2], [7, 2]]},
        ]
        model.trim_line(profiles, 0, [5, 0])
        self.assertEqual(profiles[0]["points"], [[0, 0], [3, 0]])
        self.assertEqual(profiles[-1]["points"], [[7, 0], [10, 0]])
        model.apply_line_rule(profiles[0], "Vertical", 4)
        self.assertEqual(profiles[0]["points"], [[0, 0], [0, 4]])
        plate = model.evaluate(model.load(EXAMPLE), 2).bodies["plate"]
        projected = model.project_lines({"plane": "XY"}, plate)
        self.assertEqual(len(projected), 4)
        self.assertFalse(model.closed_profiles({"profiles": projected}))

    def test_sketch_edit_rebuilds_cut_and_fillet(self):
        features = model.load(EXAMPLE)
        start = model.evaluate(features, 2)
        original = model.evaluate(features)
        self.assertFalse(original.errors)
        self.assertAlmostEqual(start.bodies["plate"].Volume(), 21600)
        self.assertLess(original.bodies["plate"].Volume(), 21600)
        edited = deepcopy(features)
        edited[0]["profiles"][0]["points"][1][0] = 34
        revised = model.evaluate(edited)
        self.assertFalse(revised.errors)
        self.assertGreater(revised.bodies["plate"].Volume(), original.bodies["plate"].Volume())
        self.assertEqual(len(revised.bodies["plate"].Faces()), len(original.bodies["plate"].Faces()))

    def test_document_and_step_round_trip(self):
        features = model.load(EXAMPLE)
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "plate.solidstudio"
            model.save(path, features)
            self.assertEqual(model.load(path), features)
            evaluated = model.evaluate(features)
            model.export_step(path.with_suffix(".step"), evaluated)
            imported = cq.importers.importStep(str(path.with_suffix(".step"))).val()
            self.assertAlmostEqual(imported.Volume(), evaluated.bodies["plate"].Volume(), places=5)

    def test_boolean_preserves_inputs_when_an_operation_fails(self):
        features = model.load(EXAMPLE)[:2]
        tool_sketch = {
            "id": "tool-sketch", "kind": "Sketch", "name": "Tool sketch", "plane": "XY",
            "profiles": [{"type": "rectangle", "points": [[20, -10], [40, 10]]}],
        }
        tool = {
            "id": "tool", "kind": "Extrude", "name": "Tool", "sketch": "tool-sketch",
            "profile": 0, "distance": 10, "operation": "New Body",
        }
        combine = {
            "id": "combine", "kind": "Combine", "name": "Combine", "target": "plate",
            "tool": "tool", "operation": "Intersect", "keep_tool": False,
        }
        result = model.evaluate(features + [tool_sketch, tool, combine])
        self.assertFalse(result.errors)
        self.assertEqual(list(result.bodies), ["plate"])
        self.assertAlmostEqual(result.bodies["plate"].Volume(), 2000)
        tool_sketch["profiles"][0]["points"] = [[40, -10], [60, 10]]
        result = model.evaluate(features + [tool_sketch, tool, combine])
        self.assertIn("combine", result.errors)
        self.assertEqual(set(result.bodies), {"plate", "tool"})
