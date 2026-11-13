"""Orthographic viewport, sketch input, and visible-body picking."""

import math
from copy import deepcopy

from PySide6.QtCore import QPointF, QRectF, Qt, Signal
from PySide6.QtGui import QColor, QPainter, QPen, QPolygonF, QLinearGradient
from PySide6.QtWidgets import QWidget

from . import model


def dot(a, b):
    return sum(x * y for x, y in zip(a, b))


def cross(a, b):
    return (a[1] * b[2] - a[2] * b[1], a[2] * b[0] - a[0] * b[2],
            a[0] * b[1] - a[1] * b[0])


def unit(v):
    length = math.sqrt(dot(v, v))
    return tuple(x / length for x in v) if length else (0, 0, 0)


def depth_at(point, triangle):
    (a, za), (b, zb), (c, zc) = triangle
    u, v, q = b - a, c - a, point - a
    denominator = u.x() * v.y() - u.y() * v.x()
    if abs(denominator) < 1e-8:
        return None
    s = (q.x() * v.y() - q.y() * v.x()) / denominator
    t = (u.x() * q.y() - u.y() * q.x()) / denominator
    if s < -1e-5 or t < -1e-5 or s + t > 1.00001:
        return None
    return za + s * (zb - za) + t * (zc - za)


class Viewport(QWidget):
    body_selected = Signal(str)
    coordinates = Signal(str)
    sketch_changed = Signal()
    face_selected = Signal(object)
    sketch_notice = Signal(str)
    dimension_requested = Signal(int, int, object)

    def __init__(self):
        super().__init__()
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setMouseTracking(True)
        self.setMinimumSize(420, 330)
        self.scale = 7
        self.offset = QPointF()
        self.center = (0, 0, 0)
        self.yaw = math.radians(42)
        self.pitch = math.radians(29)
        self.navigation = ""
        self.meshes = {}
        self.sketches = {}
        self.selected = ""
        self.draft = None
        self.tool = "Line"
        self.points = []
        self.hover = None
        self.drag = None
        self.picking = []
        self.grid = True
        self.show_cube = True
        self.hidden = set()
        self.pick_face = False
        self.face_meshes = {}
        self.sketch_selection = None
        self.sketch_edge = None
        self.sketch_drag = None
        self.pending_dimension = None
        self.dimension_labels = []
        self.snap = True

    def axes(self):
        if self.draft is not None:
            return model.sketch_frame(self.draft)[1:]
        cy, sy = math.cos(self.yaw), math.sin(self.yaw)
        cp, sp = math.cos(self.pitch), math.sin(self.pitch)
        return (cy, -sy, 0), (sy * sp, cy * sp, cp), (-sy * cp, -cy * cp, sp)

    def project(self, vector):
        relative = tuple(v - c for v, c in zip(vector, self.center))
        right, up, depth = self.axes()
        return QPointF(self.width() / 2 + self.offset.x() + dot(relative, right) * self.scale,
                       self.height() / 2 + self.offset.y() - dot(relative, up) * self.scale), dot(relative, depth)

    def sketch_point(self, point, snap=True):
        coords = [(point.x() - self.width() / 2 - self.offset.x()) / self.scale,
                  (self.height() / 2 + self.offset.y() - point.y()) / self.scale]
        return [round(value) if snap and self.snap else value for value in coords]

    def sketch_screen(self, point):
        return QPointF(self.width() / 2 + self.offset.x() + point[0] * self.scale,
                       self.height() / 2 + self.offset.y() - point[1] * self.scale)

    def draw_point(self, pos):
        candidates = [p for profile in self.draft["profiles"] if not profile.get("deleted")
                      and profile["type"] in {"polyline", "open_arc", "polygon"}
                      for p in (profile["points"][0], profile["points"][-1])]
        if candidates:
            nearest = min(candidates, key=lambda p: (self.sketch_screen(p) - pos).manhattanLength())
            if (self.sketch_screen(nearest) - pos).manhattanLength() <= 8:
                return list(nearest)
        return self.sketch_point(pos)

    def set_model(self, state):
        self.sketches = state.sketches
        self.bodies = state.bodies
        self.face_meshes.clear()
        self.meshes = {}
        for ident, shape in state.bodies.items():
            vertices, triangles = shape.tessellate(0.12, 0.2)
            edges = []
            for edge in shape.Edges():
                if edge.geomType() == "LINE":
                    points = [edge.startPoint(), edge.endPoint()]
                else:
                    points = [edge.positionAt(i / 32) for i in range(33)]
                edges.append([v.toTuple() for v in points])
            self.meshes[ident] = ([v.toTuple() for v in vertices], triangles, edges)
        self.update()

    def fit(self):
        vertices = [v for ident, (v, _, _) in self.meshes.items() if v and ident not in self.hidden]
        if not vertices or self.draft is not None:
            self.center = model.sketch_frame(self.draft)[0] if self.draft else (0, 0, 0)
            self.offset, self.scale = QPointF(), 7
        else:
            all_vertices = [v for vertices_in_body in vertices for v in vertices_in_body]
            self.center = tuple((min(v[i] for v in all_vertices) + max(v[i] for v in all_vertices)) / 2
                                for i in range(3))
            right, up, _ = self.axes()
            span_x = max(dot(v, right) for v in all_vertices) - min(dot(v, right) for v in all_vertices)
            span_y = max(dot(v, up) for v in all_vertices) - min(dot(v, up) for v in all_vertices)
            self.scale = min(self.width() / max(span_x, 1), self.height() / max(span_y, 1)) * 0.62
            self.offset = QPointF()
        self.update()

    def set_view(self, name):
        if self.draft is not None:
            return
        self.yaw, self.pitch = {
            "HOME": (math.radians(42), math.radians(29)),
            "TOP": (0, math.pi / 2), "FRONT": (0, 0), "RIGHT": (math.pi / 2, 0),
        }[name]
        self.update()

    def sketch_mode(self, draft):
        self.draft = draft
        self.points = []
        self.hover = None
        self.sketch_selection = None
        self.sketch_edge = self.sketch_drag = self.pending_dimension = None
        self.dimension_labels.clear()
        self.navigation = ""
        self.fit()

    def choose_tool(self, name):
        self.cancel_sketch_drag()
        self.finish_chain()
        self.tool, self.points = name, []
        self.pending_dimension = None
        self.setFocus()
        self.update()

    def remove_profile(self):
        if self.points:
            self.points.pop()
        elif self.draft and self.draft["profiles"]:
            index = self.sketch_selection
            if index is not None:
                self.draft["profiles"][index]["deleted"] = True
            self.sketch_selection = None
            self.sketch_edge = None
        self.sketch_changed.emit()
        self.update()

    def select_profile(self, index):
        self.sketch_selection = index
        self.sketch_edge = None
        self.update()

    def cancel_sketch_drag(self):
        if self.sketch_drag:
            self.draft["profiles"] = self.sketch_drag[3]
            self.sketch_drag = None

    def move_sketch_drag(self, pos):
        index, edge, start, original, vertex = self.sketch_drag
        delta = [(b - a) / self.scale for a, b in zip((start.x(), -start.y()), (pos.x(), -pos.y()))]
        if (pos - start).manhattanLength() < 3:
            self.draft["profiles"] = original
            return
        if self.snap:
            delta = list(map(round, delta))
        candidate = deepcopy(original)
        if vertex is None:
            model.move_sketch_segment(original, candidate, index, edge, delta)
        else:
            profile = original[index]
            point = profile["points"][vertex]
            rule = profile.get("rule")
            if rule:
                delta[1 if rule == "Horizontal" else 0] = 0
            position = [a + b for a, b in zip(point, delta)]
            if profile["type"] == "polyline" and len(profile["points"]) == 2:
                dimension = profile.get("dimensions", {}).get("0")
                if dimension and not rule:
                    anchor = profile["points"][1 - vertex]
                    distance = math.dist(position, anchor)
                    if distance < 1e-7:
                        return
                    position = [a + (b - a) * dimension["value"] / distance for a, b in zip(anchor, position)]
            model.move_sketch_points(original, candidate, index, {vertex: position})
        if model.sketch_edit_is_valid(original, candidate):
            self.draft["profiles"] = candidate

    def finish_chain(self):
        if self.draft and self.tool == "Line" and len(self.points) >= 2:
            self.add_profile("polyline", self.points)
        self.points = []
        self.sketch_changed.emit()
        self.update()

    def add_profile(self, kind, points):
        self.draft["profiles"].append({"type": kind, "points": points})
        self.sketch_selection = len(self.draft["profiles"]) - 1
        self.sketch_edge = None
        self.sketch_changed.emit()

    def pick_planar_face(self, pos):
        if not self.face_meshes:
            for ident, shape in self.bodies.items():
                for index, face in enumerate(shape.Faces()):
                    verts, triangles = face.tessellate(0.12, 0.2)
                    self.face_meshes[ident, index] = face, [v.toTuple() for v in verts], triangles
        best = None
        for (ident, _), (face, vertices, triangles) in self.face_meshes.items():
            if ident in self.hidden:
                continue
            projected = [self.project(v) for v in vertices]
            for triangle in triangles:
                depth = depth_at(pos, [projected[i] for i in triangle])
                if depth is not None and (best is None or depth > best[0]):
                    best = depth, ident, face
        if best and best[2].geomType() == "PLANE":
            return model.face_support(best[1], best[2])
        return None

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        gradient = QLinearGradient(0, 0, 0, self.height())
        gradient.setColorAt(0, QColor("#edf0f4"))
        gradient.setColorAt(1, QColor("#f9fafb"))
        p.fillRect(self.rect(), gradient)
        if self.grid:
            self.draw_grid(p)
        self.draw_model(p)
        if self.draft is not None:
            self.draw_sketch(p)
        else:
            self.draw_profiles(p)
            if self.show_cube:
                self.draw_cube(p)
        self.draw_axes(p)

    def draw_grid(self, p):
        step = 10 ** math.floor(math.log10(85 / max(self.scale, 0.001)))
        origin, _ = self.project(model.sketch_frame(self.draft)[0] if self.draft else (0, 0, 0))
        if self.draft is not None:
            spacing = step * self.scale
            p.setPen(QPen(QColor("#dce2e8"), 1))
            for x in range(round(origin.x() % spacing), self.width(), max(1, round(spacing))):
                p.drawLine(x, 0, x, self.height())
            for y in range(round(origin.y() % spacing), self.height(), max(1, round(spacing))):
                p.drawLine(0, y, self.width(), y)
            p.setPen(QPen(QColor("#7da68b"), 1))
            p.drawLine(QPointF(origin.x(), 0), QPointF(origin.x(), self.height()))
            p.setPen(QPen(QColor("#cb8e86"), 1))
            p.drawLine(QPointF(0, origin.y()), QPointF(self.width(), origin.y()))
            return
        if abs(self.pitch) < 0.015:
            return
        extent = 22 * step
        cx = round(self.center[0] / step) * step
        cy = round(self.center[1] / step) * step
        for i in range(-22, 23):
            x, y = cx + i * step, cy + i * step
            for a, b, coord, axis in [((x, cy - extent, 0), (x, cy + extent, 0), x, "#a5b5aa"),
                                      ((cx - extent, y, 0), (cx + extent, y, 0), y, "#c6ada9")]:
                p.setPen(QPen(QColor(axis if abs(coord) < 1e-8 else "#e0e4e9"), 1))
                p.drawLine(self.project(a)[0], self.project(b)[0])

    def draw_model(self, p):
        faces = []
        camera = self.axes()[2]
        light = unit((-0.35, -0.55, 1))
        for ident, (vertices, triangles, _) in self.meshes.items():
            if ident in self.hidden:
                continue
            projected = [self.project(v) for v in vertices]
            for a, b, c in triangles:
                v, w, x = vertices[a], vertices[b], vertices[c]
                normal = unit(cross(tuple(i - j for i, j in zip(w, v)), tuple(i - j for i, j in zip(x, v))))
                if dot(normal, camera) <= 1e-7:
                    continue
                triangle = [projected[i] for i in (a, b, c)]
                polygon = QPolygonF([point for point, _ in triangle])
                depth = sum(value for _, value in triangle) / 3
                shade = 0.74 + 0.26 * max(0, dot(normal, light))
                rgb = (123, 185, 220) if ident == self.selected else (195, 207, 219)
                color = QColor(*(int(value * shade) for value in rgb))
                faces.append((depth, polygon, color, ident, triangle, polygon.boundingRect()))
        faces.sort(key=lambda face: face[0])
        self.picking = faces
        for _, polygon, color, _, _, _ in faces:
            p.setPen(QPen(color, 0.7))
            p.setBrush(color)
            p.drawPolygon(polygon)
        for ident, (_, _, edges) in self.meshes.items():
            if ident in self.hidden:
                continue
            p.setPen(QPen(QColor("#305e7b" if ident == self.selected else "#647587"), 1.15))
            for edge in edges:
                for a, b in zip(edge, edge[1:]):
                    pt, da = self.project(a)
                    qt, db = self.project(b)
                    mid = (pt + qt) / 2
                    limit = (da + db) / 2 + 0.3
                    if not any((depth := depth_at(mid, triangle)) is not None and depth > limit
                               for _, _, _, _, triangle, bounds in faces if bounds.contains(mid)):
                        p.drawLine(pt, qt)

    def draw_profiles(self, p):
        if self.selected not in self.sketches:
            return
        sketch = self.sketches[self.selected]
        p.setPen(QPen(QColor("#317da5"), 1.8))
        p.setBrush(Qt.BrushStyle.NoBrush)
        for profile in sketch["profiles"]:
            if profile.get("deleted"):
                continue
            points = model.sketch_path(profile)
            p.drawPolyline(QPolygonF([self.project(model.world(sketch, *point).toTuple())[0]
                                       for point in points]))

    def draw_sketch(self, p):
        def shape(profile, preview=False, selected=False):
            points = model.sketch_path(profile)
            if not points:
                return
            path = QPolygonF([self.sketch_screen(point) for point in points])
            dashed = preview or profile.get("construction")
            color = "#df8b3b" if selected else "#518351" if profile.get("fixed") else "#76a4bb" if dashed else "#2b78a5"
            p.setPen(QPen(QColor(color), 2 if selected else 1.6,
                          Qt.PenStyle.DashLine if dashed else Qt.PenStyle.SolidLine))
            if not dashed and points[0] == points[-1]:
                p.setBrush(QColor(116, 182, 220, 32))
                p.drawPolygon(path)
            else:
                p.setBrush(Qt.BrushStyle.NoBrush)
                p.drawPolyline(path)
            for pt in map(self.sketch_screen, profile["points"]):
                p.drawEllipse(pt, 2.7, 2.7)

        for index, profile in enumerate(self.draft["profiles"]):
            if not profile.get("deleted"):
                shape(profile, selected=index == self.sketch_selection and self.sketch_edge is None)
                if index == self.sketch_selection and self.sketch_edge is not None:
                    a, b = model.dimension_segment(profile, self.sketch_edge)
                    p.setPen(QPen(QColor("#df8b3b"), 2.5))
                    p.drawLine(self.sketch_screen(a), self.sketch_screen(b))
        self.draw_dimensions(p)
        if self.points and self.hover is not None:
            kind = {"Line": "polyline", "Rectangle": "rectangle", "Circle": "circle", "Arc": "open_arc"}[self.tool]
            points = self.points + [self.hover]
            if self.tool == "Arc" and len(points) == 3:
                points = [points[0], points[2], points[1]]
            shape({"type": kind, "points": points}, True)

    def draw_dimensions(self, painter):
        self.dimension_labels.clear()
        for index, profile in enumerate(self.draft["profiles"]):
            if profile.get("deleted"):
                continue
            for dimension in profile.get("dimensions", {}).values():
                rect = self.draw_dimension(painter, profile, dimension["edge"], dimension["label"])
                self.dimension_labels.append((rect, index, dimension))
        if self.pending_dimension is not None and self.hover is not None:
            index, edge = self.pending_dimension
            self.draw_dimension(painter, self.draft["profiles"][index], edge, self.hover)

    def draw_dimension(self, painter, profile, edge, label):
        a, b = map(self.sketch_screen, model.dimension_segment(profile, edge))
        label = self.sketch_screen(label)
        dx, dy = b.x() - a.x(), b.y() - a.y()
        length = math.hypot(dx, dy)
        normal = QPointF(-dy / length, dx / length) if length else QPointF(0, 1)
        offset = normal * dot((label.x() - a.x(), label.y() - a.y()), (normal.x(), normal.y()))
        painter.setPen(QPen(QColor("#334d65"), 1))
        painter.drawLine(a, a + offset)
        painter.drawLine(b, b + offset)
        painter.drawLine(a + offset, b + offset)
        painter.drawLine(a + offset - normal * 4, a + offset + normal * 4)
        painter.drawLine(b + offset - normal * 4, b + offset + normal * 4)
        value = length / self.scale
        prefix = "⌀ " if profile["type"] == "circle" else ""
        text = f"{prefix}{value:.3f}".rstrip("0").rstrip(".")
        width = painter.fontMetrics().horizontalAdvance(text) + 10
        rect = QRectF(label.x() - width / 2, label.y() - 9, width, 18)
        painter.fillRect(rect, QColor("#f8fafc"))
        painter.drawText(rect, Qt.AlignmentFlag.AlignCenter, text)
        return rect

    def cube_polygons(self):
        x = self.width() - 82
        return {
            "TOP": [(x, 36), (x + 35, 19), (x + 69, 36), (x + 35, 53)],
            "FRONT": [(x, 36), (x + 35, 53), (x + 35, 88), (x, 71)],
            "RIGHT": [(x + 35, 53), (x + 69, 36), (x + 69, 71), (x + 35, 88)],
        }

    def draw_cube(self, p):
        p.save()
        font = p.font()
        font.setPixelSize(9)
        p.setFont(font)
        for label, points in self.cube_polygons().items():
            poly = QPolygonF([QPointF(*point) for point in points])
            p.setPen(QPen(QColor("#9ea9b4"), 1))
            p.setBrush(QColor({"TOP": "#f4f6f7", "FRONT": "#dde3e8", "RIGHT": "#c6d0d8"}[label]))
            p.drawPolygon(poly)
            p.setPen(QColor("#536371"))
            p.drawText(poly.boundingRect(), Qt.AlignmentFlag.AlignCenter, label)
        p.setPen(QColor("#687684"))
        p.drawText(QRectF(self.width() - 83, 91, 70, 23), Qt.AlignmentFlag.AlignCenter, "Home")
        p.restore()

    def draw_axes(self, p):
        center = QPointF(35, self.height() - 33)
        origin = self.project((0, 0, 0))[0]
        for name, point, color in [("X", (1, 0, 0), "#be625b"), ("Y", (0, 1, 0), "#62986e"),
                                   ("Z", (0, 0, 1), "#568cb2")]:
            delta = (self.project(point)[0] - origin) * (20 / self.scale)
            if delta.manhattanLength() < 3:
                continue
            p.setPen(QPen(QColor(color), 2))
            p.drawLine(center, center + delta)
            p.drawText(center + delta * 1.22 + QPointF(-3, 3), name)

    def mousePressEvent(self, event):
        self.setFocus()
        pos = event.position()
        if event.button() == Qt.MouseButton.MiddleButton or self.navigation in {"Pan", "Orbit", "Zoom"}:
            mode = self.navigation or ("Orbit" if event.modifiers() & Qt.KeyboardModifier.ShiftModifier else "Pan")
            self.drag = (pos, mode)
            return
        if event.button() != Qt.MouseButton.LeftButton:
            return
        if self.draft is not None:
            if self.tool == "Dimension":
                self.pick_dimension(pos)
                return
            point = self.sketch_point(pos) if self.tool in {"Select", "Trim"} else self.draw_point(pos)
            if self.tool in {"Select", "Trim"}:
                self.sketch_edge = None
                hit = model.closest_sketch_edge(self.draft["profiles"], self.sketch_point(pos, False), 9 / self.scale)
                self.sketch_selection = hit[0] if hit else None
                if self.tool == "Select" and hit:
                    index, edge = hit
                    profiles = self.draft["profiles"]
                    profile = profiles[index]
                    if profile["type"] in {"rectangle", "polyline", "polygon"}:
                        self.sketch_edge = edge
                        vertices = profile["points"]
                        vertex = next((i for i, v in enumerate(vertices)
                                       if (self.sketch_screen(v) - pos).manhattanLength() <= 7), None)
                        self.sketch_drag = index, edge, pos, deepcopy(profiles), vertex
                if self.tool == "Trim" and self.sketch_selection is not None:
                    try:
                        model.trim_line(self.draft["profiles"], self.sketch_selection, point)
                    except ValueError as exc:
                        self.sketch_notice.emit(str(exc))
            elif not self.points:
                self.points.append(point)
            elif self.tool == "Line":
                close_distance = 9 / self.scale
                if len(self.points) >= 3 and math.dist(point, self.points[0]) <= close_distance:
                    self.add_profile("polygon", self.points)
                    self.points = []
                elif point != self.points[-1]:
                    self.points.append(point)
            elif self.tool == "Arc":
                if len(self.points) == 1 and point != self.points[0]:
                    self.points.append(point)
                elif len(self.points) == 2 and model.arc_geometry([self.points[0], point, self.points[1]]) is not None:
                    self.add_profile("open_arc", [self.points[0], point, self.points[1]])
                    self.points = []
            elif point != self.points[0]:
                if self.tool == "Rectangle" and (point[0] == self.points[0][0] or point[1] == self.points[0][1]):
                    return
                self.add_profile(self.tool.lower(), self.points + [point])
                self.points = []
            self.sketch_changed.emit()
            self.update()
            return
        if self.show_cube:
            for view, polygon in self.cube_polygons().items():
                if QPolygonF([QPointF(*pt) for pt in polygon]).containsPoint(pos, Qt.FillRule.OddEvenFill):
                    self.set_view(view)
                    return
            if QRectF(self.width() - 83, 91, 70, 23).contains(pos):
                self.set_view("HOME")
                return
        if self.pick_face:
            self.face_selected.emit(self.pick_planar_face(pos))
            return
        hits = [(depth, ident) for _, _, _, ident, triangle, bounds in self.picking
                if bounds.contains(pos) and (depth := depth_at(pos, triangle)) is not None]
        self.body_selected.emit(max(hits)[1] if hits else "")

    def mouseMoveEvent(self, event):
        pos = event.position()
        if self.sketch_drag:
            self.move_sketch_drag(pos)
        if self.drag:
            previous, mode = self.drag
            delta = pos - previous
            if mode == "Orbit" and self.draft is None:
                self.yaw -= delta.x() * 0.009
                self.pitch = min(math.pi / 2, max(-math.pi / 2, self.pitch + delta.y() * 0.009))
            elif mode == "Zoom":
                self.scale = min(500, max(0.05, self.scale * math.exp(-delta.y() * 0.012)))
            else:
                self.offset += delta
            self.drag = pos, mode
        if self.draft is not None:
            self.hover = self.sketch_point(pos, False) if self.tool in {"Select", "Trim", "Dimension"} else self.draw_point(pos)
            self.coordinates.emit(f"X {self.hover[0]:.3g} mm   Y {self.hover[1]:.3g} mm")
        self.update()

    def mouseReleaseEvent(self, event):
        self.drag = None
        if self.sketch_drag and event.button() == Qt.MouseButton.LeftButton:
            self.move_sketch_drag(event.position())
            self.sketch_drag = None
            self.sketch_changed.emit()
            self.update()

    def pick_dimension(self, pos):
        point = self.sketch_point(pos, False)
        if self.pending_dimension is not None:
            index, edge = self.pending_dimension
            self.pending_dimension = None
            self.dimension_requested.emit(index, edge, point)
            self.sketch_notice.emit("Select a sketch line or circle to dimension.")
        else:
            hit = model.closest_sketch_edge(self.draft["profiles"], point, 9 / self.scale, dimensionable=True)
            if hit is None:
                return
            self.sketch_selection, self.sketch_edge = hit
            if self.draft["profiles"][hit[0]]["type"] == "circle":
                self.sketch_edge = None
            self.pending_dimension = hit
            self.hover = point
            self.sketch_notice.emit("Click where you want to place the dimension.")
        self.update()

    def mouseDoubleClickEvent(self, event):
        if self.draft is not None and event.button() == Qt.MouseButton.LeftButton and self.tool == "Select":
            for rect, index, dimension in reversed(self.dimension_labels):
                if rect.contains(event.position()):
                    self.cancel_sketch_drag()
                    self.dimension_requested.emit(index, dimension["edge"], dimension["label"])
                    return
        super().mouseDoubleClickEvent(event)

    def wheelEvent(self, event):
        before = self.scale
        self.scale = min(500, max(0.05, before * 1.15 ** (event.angleDelta().y() / 120)))
        relative = event.position() - QPointF(self.width() / 2, self.height() / 2)
        self.offset = relative - (relative - self.offset) * (self.scale / before)
        self.update()
