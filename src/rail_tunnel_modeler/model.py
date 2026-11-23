"""Sweep section boundaries and compare the resulting solids in 3D."""

from dataclasses import dataclass

import fcl
import numpy as np
import trimesh
from shapely import constrained_delaunay_triangles, orient_polygons, set_precision


def _sweep(polygon, centers, headings, datum_y):
    polygon = orient_polygons(set_precision(polygon, 1e-8))
    if polygon.is_empty or polygon.geom_type != "Polygon":
        raise ValueError("Section boundary collapsed or split during triangulation")
    rings = [np.asarray(ring.coords)[:-1] for ring in (polygon.exterior, *polygon.interiors)]
    section = np.vstack(rings)
    vertex_index = {tuple(point): i for i, point in enumerate(section)}
    if len(vertex_index) != len(section):
        raise ValueError("Section boundary rings share vertices")
    cap = np.array([
        [vertex_index[point] for point in triangle.exterior.coords[:-1]]
        for triangle in orient_polygons(constrained_delaunay_triangles(polygon)).geoms
    ])
    if not len(cap):
        raise ValueError("Unable to triangulate section boundary")
    size = len(section)
    # Civil cross sections remain vertical and normal to the horizontal bearing.
    right = np.column_stack((np.sin(headings), -np.cos(headings), np.zeros(len(headings))))
    vertical = np.array([0, 0, 1])
    vertices = (centers[:, None, :] + right[:, None, :] * section[None, :, :1]
                + vertical * (section[None, :, 1:2] - datum_y))

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
    faces = np.vstack((cap, strips.reshape(-1, 3), cap[:, ::-1] + last))
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

    def station_at(self, point):
        starts = self.centers[:-1, :2]
        vectors = self.centers[1:, :2] - starts
        delta = point[:2] - starts
        lengths_sq = np.einsum("ij,ij->i", vectors, vectors)
        fractions = np.clip(np.einsum("ij,ij->i", delta, vectors) / lengths_sq, 0, 1)
        residual = delta - fractions[:, None] * vectors
        index = np.argmin(np.einsum("ij,ij->i", residual, residual))
        position = self.stations[index] + fractions[index] * (self.stations[index + 1] - self.stations[index])
        return float(position), float(self.alignment.displayed_station(position))


def build_tunnel(name, alignment, frame, boundaries, datum_y, origin):
    stations, centers, headings = frame
    centers = centers - origin
    inner, outer = boundaries
    # At a tight curve the inside of an offset section can fold over itself.
    left = np.searchsorted(alignment.limits, stations[0], side="right") - 1
    right = np.searchsorted(alignment.limits, stations[-1], side="left")
    min_x, _, max_x, _ = outer.bounds
    for element in alignment.elements[left:right]:
        for curvature in (element.k_start, element.k_end):
            if min(1 + curvature * min_x, 1 + curvature * max_x) <= 0:
                raise ValueError(f"{name}: section crosses the center of horizontal curvature")
    inner_parts = inner.geoms if inner.geom_type == "MultiPolygon" else [inner]
    return Tunnel(name, alignment, stations, centers,
                  [_sweep(part, centers, headings, datum_y) for part in inner_parts],
                  _sweep(outer, centers, headings, datum_y))


def _collision_object(mesh):
    shape = fcl.BVHModel()
    shape.beginModel(len(mesh.faces), len(mesh.vertices))
    shape.addSubModel(mesh.vertices, mesh.faces)
    shape.endModel()
    return fcl.CollisionObject(shape)


def compare(first, second):
    a, b = map(_collision_object, (first.outer, second.outer))
    collision = fcl.CollisionResult()
    fcl.collide(a, b, fcl.CollisionRequest(num_max_contacts=1, enable_contact=True), collision)
    if collision.is_collision:
        point = np.asarray(collision.contacts[0].pos, dtype=float)
        return "contact_or_overlap", 0.0, (point, point)

    # Surface queries miss a solid that lies wholly inside the other solid.
    for container, candidate in ((first.outer, second.outer), (second.outer, first.outer)):
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
