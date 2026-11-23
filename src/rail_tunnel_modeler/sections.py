"""Adapt the existing 2D section calculator to closed conceptual boundaries."""

import json
from math import dist, isfinite

from shapely.geometry import Polygon

from .section_core.constants import TunnelType
from .section_core.geometry import (
    arc_to_linestring,
    compute_box_geometry,
    compute_geometry_from_arch,
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


def boundaries(params):
    if params.kind == TunnelType.BOX:
        geometry = compute_box_geometry(params)
        inner, outer = geometry.inner, geometry.outer
    elif params.kind == TunnelType.NATM:
        if not isfinite(params.ts_slab) or params.ts_slab < 0:
            raise ValueError("NATM floor thickness must be nonnegative and finite")
        arch = ArchParams(R1=params.R1, top_angle=params.top_angle, R2=params.R2,
                          r2_angle=params.r2_angle, R1_Y=params.R1_Y)
        geometry = compute_geometry_from_arch(arch, FL=params.FL, ts=params.ts,
                                              ts_slab=params.ts_slab, show_lining=True)
        inner, outer = geometry.polygon, _natm_outer(geometry)
        if any(abs(point[1] - (geometry.left_end[1] - params.ts_slab)) > 1e-7
               for point in geometry.lining_bottom_line):
            raise ValueError("NATM outer arcs do not reach the specified floor level")
    else:
        raise ValueError(f"{params.kind.value}: this milestone supports single NATM and Box")
    if (inner.is_empty or not inner.is_valid or not outer.is_valid
            or inner.area <= 0 or outer.area <= 0
            or not outer.buffer(1e-8).covers(inner)):
        raise ValueError("Section requires valid inner and outer boundaries with inner inside outer")
    return inner, outer
