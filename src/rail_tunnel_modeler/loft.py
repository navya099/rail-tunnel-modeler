"""Match section boundaries and interpolate between station keyframes."""

from bisect import bisect_right
from dataclasses import dataclass
from functools import cache

import numpy as np
from shapely import orient_polygons, set_precision
from shapely.affinity import translate
from shapely.geometry import MultiPolygon, Polygon


@dataclass(frozen=True)
class SectionKeyframe:
    station: float
    inner: Polygon | MultiPolygon
    outer: Polygon
    datum_y: float


def _ordered_rings(polygon):
    rings = [polygon.exterior, *sorted(polygon.interiors, key=lambda ring: ring.bounds[:2])]
    ordered = []
    for ring in rings:
        points = np.asarray(ring.coords)[:-1]
        # Anchor correspondence to the leftmost vertex at the bottom of each ring.
        start = np.lexsort((points[:, 0], points[:, 1]))[0]
        ordered.append(np.roll(points, -start, axis=0))
    return ordered


def _section_parts(keyframe):
    inner_parts = keyframe.inner.geoms if isinstance(keyframe.inner, MultiPolygon) else [keyframe.inner]
    inner_parts = sorted(inner_parts, key=lambda part: (part.centroid.x, part.centroid.y))
    parts = [keyframe.outer, *inner_parts]
    prepared = []
    for part in parts:
        polygon = orient_polygons(set_precision(translate(part, yoff=-keyframe.datum_y), 1e-8))
        if polygon.is_empty or polygon.geom_type != "Polygon":
            raise ValueError(f"Section at {keyframe.station:g} m collapsed or split at mesh precision")
        prepared.append(_ordered_rings(polygon))
    return prepared


def _match_rings(rings):
    if all(np.array_equal(rings[0], ring) for ring in rings[1:]):
        return rings
    closed_rings, parameters = [], []
    for ring in rings:
        closed = np.vstack((ring, ring[0]))
        lengths = np.r_[0, np.cumsum(np.linalg.norm(np.diff(closed, axis=0), axis=1))]
        closed_rings.append(closed)
        parameters.append(lengths / lengths[-1])
    fractions = np.unique(np.concatenate(parameters))
    # Merge fractions that only differ due to floating point rounding.
    fractions = fractions[np.r_[True, np.diff(fractions) > 1e-12]]
    fractions = fractions[fractions < 1 - 1e-12]
    return [np.column_stack([np.interp(fractions, u, ring[:, axis]) for axis in range(2)])
            for u, ring in zip(parameters, closed_rings)]


def _validate_transition(parts, station):
    polygons = [Polygon(rings[0], rings[1:]) for rings in parts]
    outer, inner_parts = polygons[0], polygons[1:]
    inner = MultiPolygon(inner_parts) if len(inner_parts) > 1 else inner_parts[0]
    if not outer.is_valid or not inner.is_valid or not outer.buffer(1e-8).covers(inner):
        raise ValueError(f"Section transition at {station:g} m has crossing rings or inner boundaries outside the outer boundary")


def section_samples(keyframes, stations):
    """Return samples grouped by part, starting with the outer boundary."""
    profiles = [_section_parts(keyframe) for keyframe in keyframes]
    topology = [len(part) for part in profiles[0]]
    if any([len(part) for part in profile] != topology for profile in profiles[1:]):
        raise ValueError("Section transitions require the same number of compartments and holes")

    for part_idx, ring_count in enumerate(topology):
        for ring_idx in range(ring_count):
            rings = _match_rings([profile[part_idx][ring_idx] for profile in profiles])
            for profile, ring in zip(profiles, rings):
                profile[part_idx][ring_idx] = ring

    positions = [keyframe.station for keyframe in keyframes]

    @cache
    def endpoints(index):
        parts = profiles[index]
        _validate_transition(parts, positions[index])
        return parts

    frames = []
    for station in stations:
        lower = bisect_right(positions, station) - 1
        if lower < 0 or lower == len(positions) - 1 or station == positions[lower]:
            frames.append(endpoints(max(0, lower)))
            continue
        fraction = (station - positions[lower]) / (positions[lower + 1] - positions[lower])
        parts = [[a + fraction * (b - a) for a, b in zip(start, end)]
                 for start, end in zip(endpoints(lower), endpoints(lower + 1))]
        _validate_transition(parts, station)
        frames.append(parts)
    return list(zip(*frames))
