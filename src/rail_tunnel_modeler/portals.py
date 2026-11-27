"""Build tunnel headwalls and approach cut slopes at tunnel ends."""

from dataclasses import dataclass
from math import isfinite

import numpy as np
import trimesh
from shapely.affinity import translate
from shapely.geometry import Polygon, box

from .model import check_horizontal_extent, sweep_profile
from .terrain_profile import TerrainSections, first_daylight


@dataclass(frozen=True)
class ApproachCut:
    frame: tuple[np.ndarray, np.ndarray, np.ndarray]
    half_width: float
    toe_offset: float
    max_run: float
    sides: dict[str, tuple[np.ndarray, np.ndarray]]

    @property
    def corridor(self):
        return self.frame[1], self.half_width + self.max_run


@dataclass(frozen=True)
class PortalInput:
    name: str
    tunnel: str
    end: str
    alignment: object
    station: float
    center: np.ndarray
    heading: float
    wall: Polygon
    thickness: float
    approach: ApproachCut | None


@dataclass(frozen=True)
class PortalModel:
    name: str
    wall: trimesh.Trimesh
    slopes: dict[str, trimesh.Trimesh]
    report: dict


def _positive(value, field):
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


def _prepare_cut(spec, alignment, station, end, max_step):
    length = _positive(spec["length_m"], "approach_cut.length_m")
    half_width = _positive(spec["bottom_width_m"], "approach_cut.bottom_width_m") / 2
    max_run = _positive(spec["max_run_m"], "approach_cut.max_run_m")
    offset = float(spec.get("toe_offset_m", 0.0))
    if not isfinite(offset):
        raise ValueError("approach_cut.toe_offset_m must be finite")
    benches = [(_positive(item["rise_m"], "bench rise"), _positive(item["width_m"], "bench width"))
               for item in spec.get("benches", [])]
    if any(a[0] >= b[0] for a, b in zip(benches, benches[1:])):
        raise ValueError("Bench rises must increase from the toe")
    start, finish = (station - length, station) if end == "start" else (station, station + length)
    frame = alignment.sample(start, finish, max_step)
    ratios = spec["slope_hv"]
    sides = {
        side: _slope_profile(_positive(ratios[side], f"{side} horizontal/vertical slope ratio"), benches, max_run)
        for side in ("left", "right")
    }
    return ApproachCut(frame, half_width, offset, max_run, sides)


def prepare_portal(spec, main_by_name, max_step):
    name, tunnel, end = spec["name"], spec["tunnel"], spec["end"]
    if tunnel not in main_by_name:
        raise ValueError(f"{name}: portal tunnel must name a main tunnel")
    if end not in {"start", "end"}:
        raise ValueError(f"{name}: portal end must be start or end")
    alignment, frame, keyframes = main_by_name[tunnel]
    index = 0 if end == "start" else -1
    stations, centers, headings = frame
    section = keyframes[index]
    margin = _positive(spec.get("wall_margin_m", 0.5), "wall_margin_m")
    thickness = _positive(spec.get("wall_thickness_m", 0.8), "wall_thickness_m")
    x0, y0, x1, y1 = section.outer.bounds
    outer = box(x0 - margin, y0 - margin, x1 + margin, y1 + margin)
    wall = translate(outer.difference(section.inner), yoff=-section.datum_y)
    if wall.geom_type != "Polygon" or not wall.is_valid or not len(wall.interiors):
        raise ValueError(f"{name}: unable to form a headwall with the tunnel openings")
    approach = (_prepare_cut(spec["approach_cut"], alignment, stations[index], end, max_step)
                if "approach_cut" in spec else None)
    if approach is not None:
        approach_heading = approach.frame[2][-1 if end == "start" else 0]
        turn = approach_heading - headings[index]
        if abs((turn + np.pi) % (2 * np.pi) - np.pi) > 1e-3:
            raise ValueError(f"{name}: alignment changes direction abruptly at the portal")
    return PortalInput(name, tunnel, end, alignment, stations[index], centers[index],
                       headings[index], wall, thickness, approach)


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


def _slope(approach, side, alignment, terrain, origin):
    stations, centers, headings = approach.frame
    distances, rises = approach.sides[side]
    sign = 1 if side == "right" else -1
    rows, report = [], []
    max_width = approach.half_width
    for station, center, heading in zip(stations, centers - origin, headings):
        direction = sign * np.array([np.sin(heading), -np.cos(heading)])
        toe = center + [*(direction * approach.half_width), approach.toe_offset]
        spans = terrain.intervals(toe, direction, approach.max_run)
        status, run = first_daylight(spans, distances, rises, toe[2])
        point = None
        if run is not None:
            offsets = distances.clip(max=run)
            row = toe + np.column_stack((offsets[:, None] * direction, np.interp(offsets, distances, rises)))
            rows.append(row)
            point = (row[-1] + origin).tolist()
            max_width = max(max_width, approach.half_width + run)
        else:
            rows.append(None)
        report.append({
            "internal_station_m": float(station),
            "displayed_station_m": float(alignment.displayed_station(station)),
            "result": status,
            "point_enz_m": point,
        })
    check_horizontal_extent("Approach cut", alignment, stations, 0 if sign == 1 else -max_width,
                            max_width if sign == 1 else 0)
    return _cut_surface(rows, sign == 1), report


def build_portals(inputs, terrain, origin):
    if any(item.approach is not None for item in inputs):
        if terrain is None:
            raise ValueError("A portal approach cut requires terrain")
        terrain_sections = TerrainSections(terrain.tiles)
    models = []
    for item in inputs:
        direction = -1 if item.end == "start" else 1
        shift = direction * item.thickness * np.array([np.cos(item.heading), np.sin(item.heading), 0])
        first = item.center - origin
        wall = sweep_profile(item.wall, np.array([first, first + shift]), np.repeat(item.heading, 2))
        slopes = {}
        report = {
            "name": item.name, "tunnel": item.tunnel, "end": item.end,
            "internal_station_m": float(item.station),
            "displayed_station_m": float(item.alignment.displayed_station(item.station)),
            "point_enz_m": item.center.tolist(),
        }
        if item.approach is not None:
            approach_report = {}
            for side in item.approach.sides:
                slope, sections = _slope(item.approach, side, item.alignment, terrain_sections, origin)
                approach_report[side] = sections
                if slope is not None:
                    slopes[side] = slope
            report["approach_cut"] = approach_report
        models.append(PortalModel(item.name, wall, slopes, report))
    return models
