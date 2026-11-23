"""Position straight galleries and vertical shafts relative to a main tunnel."""

from dataclasses import dataclass
from math import atan2, ceil, isfinite

import numpy as np
from shapely.geometry import Point

from .loft import SectionKeyframe


GALLERY_TYPES = {"inclined_gallery", "equipment_gallery", "branch"}
SHAFT_TYPES = {"escape_shaft", "ventilation_shaft"}


def _vector(values, size, field):
    array = np.asarray(values, dtype=float)
    if array.shape != (size,) or not np.isfinite(array).all():
        raise ValueError(f"{field}: expected {size} finite numbers")
    return array


@dataclass(frozen=True)
class StraightAxis:
    name: str
    start: np.ndarray
    end: np.ndarray
    vertical: bool

    @property
    def length(self):
        delta = self.end - self.start
        return abs(delta[2]) if self.vertical else np.linalg.norm(delta[:2])

    def at(self, station):
        delta = self.end - self.start
        heading = atan2(delta[1], delta[0])
        return self.start + (station / self.length) * delta, heading

    def displayed_station(self, station):
        return station

    def sample(self, max_step, section_stations=()):
        # A shaft is a constant vertical extrusion and needs no intermediate rings.
        length = self.length
        limits = sorted({0.0, length, *(s for s in section_stations if 1e-7 < s < length - 1e-7)})
        positions = [limits[0]]
        for start, end in zip(limits, limits[1:]):
            count = 1 if self.vertical else ceil((end - start) / max_step)
            positions.extend(np.linspace(start, end, count + 1)[1:])
        points, headings = zip(*(self.at(station) for station in positions))
        return np.array(positions), np.array(points), np.array(headings)


@dataclass(frozen=True)
class Connection:
    parent: str
    station: float
    center: np.ndarray
    extents: np.ndarray


def place_auxiliary(spec, parent_alignment, parent_limits):
    kind = spec["type"]
    if kind not in GALLERY_TYPES | SHAFT_TYPES:
        raise ValueError(f"Unknown auxiliary type {kind!r}")
    settings = spec["connection"]
    station = float(settings["station"])
    if not parent_limits[0] <= station <= parent_limits[-1]:
        raise ValueError(f"{spec['name']}: connection station must be within its main tunnel")
    point, bearing = parent_alignment.at(station, from_left=station == parent_limits[-1])
    lateral, elevation = _vector(settings.get("offset_m", [0, 0]), 2, "offset_m")
    start = point + [np.sin(bearing) * lateral, -np.cos(bearing) * lateral, elevation]
    extents = _vector(settings["allow_overlap_box_m"], 3, "allow_overlap_box_m")
    if np.any(extents <= 0):
        raise ValueError("allow_overlap_box_m: extents must be positive")

    vertical = kind in SHAFT_TYPES
    if vertical:
        if "end_enz_m" in spec or {"section", "sections", "datum", "snapshot_index", "outer_boundary"} & spec.keys():
            raise ValueError("Shafts use end_elevation_m and inner/outer diameters")
        end = _vector([*start[:2], spec["end_elevation_m"]], 3, "shaft endpoint")
    else:
        if {"end_elevation_m", "inner_diameter_m", "outer_diameter_m"} & spec.keys():
            raise ValueError("Galleries and branches use end_enz_m and section inputs")
        end = _vector(spec["end_enz_m"], 3, "end_enz_m")
    axis = StraightAxis(spec["name"], start, end, vertical)
    if not isfinite(axis.length) or axis.length <= 1e-7:
        raise ValueError(f"{axis.name}: an auxiliary must have positive station length")
    return axis, Connection(settings["tunnel"], station, start, extents)


def shaft_section(spec):
    inner, outer = float(spec["inner_diameter_m"]), float(spec["outer_diameter_m"])
    if not (isfinite(outer) and 1e-7 < inner < outer):
        raise ValueError("Shaft diameters must be finite, positive, and inner < outer")
    return SectionKeyframe(0.0, Point(0, 0).buffer(inner / 2, quad_segs=24),
                           Point(0, 0).buffer(outer / 2, quad_segs=24), 0.0)
