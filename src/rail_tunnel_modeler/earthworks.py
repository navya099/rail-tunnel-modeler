"""Build cut slopes along a sampled alignment and intersect them with terrain."""

from dataclasses import dataclass
from math import isfinite

import numpy as np
import trimesh

from .model import check_horizontal_extent
from .terrain_profile import first_daylight


@dataclass(frozen=True)
class EarthworkCut:
    frame: tuple[np.ndarray, np.ndarray, np.ndarray]
    toes: np.ndarray  # One row per station: left offset, right offset, level offset.
    max_run: float
    sides: dict[str, tuple[np.ndarray, np.ndarray]]

    @property
    def corridor(self):
        return self.frame[1], float(np.abs(self.toes[:, :2]).max()) + self.max_run


def positive_dimension(value, field):
    value = float(value)
    if not isfinite(value) or value <= 0:
        raise ValueError(f"{field} must be positive and finite")
    return value


def _slope_profile(hv, benches, limit):
    distances, rises = [0.0], [0.0]
    for height, width in benches:
        run = distances[-1] + (height - rises[-1]) * hv
        if run >= limit:
            break
        distances.extend([run, min(run + width, limit)])
        rises.extend([height, height])
        if distances[-1] == limit:
            return np.array(distances), np.array(rises)
    rises.append(rises[-1] + (limit - distances[-1]) / hv)
    distances.append(limit)
    return np.array(distances), np.array(rises)


def prepare_earthwork(spec, frame, toes):
    max_run = positive_dimension(spec["max_run_m"], "max_run_m")
    benches = [(positive_dimension(item["rise_m"], "bench rise"),
                positive_dimension(item["width_m"], "bench width"))
               for item in spec.get("benches", [])]
    if any(a[0] >= b[0] for a, b in zip(benches, benches[1:])):
        raise ValueError("Bench rises must increase from the toe")
    ratios = spec["slope_hv"]
    sides = {
        side: _slope_profile(positive_dimension(ratios[side], f"{side} horizontal/vertical slope ratio"),
                             benches, max_run)
        for side in ("left", "right")
    }
    return EarthworkCut(frame, toes, max_run, sides)


def _cut_surface(rows, right_side):
    if len(rows) < 2:
        return None
    template = next((row for row in rows if row is not None), None)
    if template is None:
        return None
    width = len(template)
    vertices, faces = [], []
    preceding = None
    for row in rows:
        if row is None:
            preceding = None
            continue
        current = len(vertices) * width
        if preceding is not None:
            a = preceding + np.arange(width - 1)
            b, c = a + 1, current + np.arange(width - 1)
            faces.extend([np.column_stack((a, c, b)), np.column_stack((b, c, c + 1))])
        vertices.append(row)
        preceding = current
    if not faces:
        return None
    faces = np.vstack(faces)
    if right_side:
        faces = faces[:, ::-1]
    mesh = trimesh.Trimesh(vertices=np.vstack(vertices), faces=faces, process=True, validate=True)
    mesh.remove_unreferenced_vertices()
    return mesh if len(mesh.faces) else None


def _slope(cut, side, alignment, terrain, origin, label):
    stations, centers, headings = cut.frame
    distances, rises = cut.sides[side]
    sign = 1 if side == "right" else -1
    rows, report = [], []
    side_index = 1 if sign == 1 else 0
    toe_offsets = cut.toes[:, side_index]
    limits = [float(toe_offsets.min()), float(toe_offsets.max())]
    for station, center, heading, lateral, level in zip(
        stations, centers - origin, headings, toe_offsets, cut.toes[:, 2]
    ):
        right = np.array([np.sin(heading), -np.cos(heading)])
        direction = sign * right
        toe = center + [*(right * lateral), level]
        spans = terrain.intervals(toe, direction, cut.max_run)
        status, run = first_daylight(spans, distances, rises, toe[2])
        point = None
        if run is not None:
            offsets = distances.clip(max=run)
            row = toe + np.column_stack((offsets[:, None] * direction, np.interp(offsets, distances, rises)))
            rows.append(row)
            point = (row[-1] + origin).tolist()
            edge = lateral + sign * run
            limits[0] = min(limits[0], edge)
            limits[1] = max(limits[1], edge)
        else:
            rows.append(None)
        report.append({
            "internal_station_m": float(station),
            "displayed_station_m": float(alignment.displayed_station(station)),
            "result": status,
            "point_enz_m": point,
        })
    check_horizontal_extent(label, alignment, stations, *limits)
    return _cut_surface(rows, sign == 1), report


def cut_slopes(cut, alignment, terrain, origin, label):
    meshes, report = {}, {}
    for side in cut.sides:
        mesh, sections = _slope(cut, side, alignment, terrain, origin, label)
        report[side] = sections
        if mesh is not None:
            meshes[side] = mesh
    return meshes, report


def formation_surface(cut, origin):
    _, centers, headings = cut.frame
    centers = centers - origin
    right = np.column_stack((np.sin(headings), -np.cos(headings), np.zeros(len(headings))))
    centers[:, 2] += cut.toes[:, 2]
    rows = centers[:, None, :] + cut.toes[:, :2, None] * right[:, None, :]
    return _cut_surface(rows, right_side=True)
