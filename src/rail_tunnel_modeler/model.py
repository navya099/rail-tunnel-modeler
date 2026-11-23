"""Sweep section boundaries and compare the resulting solids in 3D."""

from dataclasses import dataclass

import fcl
import numpy as np
import trimesh
from shapely import constrained_delaunay_triangles, orient_polygons
from shapely.geometry import Polygon

from .loft import section_samples


def _cap(rings):
    polygon = Polygon(rings[0], rings[1:])
    vertex_index = {tuple(point): i for i, point in enumerate(np.vstack(rings))}
    cap = np.array([
        [vertex_index[point] for point in triangle.exterior.coords[:-1]]
        for triangle in orient_polygons(constrained_delaunay_triangles(polygon)).geoms
    ])
    if not len(cap):
        raise ValueError("Unable to triangulate section boundary")
    return cap


def _sweep(samples, centers, lateral, up):
    rings = samples[0]
    if all(frame is rings for frame in samples):
        section = np.vstack(rings)[None, :, :]
    else:
        section = np.stack([np.vstack(frame) for frame in samples])
    size = section.shape[1]
    vertices = (centers[:, None, :] + lateral[:, None, :] * section[:, :, :1]
                + up * section[:, :, 1:2])

    current = np.arange(size)
    following = current + 1
    start = 0
    for ring in rings:
        following[start + len(ring) - 1] = start
        start += len(ring)
    i, j = current, following
    sides = np.concatenate((np.column_stack((i, i + size, j)),
                            np.column_stack((j, i + size, j + size))))
    strips = sides[None, :, :] + np.arange(len(centers) - 1)[:, None, None] * size
    last = (len(centers) - 1) * size
    first_cap = _cap(rings)
    last_cap = first_cap if samples[-1] is rings else _cap(samples[-1])
    faces = np.vstack((first_cap, strips.reshape(-1, 3), last_cap[:, ::-1] + last))
    mesh = trimesh.Trimesh(vertices=vertices.reshape(-1, 3), faces=faces, process=False)
    mesh.fix_normals()
    if not mesh.is_volume:
        raise ValueError("Unable to form a watertight, consistently oriented tunnel solid")
    return mesh


@dataclass
class Tunnel:
    name: str
    alignment: object
    stations: np.ndarray
    centers: np.ndarray
    inner: list[trimesh.Trimesh]
    outer: trimesh.Trimesh
    vertical: bool = False

    def station_at(self, point):
        axes = 3 if self.vertical else 2
        starts = self.centers[:-1, :axes]
        vectors = self.centers[1:, :axes] - starts
        delta = point[:axes] - starts
        lengths_sq = np.einsum("ij,ij->i", vectors, vectors)
        fractions = np.clip(np.einsum("ij,ij->i", delta, vectors) / lengths_sq, 0, 1)
        residual = delta - fractions[:, None] * vectors
        index = np.argmin(np.einsum("ij,ij->i", residual, residual))
        position = self.stations[index] + fractions[index] * (self.stations[index + 1] - self.stations[index])
        return float(position), float(self.alignment.displayed_station(position))


def build_tunnel(name, alignment, frame, keyframes, origin):
    stations = frame[0]
    # At a tight curve the inside of an offset section can fold over itself.
    left = np.searchsorted(alignment.limits, stations[0], side="right") - 1
    right = np.searchsorted(alignment.limits, stations[-1], side="left")
    min_x = min(keyframe.outer.bounds[0] for keyframe in keyframes)
    max_x = max(keyframe.outer.bounds[2] for keyframe in keyframes)
    for element in alignment.elements[left:right]:
        for curvature in (element.k_start, element.k_end):
            if min(1 + curvature * min_x, 1 + curvature * max_x) <= 0:
                raise ValueError(f"{name}: section crosses the center of horizontal curvature")
    return sweep_tunnel(name, alignment, frame, keyframes, origin)


def sweep_tunnel(name, axis, frame, keyframes, origin, *, vertical=False):
    stations, centers, headings = frame
    centers = centers - origin
    if vertical:
        lateral = np.broadcast_to([1, 0, 0], centers.shape)
        up = np.array([0, 1, 0])
    else:
        lateral = np.column_stack((np.sin(headings), -np.cos(headings), np.zeros(len(headings))))
        up = np.array([0, 0, 1])
    outer, *inner = section_samples(keyframes, stations)
    return Tunnel(name, axis, stations, centers,
                  [_sweep(part, centers, lateral, up) for part in inner],
                  _sweep(outer, centers, lateral, up), vertical)


class CollisionIndex:
    def __init__(self, mesh):
        self.mesh = mesh
        shape = fcl.BVHModel()
        shape.beginModel(len(mesh.faces), len(mesh.vertices))
        shape.addSubModel(mesh.vertices, mesh.faces)
        shape.endModel()
        self.object = fcl.CollisionObject(shape)


def compare(first, second):
    return compare_solids(CollisionIndex(first.outer), CollisionIndex(second.outer))


def compare_solids(first, second):
    a, b = first.object, second.object
    collision = fcl.CollisionResult()
    fcl.collide(a, b, fcl.CollisionRequest(num_max_contacts=1, enable_contact=True), collision)
    if collision.is_collision:
        point = np.asarray(collision.contacts[0].pos, dtype=float)
        return "contact_or_overlap", 0.0, (point, point)

    # Surface queries miss a solid that lies wholly inside the other solid.
    for container, candidate in ((first.mesh, second.mesh), (second.mesh, first.mesh)):
        if (np.all(container.bounds[0] <= candidate.bounds[0])
                and np.all(container.bounds[1] >= candidate.bounds[1])
                and container.contains(candidate.vertices[:1])[0]):
            point = candidate.vertices[0]
            return "containment", 0.0, (point, point)

    result = fcl.DistanceResult()
    distance = fcl.distance(a, b, fcl.DistanceRequest(enable_nearest_points=True), result)
    points = np.asarray(result.nearest_points, dtype=float)
    if distance < 0 or not np.isfinite(distance) or points.shape != (2, 3) or not np.isfinite(points).all():
        raise ValueError("Could not compute minimum distance and witness points")
    return ("contact_or_overlap" if distance == 0 else "clear"), float(distance), points
