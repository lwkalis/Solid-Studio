"""Persistent feature inputs and deterministic Open CASCADE recomputation."""

from dataclasses import dataclass, field
import json
import math
import os
from pathlib import Path
import tempfile

import cadquery as cq


PLANES = {
    "XY": ((1, 0, 0), (0, 1, 0), (0, 0, 1)),
    "XZ": ((1, 0, 0), (0, 0, 1), (0, -1, 0)),
    "YZ": ((0, 1, 0), (0, 0, 1), (1, 0, 0)),
}


def sketch_frame(sketch):
    if "frame" in sketch:
        return sketch["frame"]
    return ((0, 0, 0), *PLANES[sketch["plane"]])


def face_support(body, face):
    center = face.Center()
    normal = face.normalAt()
    return {"body": body, "point": list(center.toTuple()), "normal": list(normal.toTuple())}


def resolve_sketch(sketch, bodies):
    support = sketch.get("support")
    if support is None:
        return sketch
    normal = cq.Vector(*support["normal"])
    reference = cq.Vector(*support["point"])
    candidates = []
    for face in bodies[support["body"]].Faces():
        if face.geomType() == "PLANE" and face.normalAt().dot(normal) > 1 - 1e-8:
            center = face.Center()
            candidates.append(((center - reference).Length, center.dot(normal)))
    if not candidates:
        raise ValueError("The planar face supporting this sketch is unavailable.")
    candidates.sort()
    _, offset = candidates[0]
    if len(candidates) > 1 and abs(candidates[1][0] - candidates[0][0]) < 1e-6 and abs(candidates[1][1] - offset) > 1e-6:
        raise ValueError("The sketch support matches multiple planes.")
    axis = cq.Vector(0, 0, 1) if abs(normal.z) < 0.9 else cq.Vector(1, 0, 0)
    right = (axis - normal.multiply(axis.dot(normal))).normalized()
    up = normal.cross(right)
    origin = reference + normal.multiply(offset - reference.dot(normal))
    return sketch | {"frame": [v.toTuple() for v in (origin, right, up, normal)]}


def world(sketch, x, y):
    origin, a, b, _ = sketch_frame(sketch)
    return cq.Vector(*(o + u * x + v * y for o, u, v in zip(origin, a, b)))


def arc_geometry(points):
    if len(points) != 3:
        return None
    (x, y), (u, v), (a, b) = points
    ux, uy, ax, ay = u - x, v - y, a - x, b - y
    determinant = 2 * (ux * ay - uy * ax)
    if abs(determinant) < 1e-8:
        return None
    s, t = ux * ux + uy * uy, ax * ax + ay * ay
    cx = x + (s * ay - t * uy) / determinant
    cy = y + (ux * t - ax * s) / determinant
    start, mid, end = [math.atan2(py - cy, px - cx) for px, py in points]
    sweep = (end - start) % math.tau
    if (mid - start) % math.tau > sweep:
        sweep -= math.tau
    return (cx, cy), math.hypot(x - cx, y - cy), start, sweep


def sketch_path(profile):
    points = profile["points"]
    kind = profile["type"]
    if kind == "rectangle":
        (x, y), (u, v) = points
        return [(x, y), (u, y), (u, v), (x, v), (x, y)]
    if kind == "circle":
        (x, y), radius_point = points
        radius = math.dist((x, y), radius_point)
        points = [(x + radius * math.cos(i * math.tau / 64), y + radius * math.sin(i * math.tau / 64))
                  for i in range(64)]
        return points + points[:1]
    if kind in {"arc", "open_arc"}:
        geometry = arc_geometry(points)
        if geometry is None:
            return points
        (x, y), radius, start, sweep = geometry
        sampled = [points[0]] + [(x + radius * math.cos(start + sweep * i / 48),
                                  y + radius * math.sin(start + sweep * i / 48))
                                 for i in range(1, 48)] + [points[-1]]
        return sampled + sampled[:1] if kind == "arc" else sampled
    return points + points[:1] if kind == "polygon" else points


def closest_profile(profiles, point, tolerance):
    best = None
    for index, profile in enumerate(profiles):
        if profile.get("deleted"):
            continue
        path = sketch_path(profile)
        for a, b in zip(path, path[1:]):
            dx, dy = b[0] - a[0], b[1] - a[1]
            length = dx * dx + dy * dy
            t = max(0, min(1, ((point[0] - a[0]) * dx + (point[1] - a[1]) * dy) / length)) if length else 0
            distance = math.dist(point, (a[0] + t * dx, a[1] + t * dy))
            if distance <= tolerance and (best is None or distance <= best[0]):
                best = distance, index
    return best[1] if best is not None else None


def line_intersection(a, b, c, d):
    ux, uy = b[0] - a[0], b[1] - a[1]
    vx, vy = d[0] - c[0], d[1] - c[1]
    determinant = ux * vy - uy * vx
    if abs(determinant) < 1e-8:
        return None
    dx, dy = c[0] - a[0], c[1] - a[1]
    return (dx * vy - dy * vx) / determinant, (dx * uy - dy * ux) / determinant


def trim_line(profiles, index, pick):
    profile = profiles[index]
    if profile["type"] != "polyline" or len(profile["points"]) != 2:
        raise ValueError("Trim currently works on individual straight lines.")
    a, b = profile["points"]
    dx, dy = b[0] - a[0], b[1] - a[1]
    length = dx * dx + dy * dy
    picked = ((pick[0] - a[0]) * dx + (pick[1] - a[1]) * dy) / length
    cuts = {0.0, 1.0}
    for i, other in enumerate(profiles):
        if i == index or other.get("deleted") or other["type"] in {"circle", "arc", "open_arc"}:
            continue
        path = sketch_path(other)
        for c, d in zip(path, path[1:]):
            crossing = line_intersection(a, b, c, d)
            if crossing and 1e-8 < crossing[0] < 1 - 1e-8 and -1e-8 <= crossing[1] <= 1 + 1e-8:
                cuts.add(round(crossing[0], 9))
    cuts = sorted(cuts)
    if len(cuts) < 3:
        raise ValueError("The line has no crossing straight edge.")
    start, end = next((lo, hi) for lo, hi in zip(cuts, cuts[1:]) if picked <= hi or hi == 1)
    remaining = []
    for lo, hi in [(0, start), (end, 1)]:
        if hi - lo > 1e-8:
            remaining.append(profile | {"points": [[a[0] + dx * t, a[1] + dy * t] for t in (lo, hi)]})
    profiles[index] = remaining[0]
    profiles.extend(remaining[1:])


def project_lines(sketch, body):
    origin, right, up, _ = (cq.Vector(*v) for v in sketch_frame(sketch))
    seen = set()
    profiles = []
    for edge in body.Edges():
        if edge.geomType() != "LINE":
            continue
        points = [[round(delta.dot(axis), 7) for axis in (right, up)]
                  for delta in (edge.startPoint() - origin, edge.endPoint() - origin)]
        key = tuple(sorted(map(tuple, points)))
        if points[0] != points[1] and key not in seen:
            seen.add(key)
            profiles.append({"type": "polyline", "points": points, "construction": True})
    return profiles


def apply_line_rule(profile, rule, length=None):
    if profile["type"] != "polyline" or len(profile["points"]) != 2:
        raise ValueError("Select an individual straight line.")
    if rule not in {"Horizontal", "Vertical"}:
        raise ValueError("Choose a supported line constraint.")
    a, b = profile["points"]
    axis = 0 if rule == "Horizontal" else 1
    size = math.dist(a, b) if length is None else length
    end = a[:]
    end[axis] += math.copysign(size, b[axis] - a[axis])
    profile.update(points=[a, end], rule=rule)


def profile_wire(sketch, profile):
    if profile["type"] == "loop":
        edges = []
        for part in profile["segments"]:
            points = [world(sketch, *p) for p in part["points"]]
            if part["type"] == "open_arc":
                edges.append(cq.Edge.makeThreePointArc(*points))
            else:
                edges.extend(cq.Edge.makeLine(a, b) for a, b in zip(points, points[1:]))
        return cq.Wire.assembleEdges(edges)
    points = profile["points"]
    if profile["type"] == "circle":
        center, radius_point = points
        radius = math.dist(center, radius_point)
        if radius <= 1e-6:
            raise ValueError("The circle needs a positive radius.")
        return cq.Wire.makeCircle(radius, world(sketch, *center), cq.Vector(*sketch_frame(sketch)[3]))
    if profile["type"] == "arc":
        arc = cq.Edge.makeThreePointArc(*(world(sketch, *p) for p in points))
        closing = cq.Edge.makeLine(world(sketch, *points[-1]), world(sketch, *points[0]))
        return cq.Wire.assembleEdges([arc, closing])
    if profile["type"] == "rectangle":
        (x, y), (u, v) = points
        points = [(x, y), (u, y), (u, v), (x, v)]
    if len(points) < 3 or abs(sum(a[0] * b[1] - a[1] * b[0]
                                  for a, b in zip(points, points[1:] + points[:1]))) < 1e-6:
        raise ValueError("The profile needs a closed area.")
    return cq.Wire.makePolygon([world(sketch, *p) for p in points], close=True)


def closed_profiles(sketch):
    closed, open_paths = [], {}
    for i, p in enumerate(sketch["profiles"]):
        if p.get("deleted") or p.get("construction"):
            continue
        if p["type"] in {"polyline", "open_arc"}:
            open_paths[i] = p
        else:
            closed.append((i, p))
    # Join unbranched chains whose endpoints coincide, preserving curve types.
    def endpoint(point):
        return tuple(round(value, 6) for value in point)

    links = {}
    for i, p in open_paths.items():
        for point in (p["points"][0], p["points"][-1]):
            links.setdefault(endpoint(point), []).append(i)
    used = set()
    for i, p in open_paths.items():
        if i in used:
            continue
        start = endpoint(p["points"][0])
        end = endpoint(p["points"][-1])
        chain = {i}
        segments = [p]
        while end != start and len(links[end]) == 2:
            candidates = [j for j in links[end] if j not in chain]
            if len(candidates) != 1:
                break
            j = candidates[0]
            part = open_paths[j]
            if endpoint(part["points"][0]) != end:
                part = part | {"points": part["points"][::-1]}
            segments.append(part)
            chain.add(j)
            end = endpoint(part["points"][-1])
        if len(chain) > 1 and end == start and len(links[start]) == 2:
            used.update(chain)
            closed.append((i, {"type": "loop", "segments": segments}))
    return sorted(closed, key=lambda item: item[0])


@dataclass
class Evaluation:
    bodies: dict = field(default_factory=dict)
    sketches: dict = field(default_factory=dict)
    errors: dict = field(default_factory=dict)


def validate_shape(shape):
    if not shape.Solids() or not shape.isValid() or shape.Volume() <= 1e-8:
        raise ValueError("The operation did not produce a valid solid.")
    return shape


def extrude(sketch, distance, profile_index):
    if not math.isfinite(distance) or abs(distance) < 1e-6:
        raise ValueError("Specify a nonzero extrusion distance.")
    profiles = dict(closed_profiles(sketch))
    if profile_index not in profiles:
        raise ValueError("Select a closed, non-construction sketch profile.")
    wire = profile_wire(sketch, profiles[profile_index])
    direction = cq.Vector(*sketch_frame(sketch)[3]).multiply(distance)
    outer = cq.Face.makeFromWires(wire)
    nested = []
    for i, profile in profiles.items():
        if i == profile_index:
            continue
        inner = profile_wire(sketch, profile)
        # Subtract enclosed islands only. Boundaries that touch or cross need
        # a profile arrangement, which this simple region builder cannot infer.
        inner_face = cq.Face.makeFromWires(inner)
        overlap = outer.intersect(inner_face)
        if overlap.Area() > 0 and math.isclose(overlap.Area(), inner_face.Area(), rel_tol=1e-7):
            if inner_face.Area() < outer.Area() - 1e-7 and wire.distance(inner) > 1e-6:
                nested.append((inner, inner_face))
    holes = [inner for inner, face in nested if not any(
        other.Area() > face.Area() and math.isclose(other.intersect(face).Area(), face.Area(), rel_tol=1e-7)
        for _, other in nested)]
    return validate_shape(cq.Solid.extrudeLinear(wire, holes, direction))


def boolean(target, tool, operation):
    if operation not in {"Join", "Cut", "Intersect"}:
        raise ValueError("Unknown Boolean operation.")
    fn = {"Join": target.fuse, "Cut": target.cut, "Intersect": target.intersect}[operation]
    return validate_shape(fn(tool).clean())


def edge_set(shape, selection):
    edges = shape.Edges()
    if selection == "All edges":
        return edges
    if selection not in {"Top edges", "Bottom edges"}:
        raise ValueError("Unknown edge selection.")
    bounds = shape.BoundingBox()
    height = bounds.zmax if selection == "Top edges" else bounds.zmin
    return [e for e in edges if abs(e.BoundingBox().zmin - height) < 1e-5
            and abs(e.BoundingBox().zmax - height) < 1e-5]


def evaluate(features, cursor=None):
    state = Evaluation()
    for f in features[:cursor]:
        fid, kind = f["id"], f["kind"]
        try:
            if kind == "Sketch":
                sketch = resolve_sketch(f, state.bodies)
                for _, profile in closed_profiles(sketch):
                    if not cq.Face.makeFromWires(profile_wire(sketch, profile)).isValid():
                        raise ValueError("A sketch profile is self-intersecting or invalid.")
                state.sketches[fid] = sketch
            elif kind == "Extrude":
                solid = extrude(state.sketches[f["sketch"]], f["distance"], f["profile"])
                if f["operation"] == "New Body":
                    state.bodies[fid] = solid
                else:
                    state.bodies[f["target"]] = boolean(state.bodies[f["target"]], solid, f["operation"])
            elif kind == "Combine":
                target, tool = f["target"], f["tool"]
                if target == tool:
                    raise ValueError("Choose two different bodies.")
                state.bodies[target] = boolean(state.bodies[target], state.bodies[tool], f["operation"])
                if not f["keep_tool"]:
                    del state.bodies[tool]
            elif kind in {"Fillet", "Chamfer"}:
                shape = state.bodies[f["target"]]
                edges = edge_set(shape, f["edges"])
                if not edges:
                    raise ValueError("No edges matched that selection.")
                if not math.isfinite(f["size"]) or f["size"] <= 0:
                    raise ValueError("Specify a positive size.")
                result = shape.fillet(f["size"], edges) if kind == "Fillet" else shape.chamfer(f["size"], None, edges)
                state.bodies[f["target"]] = validate_shape(result)
            else:
                raise ValueError(f"Unknown feature type: {kind}.")
        except KeyError:
            state.errors[fid] = "An input sketch or body is unavailable at this point in the timeline."
        except Exception as exc:
            state.errors[fid] = str(exc) or "The modeling kernel could not complete this operation."
    return state


def save(path, features):
    path = Path(path)
    fd, staged = tempfile.mkstemp(dir=path.parent, prefix=".solidstudio-", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            json.dump({"version": 1, "features": features}, stream, indent=2, allow_nan=False)
            stream.write("\n")
        os.replace(staged, path)
    finally:
        Path(staged).unlink(missing_ok=True)


def load(path):
    with Path(path).open(encoding="utf-8") as stream:
        data = json.load(stream)
    if not isinstance(data, dict) or data.get("version") != 1 or not isinstance(data.get("features"), list):
        raise ValueError("This file is not a supported Solid Studio document.")
    features = data["features"]
    ids = set()
    for f in features:
        if not isinstance(f, dict) or not isinstance(f.get("id"), str) or not isinstance(f.get("name"), str):
            raise ValueError("The document contains an invalid feature.")
        if f["id"] in ids or f.get("kind") not in {"Sketch", "Extrude", "Combine", "Fillet", "Chamfer"}:
            raise ValueError("The document contains a duplicate or unsupported feature.")
        ids.add(f["id"])
    return features


def export_step(path, state):
    if not state.bodies:
        raise ValueError("Create a solid body before exporting.")
    cq.exporters.export(cq.Compound.makeCompound(list(state.bodies.values())), str(path), exportType="STEP")
