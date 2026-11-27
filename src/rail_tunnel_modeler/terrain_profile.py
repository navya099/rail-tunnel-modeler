"""Query exact cross-sections of the loaded terrain triangles."""

from functools import cache

import numpy as np
import shapely
from shapely.geometry import LineString, Point
from shapely.strtree import STRtree


DISTANCE_TOLERANCE = 1e-7


class TerrainSections:
    def __init__(self, tiles):
        self.tiles = tiles
        bounds = np.asarray([tile.bounds[:, :2] for tile in tiles]).reshape(-1, 2, 2)
        boxes = shapely.box(bounds[:, 0, 0], bounds[:, 0, 1], bounds[:, 1, 0], bounds[:, 1, 1])
        self.tile_index = STRtree(boxes)
        self._cached_tile = cache(self._tile)

    def _tile(self, index):
        mesh = self.tiles[index]
        triangles = mesh.vertices[mesh.faces]
        polygons = shapely.polygons(shapely.linearrings(triangles[:, :, :2]))
        origins = triangles[:, 0]
        a, b = triangles[:, 1] - origins, triangles[:, 2] - origins
        determinant = a[:, 0] * b[:, 1] - a[:, 1] * b[:, 0]
        gradients = np.column_stack((a[:, 2] * b[:, 1] - a[:, 1] * b[:, 2],
                                     a[:, 0] * b[:, 2] - a[:, 2] * b[:, 0])) / determinant[:, None]
        return STRtree(polygons), origins, gradients

    def intervals(self, toe, direction, limit):
        start = toe[:2]
        ray = LineString([start, start + direction * limit])
        spans = []
        for index in self.tile_index.query(ray, predicate="intersects"):
            tree, origins, gradients = self._cached_tile(index)
            hits = tree.query(ray, predicate="intersects")
            for hit, intersection in zip(hits, shapely.intersection(tree.geometries[hits], ray)):
                if intersection.geom_type != "LineString" or intersection.length <= DISTANCE_TOLERANCE:
                    continue
                low, high = np.sort((np.asarray(intersection.coords)[[0, -1]] - start) @ direction)
                low, high = max(0.0, low), min(limit, high)
                x, y, z = origins[hit]
                gradient = gradients[hit]
                spans.append((low, high, z + (start - [x, y]) @ gradient, direction @ gradient))
        return sorted(spans)

    def elevation_at(self, xy):
        point = Point(xy)
        for index in self.tile_index.query(point, predicate="intersects"):
            tree, origins, gradients = self._cached_tile(index)
            hits = tree.query(point, predicate="intersects")
            if len(hits):
                hit = hits[0]
                return float(origins[hit, 2] + (xy - origins[hit, :2]) @ gradients[hit])
        return None


def first_daylight(spans, distances, rises, toe_elevation):
    """Find the first slope/terrain crossing, without crossing a terrain gap."""
    covered = 0.0
    for low, high, ground_at_toe, grade in spans:
        if high <= covered + DISTANCE_TOLERANCE:
            continue
        if low > covered + DISTANCE_TOLERANCE:
            break

        breaks = distances[(distances > covered) & (distances < high)]
        nodes = np.r_[covered, breaks, high]
        residuals = toe_elevation + np.interp(nodes, distances, rises) - (ground_at_toe + grade * nodes)
        if covered == 0 and residuals[0] >= -DISTANCE_TOLERANCE:
            return "no_cut", 0.0
        if residuals[0] >= 0:
            return "daylight", covered
        for i in np.flatnonzero(residuals[1:] >= 0)[:1]:
            length = nodes[i + 1] - nodes[i]
            return "daylight", float(nodes[i] - residuals[i] * length / (residuals[i + 1] - residuals[i]))
        covered = high

    if covered <= DISTANCE_TOLERANCE:
        return "no_terrain", None
    if covered < distances[-1] - DISTANCE_TOLERANCE:
        return "terrain_gap", None
    return "no_daylight", None
