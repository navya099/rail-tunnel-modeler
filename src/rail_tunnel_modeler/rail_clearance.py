"""Build and check railway gauges, vehicle envelopes, and facilities."""

from dataclasses import dataclass
from math import isfinite

import manifold3d
import numpy as np
import trimesh
from shapely.affinity import rotate, translate
from shapely.geometry import Polygon

from .model import CollisionIndex, check_horizontal_extent, compare_solids, sweep_profile


# Ignore residual volumes below the precision needed for a conceptual model.
VOLUME_TOLERANCE_M3 = 1e-6
RAIL_STYLE = {
    "gauge": ([68, 214, 161, 130], "Structure gauge"),
    "vehicle": ([110, 171, 249, 220], "Vehicle envelope"),
    "facility": ([244, 184, 89, 255], "Facility"),
}


@dataclass(frozen=True)
class RailShape:
    name: str
    kind: str
    tunnel: str
    mesh: trimesh.Trimesh


def track_range(spec, start, end):
    low = float(spec.get("start_station", start))
    high = float(spec.get("end_station", end))
    if not (isfinite(low) and isfinite(high) and start - 1e-7 <= low < high <= end + 1e-7
            and min(end, high) - max(start, low) > 1e-7):
        raise ValueError(f"{spec['name']}: clearance stations must be within the modeled tunnel interval")
    return max(start, low), min(end, high)


def _finite_vector(values, size, field):
    array = np.asarray(values, dtype=float)
    if array.shape != (size,) or not np.isfinite(array).all():
        raise ValueError(f"{field}: expected {size} finite numbers")
    return array


def _profile(spec):
    points = np.asarray(spec["outline"], dtype=float)
    if points.ndim != 2 or points.shape[0] < 3 or points.shape[1] != 2 or not np.isfinite(points).all():
        raise ValueError("outline: expected at least three finite [right, height] points")
    polygon = Polygon(points)
    if not polygon.is_valid or polygon.area <= 0:
        raise ValueError("outline: expected a simple polygon with positive area")
    cant = float(spec.get("cant_deg", 0))
    if not isfinite(cant) or abs(cant) >= 90:
        raise ValueError("cant_deg must be finite and between -90 and 90 degrees")
    return rotate(polygon, cant, origin=(0, 0))


def track_shapes(spec, tunnel, frame, keyframes):
    stations, _, headings = frame
    start, end = track_range(spec, stations[0], stations[-1])
    # Reuse the host's rings so the profile follows the same sampled alignment.
    selected = (stations >= start - 1e-7) & (stations <= end + 1e-7)
    centers = tunnel.centers[selected].copy()
    stations, headings = stations[selected], headings[selected]
    if len(stations) < 2:
        raise ValueError(f"{spec['name']}: clearance interval needs two distinct section rings")
    right = float(spec.get("track_offset_m", 0))
    height = float(spec.get("vertical_track_offset_m", 0))
    if not (isfinite(right) and isfinite(height)):
        raise ValueError("Track offsets must be finite")
    rail_offsets = [item.rail_offset_y for item in keyframes]
    if not all(map(isfinite, rail_offsets)):
        raise ValueError(f"{spec['name']}: section RL must be finite")
    centers[:, 2] += height + np.interp(stations, [item.station for item in keyframes], rail_offsets)

    profile_types = ["gauge", "vehicle"] if "vehicle" in spec else ["gauge"]
    shapes = []
    for kind in profile_types:
        polygon = translate(_profile(spec[kind]), xoff=right)
        check_horizontal_extent(spec["name"], tunnel.alignment, stations, polygon.bounds[0], polygon.bounds[2])
        shapes.append(RailShape(spec["name"], kind, tunnel.name, sweep_profile(polygon, centers, headings)))
    return shapes


def facility_shape(spec, tunnel, origin):
    station = float(spec["station"])
    if not tunnel.stations[0] - 1e-7 <= station <= tunnel.stations[-1] + 1e-7:
        raise ValueError(f"{spec['name']}: facility station must be within its tunnel")
    station = np.clip(station, tunnel.stations[0], tunnel.stations[-1])
    point, bearing = tunnel.alignment.at(station, from_left=station == tunnel.stations[-1])
    right, height = _finite_vector(spec.get("offset_m", [0, 0]), 2, "offset_m")
    size = _finite_vector(spec["size_m"], 3, "size_m")
    if np.any(size <= 0):
        raise ValueError("size_m: length, width and height must be positive")
    forward = np.array([np.cos(bearing), np.sin(bearing), 0])
    left = np.array([-forward[1], forward[0], 0])
    transform = np.eye(4)
    transform[:3, :2] = np.column_stack((forward, left))
    transform[:3, 3] = point - origin - right * left + [0, 0, height]
    return RailShape(spec["name"], "facility", tunnel.name, trimesh.creation.box(size, transform=transform))


def _manifold(mesh):
    result = manifold3d.Manifold(manifold3d.Mesh64(
        np.array(mesh.vertices, dtype=np.float64, order="C"),
        np.array(mesh.faces, dtype=np.uint64, order="C"),
    ))
    if result.status() != manifold3d.Error.NoError or result.is_empty():
        raise ValueError("Rail clearance check requires valid closed models")
    return result


def _outside(gauge, voids):
    remainder = manifold3d.Manifold.batch_boolean([_manifold(gauge), *voids], manifold3d.OpType.Subtract)
    if remainder.status() != manifold3d.Error.NoError:
        raise ValueError("Could not compare the gauge to the tunnel inner volumes")
    volume = remainder.volume()
    if volume <= VOLUME_TOLERANCE_M3:
        return 0.0, None
    mesh = remainder.to_mesh64()
    triangles = mesh.vert_properties[mesh.tri_verts, :3]
    areas = np.linalg.norm(np.cross(triangles[:, 1] - triangles[:, 0],
                                   triangles[:, 2] - triangles[:, 0]), axis=1)
    return volume, triangles[areas.argmax()].mean(axis=0)


def _location(tunnel, point, origin):
    station, displayed = tunnel.station_at(point)
    return {
        "internal_station_m": station,
        "displayed_station_m": displayed,
        "point_enz_m": (point + origin).tolist(),
    }


def check_rail_shapes(shapes, tunnels, origin):
    """Check gauge containment and all same-tunnel envelope/facility pairs."""
    voids = {}
    facilities = {}
    for shape in shapes:
        if shape.kind == "facility":
            facilities.setdefault(shape.tunnel, []).append((shape.name, CollisionIndex(shape.mesh)))

    results = []
    for shape in shapes:
        if shape.kind == "facility":
            continue
        tunnel = tunnels[shape.tunnel]
        info = {"tunnel": shape.tunnel, "track": shape.name, "envelope": shape.kind}
        if shape.kind == "gauge":
            if shape.tunnel not in voids:
                voids[shape.tunnel] = [_manifold(part) for part in tunnel.inner]
            volume, point = _outside(shape.mesh, voids[shape.tunnel])
            results.append({
                **info, "check": "gauge_within_tunnel",
                "result": "clear" if point is None else "intrusion",
                "outside_volume_m3": volume,
                "location": None if point is None else _location(tunnel, point, origin),
            })

        same_tunnel = facilities.get(shape.tunnel, [])
        if not same_tunnel:
            continue
        envelope = CollisionIndex(shape.mesh)
        for name, facility in same_tunnel:
            status, distance, points = compare_solids(envelope, facility)
            results.append({
                **info, "check": "facility_clearance", "facility": name,
                "result": "clear" if status == "clear" else "intrusion",
                "minimum_clearance_m": distance,
                "location": _location(tunnel, np.asarray(points[0]), origin),
            })
    return results


def rail_intrusion_points(report):
    origin = np.asarray(report["model_origin_enz_m"])
    return [np.asarray(check["location"]["point_enz_m"]) - origin
            for check in report.get("rail_checks", []) if check["result"] == "intrusion"]
