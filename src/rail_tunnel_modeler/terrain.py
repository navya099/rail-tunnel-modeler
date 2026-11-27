"""Load terrain triangles around the modeled alignment corridors."""

from collections import defaultdict
from contextlib import ExitStack
from dataclasses import dataclass
from itertools import product
from math import floor, isfinite

import numpy as np
import shapely
import trimesh
from shapely.geometry import LineString, Point

from .landxml import LandXML


TIN_TILE_M = 256
DEM_TILE_CELLS = 128


@dataclass(frozen=True)
class Terrain:
    source: str
    name: str
    tiles: list[trimesh.Trimesh]
    project_crs: str | None

    def summary(self, origin):
        bounds = np.array([tile.bounds for tile in self.tiles])
        return {
            "type": self.source,
            "name": self.name,
            "project_crs": self.project_crs,
            "tile_count": len(self.tiles),
            "triangle_count": sum(len(tile.faces) for tile in self.tiles),
            "bounds_enz_m": [list(bounds[:, 0].min(axis=0) + origin),
                             list(bounds[:, 1].max(axis=0) + origin)],
        }


def _epsg(value):
    if value is None:
        return None
    value = str(value).strip().upper().removeprefix("EPSG:")
    if not value.isascii() or not value.isdecimal() or int(value) <= 0:
        raise ValueError("Terrain CRS must be an EPSG code")
    return f"EPSG:{int(value)}"


def _xml_crs(xml):
    node = xml.root.find("CoordinateSystem")
    code = node.get("epsgCode") if node is not None else None
    return _epsg(code) if code not in {None, "", "0"} else None


def _check_crs(*codes):
    present = {code for code in codes if code is not None}
    if len(present) > 1:
        raise ValueError("LandXML coordinates and terrain project_crs must use the same EPSG code")
    return next(iter(present), None)


def _corridor(axes, distance):
    paths = []
    for centers in axes:
        xy = centers[:, :2]
        path = Point(xy[0]) if np.all(xy == xy[0]) else LineString(xy)
        paths.append(path.buffer(distance))
    region = shapely.union_all(paths)
    shapely.prepare(region)
    return region


def _nearby_faces(points, faces, region):
    if not len(faces):
        return faces
    triangles = points[faces, :2]
    xmin, ymin, xmax, ymax = region.bounds
    candidates = (np.all(triangles.max(axis=1) >= [xmin, ymin], axis=1)
                  & np.all(triangles.min(axis=1) <= [xmax, ymax], axis=1))
    triangles, faces = triangles[candidates], faces[candidates]
    first, second = triangles[:, 1] - triangles[:, 0], triangles[:, 2] - triangles[:, 0]
    signed = first[:, 0] * second[:, 1] - first[:, 1] * second[:, 0]
    valid = signed != 0
    faces = faces[valid].copy()
    faces[signed[valid] < 0] = faces[signed[valid] < 0, ::-1]
    polygons = shapely.polygons(shapely.linearrings(triangles[valid]))
    return faces[shapely.intersects(region, polygons)]


def _tile(points, faces, origin):
    ids, reverse = np.unique(faces, return_inverse=True)
    return trimesh.Trimesh(points[ids] - origin, reverse.reshape(-1, 3), process=False)


def _tin_tiles(xml, name, region, offset, origin):
    surfaces = xml.root.findall("./Surfaces/Surface")
    if name is not None:
        surfaces = [surface for surface in surfaces if surface.get("name") == name]
    if len(surfaces) != 1:
        raise ValueError("Specify one unambiguous LandXML Surface name")
    surface = surfaces[0]
    definition = surface.find("Definition")
    if definition is None or definition.get("surfType", "").upper() != "TIN":
        raise ValueError("LandXML Surface must have a TIN Definition")

    points, indices = [], {}
    for node in definition.iterfind("./Pnts/P"):
        key = node.attrib["id"]
        north, east, height = map(float, (node.text or "").split())
        if key in indices or not all(map(isfinite, (north, east, height + offset))):
            raise ValueError("LandXML TIN points must have unique IDs and finite coordinates")
        indices[key] = len(points)
        points.append((east, north, height + offset))
    points = np.asarray(points)
    groups = defaultdict(list)
    faces = []

    def collect(batch):
        selected = _nearby_faces(points, np.asarray(batch, dtype=int), region)
        if len(selected):
            cells = np.floor(points[selected, :2].mean(axis=1) / TIN_TILE_M).astype(int)
            for cell, face in zip(cells, selected):
                groups[tuple(cell)].append(face)

    for node in definition.iterfind("./Faces/F"):
        if node.get("i", "").lower() in {"1", "true"}:
            continue
        ids = (node.text or "").split()
        if len(ids) != 3 or len(set(ids)) != 3 or any(key not in indices for key in ids):
            raise ValueError("LandXML TIN faces must reference three distinct defined points")
        faces.append([indices[key] for key in ids])
        if len(faces) == 4096:
            collect(faces)
            faces.clear()
    if faces:
        collect(faces)
    return [_tile(points, np.asarray(group), origin) for _, group in sorted(groups.items())], surface.get("name", "TIN")


def _candidate_tiles(raster, axes, buffer_m, span):
    count_x = (raster.width - 2) // span + 1
    count_y = (raster.height - 2) // span + 1
    if count_x < 1 or count_y < 1:
        return []
    transform = raster.transform
    tiles = set()
    for centers in axes:
        for a, b in zip(centers[:-1, :2], centers[1:, :2]):
            x0, y0 = np.minimum(a, b) - buffer_m
            x1, y1 = np.maximum(a, b) + buffer_m
            left, right = (np.array([x0, x1]) - transform.c) / transform.a
            top, bottom = (np.array([y1, y0]) - transform.f) / transform.e
            c0 = max(0, floor((left - 0.5) / span))
            c1 = min(count_x - 1, floor((right - 0.5) / span))
            r0 = max(0, floor((top - 0.5) / span))
            r1 = min(count_y - 1, floor((bottom - 0.5) / span))
            tiles.update(product(range(r0, r1 + 1), range(c0, c1 + 1)))
    return sorted(tiles)


def _sample_indices(last, stride):
    samples = np.arange(0, last + 1, stride)
    return samples if samples[-1] == last else np.r_[samples, last]


def _dem_tile(raw, row, col, transform, stride, scale, offset, region, origin):
    values = np.asarray(raw.data, dtype=float) * scale + offset
    invalid = np.ma.getmaskarray(raw) | ~np.isfinite(values)
    rows, cols = (_sample_indices(length - 1, stride) for length in values.shape)
    heights = values[np.ix_(rows, cols)]
    east = transform.c + (col + cols + 0.5) * transform.a
    north = transform.f + (row + rows + 0.5) * transform.e
    xy = np.meshgrid(east, north)
    points = np.column_stack((xy[0].ravel(), xy[1].ravel(), heights.ravel()))

    # A coarse grid cell is usable only if its whole source footprint is finite.
    sums = np.pad(invalid, ((1, 0), (1, 0))).cumsum(axis=0).cumsum(axis=1)
    sums_tl = sums[np.ix_(rows[:-1], cols[:-1])]
    sums_br = sums[np.ix_(rows[1:] + 1, cols[1:] + 1)]
    sums_bl = sums[np.ix_(rows[1:] + 1, cols[:-1])]
    sums_tr = sums[np.ix_(rows[:-1], cols[1:] + 1)]
    valid = (sums_tl + sums_br - sums_bl - sums_tr == 0)
    top_left = np.arange(points.shape[0]).reshape(heights.shape)[:-1, :-1][valid]
    bottom_left = top_left + len(cols)
    faces = np.vstack((np.column_stack((top_left, bottom_left, bottom_left + 1)),
                       np.column_stack((top_left, bottom_left + 1, top_left + 1))))
    faces = _nearby_faces(points, faces, region)
    return _tile(points, faces, origin) if len(faces) else None


def _dem_tiles(path, project_crs, axes, distance, region, stride, offset, origin):
    try:
        import rasterio
        from rasterio.enums import Resampling
        from rasterio.vrt import WarpedVRT
        from rasterio.windows import Window
    except ModuleNotFoundError as exc:
        if exc.name != "rasterio":
            raise
        raise ValueError('DEM support requires pip install ".[terrain]"') from exc

    try:
        target = rasterio.crs.CRS.from_string(project_crs)
    except rasterio.errors.CRSError as exc:
        raise ValueError(f"Unknown DEM project_crs {project_crs}") from exc
    if not target.is_projected or abs(target.linear_units_factor[1] - 1) > 1e-9:
        raise ValueError("DEM project_crs must be a projected CRS in meters")

    tiles = []
    with ExitStack() as stack:
        source = stack.enter_context(rasterio.open(path))
        if source.crs is None or not source.count:
            raise ValueError(f"{path}: DEM requires a source CRS and an elevation band")
        unit = (source.units[0] or "").lower().strip()
        if unit and unit not in {"m", "meter", "metre", "meters", "metres"}:
            raise ValueError(f"{path}: DEM elevations must be in meters")
        scale, base_offset = source.scales[0], source.offsets[0]
        affine = source.transform
        if source.crs == target and affine.a > 0 and affine.e < 0 and affine.b == affine.d == 0:
            raster = source
        else:
            raster = stack.enter_context(WarpedVRT(source, crs=target, dtype="float64", nodata=np.nan,
                                                   resampling=Resampling.bilinear))
        transform = raster.transform
        if not (transform.a > 0 and transform.e < 0 and transform.b == transform.d == 0):
            raise ValueError("Could not convert the DEM to a north-up grid")
        span = min(stride, DEM_TILE_CELLS) * max(1, DEM_TILE_CELLS // stride)
        for tile_row, tile_col in _candidate_tiles(raster, axes, distance, span):
            row, col = tile_row * span, tile_col * span
            count_y = min(raster.height - row, span + 1)
            count_x = min(raster.width - col, span + 1)
            west, east = transform.c + (np.array([col, col + count_x - 1]) + 0.5) * transform.a
            north, south = transform.f + (np.array([row, row + count_y - 1]) + 0.5) * transform.e
            if not shapely.intersects(region, shapely.box(west, south, east, north)):
                continue
            raw = raster.read(1, window=Window(col, row, count_x, count_y), masked=True)
            tile = _dem_tile(raw, row, col, transform, stride, scale, base_offset + offset,
                             region, origin)
            if tile is not None:
                tiles.append(tile)
    return tiles


def load_terrain(spec, folder, landxml_path, landxml, centers, origin):
    source_type = spec["type"]
    if source_type not in {"dem", "landxml_surface"}:
        raise ValueError("Terrain type must be dem or landxml_surface")
    default_path = landxml_path.resolve() if source_type == "landxml_surface" else None
    path = folder / spec["path"] if "path" in spec else default_path
    if path is None:
        raise ValueError("DEM terrain requires a path")
    distance = float(spec.get("corridor_m", 50))
    offset = float(spec.get("vertical_offset_m", 0))
    if not isfinite(distance) or distance <= 0 or not isfinite(offset):
        raise ValueError("Terrain corridor_m must be positive and vertical_offset_m finite")
    crs = _check_crs(_epsg(spec.get("project_crs")), _xml_crs(landxml))
    region = _corridor(centers, distance)

    if source_type == "landxml_surface":
        xml = landxml if path.resolve() == landxml_path.resolve() else LandXML(path)
        crs = _check_crs(crs, _xml_crs(xml))
        tiles, name = _tin_tiles(xml, spec.get("surface"), region, offset, origin)
    else:
        if crs is None:
            raise ValueError("DEM terrain requires project_crs or a LandXML EPSG code")
        stride = spec.get("dem_stride", 1)
        if type(stride) is not int or stride < 1:
            raise ValueError("dem_stride must be a positive integer")
        tiles = _dem_tiles(path, crs, centers, distance, region, stride, offset, origin)
        name = path.name
    if not tiles:
        raise ValueError("Terrain has no usable triangles in the modeled corridor")
    return Terrain(source_type, name, tiles, crs)
