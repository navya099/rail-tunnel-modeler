"""Build tunnel headwalls and approach cut slopes at tunnel ends."""

from dataclasses import dataclass
from math import isfinite

import numpy as np
import trimesh
from shapely.affinity import translate
from shapely.geometry import Polygon, box

from .earthworks import EarthworkCut, cut_slopes, positive_dimension, prepare_earthwork
from .model import sweep_profile


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
    approach: EarthworkCut | None


@dataclass(frozen=True)
class PortalModel:
    name: str
    wall: trimesh.Trimesh
    slopes: dict[str, trimesh.Trimesh]
    report: dict


def _prepare_cut(spec, alignment, station, end, max_step):
    length = positive_dimension(spec["length_m"], "approach_cut.length_m")
    half_width = positive_dimension(spec["bottom_width_m"], "approach_cut.bottom_width_m") / 2
    offset = float(spec.get("toe_offset_m", 0.0))
    if not isfinite(offset):
        raise ValueError("approach_cut.toe_offset_m must be finite")
    start, finish = (station - length, station) if end == "start" else (station, station + length)
    frame = alignment.sample(start, finish, max_step)
    toes = np.broadcast_to([-half_width, half_width, offset], (len(frame[0]), 3))
    return prepare_earthwork(spec, frame, toes)


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
    margin = positive_dimension(spec.get("wall_margin_m", 0.5), "wall_margin_m")
    thickness = positive_dimension(spec.get("wall_thickness_m", 0.8), "wall_thickness_m")
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


def build_portals(inputs, terrain, origin):
    if terrain is None and any(item.approach is not None for item in inputs):
        raise ValueError("A portal approach cut requires terrain")
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
            slopes, report["approach_cut"] = cut_slopes(item.approach, item.alignment, terrain, origin, item.name)
        models.append(PortalModel(item.name, wall, slopes, report))
    return models
