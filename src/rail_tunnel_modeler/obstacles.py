"""Import external infrastructure and check it against tunnel outer volumes."""

from dataclasses import dataclass
from math import isfinite, radians

import numpy as np
import trimesh

from .model import CollisionIndex, compare_solids


OBSTACLE_KINDS = {"road_tunnel", "rail_tunnel", "utility", "structure"}
GEOMETRY_KINDS = {"box", "pipe", "glb"}
OBSTACLE_COLOR = [184, 156, 223, 220]


@dataclass(frozen=True)
class Obstacle:
    name: str
    kind: str
    required_clearance: float
    meshes: list[trimesh.Trimesh]


def obstacle_files(specs, folder):
    return {(folder / spec["glb"]["path"]).resolve() for spec in specs if "glb" in spec}


def _vector(values, field):
    array = np.asarray(values, dtype=float)
    if array.shape != (3,) or not np.isfinite(array).all():
        raise ValueError(f"{field}: expected three finite numbers")
    return array


def _clearance(value):
    value = float(value)
    if not isfinite(value) or value < 0:
        raise ValueError("Obstacle clearance must be finite and nonnegative")
    return value


def _box(spec, origin):
    center = _vector(spec["center_enz_m"], "center_enz_m") - origin
    size = _vector(spec["size_m"], "size_m")
    if np.any(size <= 0):
        raise ValueError("Obstacle box size must be positive")
    bearing = float(spec.get("bearing_deg", 0))
    if not isfinite(bearing):
        raise ValueError("bearing_deg must be finite")
    e, n = np.sin(radians(bearing)), np.cos(radians(bearing))
    transform = np.array([[e, -n, 0, center[0]],
                          [n, e, 0, center[1]],
                          [0, 0, 1, center[2]],
                          [0, 0, 0, 1]])
    return [trimesh.creation.box(extents=size, transform=transform)]


def _pipe(spec, origin):
    points = np.asarray(spec["points_enz_m"], dtype=float)
    if (points.ndim != 2 or points.shape[0] < 2 or points.shape[1] != 3
            or not np.isfinite(points).all()):
        raise ValueError("points_enz_m must contain at least two finite [east, north, elevation] points")
    diameter = float(spec["diameter_m"])
    if not isfinite(diameter) or diameter <= 0:
        raise ValueError("diameter_m must be finite and positive")
    points = points - origin
    offsets = np.diff(points, axis=0)
    lengths = np.linalg.norm(offsets, axis=1)
    if np.any(lengths <= 1e-7) or not np.isfinite(lengths).all():
        raise ValueError("Consecutive pipe points must be distinct")
    parts = []
    for start, offset, length in zip(points, offsets, lengths):
        pose = trimesh.geometry.align_vectors([0, 0, 1], offset / length)
        pose[:3, 3] = start + offset / 2
        parts.append(trimesh.creation.capsule(height=length, radius=diameter / 2,
                                             count=[24, 24], transform=pose))
    return parts


def _glb(spec, folder, origin, scenes):
    path = (folder / spec["path"]).resolve()
    if path.suffix.lower() != ".glb":
        raise ValueError("Obstacle mesh path must be a .glb file")
    translation = _vector(spec["origin_enz_m"], "origin_enz_m") - origin
    if path not in scenes:
        scenes[path] = trimesh.load_scene(path)
    scene = scenes[path]
    available = set(scene.graph.nodes_geometry)
    nodes = spec.get("nodes", sorted(available))
    if (not isinstance(nodes, list) or not nodes or len(set(nodes)) != len(nodes)
            or any(node not in available for node in nodes)):
        raise ValueError(f"{path.name}: nodes must select existing, distinct geometry nodes")

    # glTF (x, y, z) maps to project (east, elevation, south).
    to_enz = np.array([[1, 0, 0, translation[0]],
                       [0, 0, -1, translation[1]],
                       [0, 1, 0, translation[2]],
                       [0, 0, 0, 1]])
    parts = []
    for node in nodes:
        transform, key = scene.graph[node]
        source = scene.geometry[key]
        if (not isinstance(source, trimesh.Trimesh) or not np.isfinite(source.vertices).all()
                or not np.isfinite(transform).all()):
            raise ValueError(f"{path.name}: node {node!r} must be a finite triangle mesh")
        # Ignore materials and weld coincident positions that glTF may split for UVs.
        mesh = trimesh.Trimesh(vertices=source.vertices, faces=source.faces, process=True)
        mesh.apply_transform(to_enz @ transform)
        mesh.fix_normals(multibody=True)
        components = mesh.split(only_watertight=False, repair=False)
        if not len(components) or any(not component.is_volume for component in components):
            raise ValueError(f"{path.name}: node {node!r} requires closed solid meshes")
        parts.extend(components)
    return parts


def load_obstacles(specs, folder, origin, default_clearance=0):
    clearance = _clearance(default_clearance)
    names = set()
    scenes = {}
    obstacles = []
    for spec in specs:
        name, kind = spec["name"], spec["kind"]
        if not isinstance(name, str) or not name.strip() or name in names:
            raise ValueError("Obstacle names must be unique nonempty strings")
        if kind not in OBSTACLE_KINDS:
            raise ValueError(f"{name}: unknown obstacle kind {kind!r}")
        representations = GEOMETRY_KINDS & spec.keys()
        if len(representations) != 1:
            raise ValueError(f"{name}: specify exactly one of box, pipe, or glb")
        representation = representations.pop()
        if representation == "box":
            meshes = _box(spec["box"], origin)
        elif representation == "pipe":
            meshes = _pipe(spec["pipe"], origin)
        else:
            meshes = _glb(spec["glb"], folder, origin, scenes)
        obstacles.append(Obstacle(name, kind, _clearance(spec.get("required_clearance_m", clearance)), meshes))
        names.add(name)
    return obstacles


def _nearest(tunnel_index, components):
    first = tunnel_index.mesh.bounds
    bounds = np.asarray([component.mesh.bounds for component in components])
    deltas = np.maximum(0, np.maximum(first[0] - bounds[:, 1], bounds[:, 0] - first[1]))
    lower_bounds = np.linalg.norm(deltas, axis=1)
    closest = None
    for index in np.argsort(lower_bounds, kind="stable"):
        if closest is not None and lower_bounds[index] > closest[1] + 1e-8:
            break
        match = compare_solids(tunnel_index, components[index])
        if closest is None or match[1] < closest[1]:
            closest = match
        if closest[1] == 0:
            break
    return closest


def check_obstacles(tunnels, obstacles, origin):
    indexes = [(obstacle, [CollisionIndex(mesh) for mesh in obstacle.meshes]) for obstacle in obstacles]
    results = []
    for tunnel in tunnels:
        tunnel_index = CollisionIndex(tunnel.outer)
        for obstacle, components in indexes:
            status, distance, points = _nearest(tunnel_index, components)
            if status == "clear" and distance + 1e-8 < obstacle.required_clearance:
                status = "below_clearance"
            station, displayed = tunnel.station_at(points[0])
            results.append({
                "tunnel": tunnel.name, "alignment": tunnel.alignment.name,
                "obstacle": obstacle.name, "obstacle_kind": obstacle.kind,
                "result": status, "minimum_clearance_m": distance,
                "required_clearance_m": obstacle.required_clearance,
                "internal_station_m": station, "displayed_station_m": displayed,
                "tunnel_point_enz_m": (points[0] + origin).tolist(),
                "obstacle_point_enz_m": (points[1] + origin).tolist(),
            })
            if tunnel.vertical:
                results[-1]["station_basis"] = "shaft_vertical_distance"
    return results


def obstacle_witness_lines(report):
    origin = np.asarray(report["model_origin_enz_m"])
    return [np.array([check["tunnel_point_enz_m"], check["obstacle_point_enz_m"]]) - origin
            for check in report.get("obstacle_checks", []) if check["result"] != "clear"]
