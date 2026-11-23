"""Adapt the existing 2D section calculator to closed conceptual boundaries."""

import json
from math import dist, isfinite

from shapely import get_coordinates
from shapely.geometry import MultiPoint, MultiPolygon, Polygon

from .section_core.constants import TunnelType
from .section_core.geometry import (
    arc_to_linestring,
    compute_box_geometry,
    compute_geometry_from_arch,
    compute_multi_arch,
)
from .section_core.models import ArchParams
from .section_core.snapshot import SnapshotStore


def read_section(path, snapshot_index):
    data = json.loads(path.read_text(encoding="utf-8-sig"))
    if isinstance(data, list):
        if not 0 <= snapshot_index < len(data):
            raise ValueError(f"{path}: snapshot index {snapshot_index} is out of range")
        data = data[snapshot_index]["payload"]
    elif snapshot_index != 0:
        raise ValueError(f"{path}: snapshot index applies to a snapshot list only")
    return SnapshotStore.deserialize(data)


def _natm_outer(geometry):
    if geometry.lining_bottom_line is None:
        raise ValueError("NATM outer boundary requires a positive side thickness")
    start, end = geometry.lining_bottom_line
    ring = [start]
    for spec in (geometry.lining_left_arc, geometry.lining_top_arc, geometry.lining_right_arc):
        points = list(arc_to_linestring(*spec).coords)
        if dist(ring[-1], points[0]) > dist(ring[-1], points[-1]):
            points.reverse()
        if dist(ring[-1], points[0]) > 1e-7:
            raise ValueError("NATM outer arcs do not meet")
        ring.extend(points[1:])
    if dist(ring[-1], end) > 1e-7:
        raise ValueError("NATM outer right arc does not meet the floor")
    return Polygon(ring)


def _arch_boundaries(arch, fl, ts, ts_slab):
    if not isfinite(ts) or ts <= 0:
        raise ValueError("NATM outer boundary requires a positive side thickness")
    if not isfinite(ts_slab) or ts_slab < 0:
        raise ValueError("NATM floor thickness must be nonnegative and finite")
    geometry = compute_geometry_from_arch(arch, FL=fl, ts=ts, ts_slab=ts_slab, show_lining=True)
    outer = _natm_outer(geometry)
    if any(abs(point[1] - (geometry.left_end[1] - ts_slab)) > 1e-7
           for point in geometry.lining_bottom_line):
        raise ValueError("NATM outer arcs do not reach the specified floor level")
    return geometry.polygon, outer


def boundaries(params, outer_boundary=None):
    if params.kind == TunnelType.BOX:
        geometry = compute_box_geometry(params)
        inner, outer = geometry.inner, geometry.outer
    elif params.kind == TunnelType.NATM:
        arch = ArchParams(R1=params.R1, top_angle=params.top_angle, R2=params.R2,
                          r2_angle=params.r2_angle, R1_Y=params.R1_Y)
        inner, outer = _arch_boundaries(arch, params.FL, params.ts, params.ts_slab)
    else:
        count = 2 if params.kind == TunnelType.NATM_2ARCH else 3
        if len(params.arches) != count:
            raise ValueError(f"{params.kind.value}: expected {count} arch definitions")
        layout = compute_multi_arch(params).layout
        parts, envelopes = zip(*(_arch_boundaries(a, params.FL, a.ts, a.ts_slab) for a in layout))
        inner = MultiPolygon(parts)
        if not inner.is_valid:
            raise ValueError(f"{params.kind.value}: arch interiors overlap or share an edge; check spacing")
        outer = MultiPoint([xy for poly in envelopes for xy in poly.exterior.coords]).convex_hull
    if outer_boundary is not None:
        if any(len(point) != 2 or not all(map(isfinite, point)) for point in outer_boundary):
            raise ValueError("outer_boundary must contain finite [x, y] points")
        outer = Polygon(outer_boundary)
    if (inner.is_empty or not inner.is_valid or not outer.is_valid
            or not all(map(isfinite, get_coordinates(inner).flat))
            or not all(map(isfinite, get_coordinates(outer).flat))
            or inner.area <= 0 or outer.area <= 0
            or not outer.buffer(1e-8).covers(inner)):
        raise ValueError("Section requires valid inner and outer boundaries with inner inside outer")
    return inner, outer
