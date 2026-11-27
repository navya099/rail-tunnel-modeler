import json
from pathlib import Path

import numpy as np
import pytest
import rasterio
import shapely
import trimesh
from rasterio.transform import from_bounds, from_origin
from rasterio.warp import transform_bounds

from rail_tunnel_modeler.cli import _project, main

EXAMPLES = Path(__file__).resolve().parents[1] / "examples"


def _config(terrain):
    config = json.loads((EXAMPLES / "terrain-tin.json").read_text())
    config["landxml"] = str(EXAMPLES / "crossing.xml")
    config["tunnels"][0]["section"] = str(EXAMPLES / "natm.json")
    config["terrain"] = terrain
    return config


def _project_file(folder, config):
    path = folder / "project.json"
    path.write_text(json.dumps(config), encoding="utf-8")
    return path


def _raster(path, values, transform, crs="EPSG:5179"):
    height, width = values.shape
    with rasterio.open(path, "w", driver="GTiff", height=height, width=width,
                       count=1, dtype="float32", transform=transform, crs=crs, nodata=-9999) as dest:
        dest.write(np.asarray(values, dtype="float32"), 1)


def test_tin_project_preserves_coordinates_and_exports_terrain(tmp_path):
    report_path = tmp_path / "report.json"
    glb_path = tmp_path / "model.glb"
    assert main([str(EXAMPLES / "terrain-tin.json"), "--report", str(report_path),
                 "--glb", str(glb_path)]) == 0
    summary = json.loads(report_path.read_text())["terrain"]
    assert summary["type"] == "landxml_surface"
    assert summary["triangle_count"] == 4
    assert summary["bounds_enz_m"] == [[249980.0, 449975.0, 20.0], [250120.0, 450025.0, 29.0]]
    scene = trimesh.load_scene(glb_path)
    assert {"Main/inner/1", "Main/outer", "Terrain/1"} <= set(scene.geometry)
    assert len(scene.geometry["Terrain/1"].faces) == 4
    assert not scene.geometry["Terrain/1"].is_volume


def test_dem_tiling_keeps_seams_and_nodata_holes(tmp_path):
    raster = tmp_path / "ground.tif"
    data = np.full((180, 290), 30.0)
    data[101, 101] = -9999  # Between sample points when using stride 2.
    _raster(raster, data, from_origin(249950, 450100, 1, 1))
    config = _config({"type": "dem", "path": raster.name, "project_crs": "EPSG:5179",
                      "corridor_m": 14, "dem_stride": 2, "vertical_offset_m": 2.5})
    project = _project(_project_file(tmp_path, config))
    tiles = project.terrain.tiles
    origin = np.asarray(project.report["model_origin_enz_m"])
    assert len(tiles) == 2
    first, second = (tile.vertices + origin for tile in tiles)
    shared_east = 250078.5
    assert first[:, 0].max() == pytest.approx(shared_east)
    assert second[:, 0].min() == pytest.approx(shared_east)
    assert np.array_equal(first[first[:, 0] == shared_east], second[second[:, 0] == shared_east])
    assert np.allclose(first[:, 2], 32.5)
    assert np.all(first[:, 1] > 449980)
    assert np.all(tiles[0].face_normals[:, 2] > 0)

    triangles = first[tiles[0].faces, :2]
    polygons = shapely.polygons(shapely.linearrings(triangles))
    assert not shapely.covers(polygons, shapely.Point(250051.5, 449998.5)).any()
    assert shapely.covers(polygons, shapely.Point(250056.5, 449998.5)).any()


def test_dem_reprojects_to_project_crs(tmp_path):
    raster = tmp_path / "geographic.tif"
    bounds = transform_bounds("EPSG:3857", "EPSG:4326", 249990, 449950, 250110, 450050)
    _raster(raster, np.full((25, 30), 39.0), from_bounds(*bounds, 30, 25), crs="EPSG:4326")
    config = _config({"type": "dem", "path": raster.name, "project_crs": "EPSG:3857",
                      "corridor_m": 10})
    report = _project(_project_file(tmp_path, config)).report["terrain"]
    assert report["project_crs"] == "EPSG:3857"
    assert report["bounds_enz_m"][0][2] == pytest.approx(39)
    assert report["bounds_enz_m"][1][2] == pytest.approx(39)
    assert 249990 < report["bounds_enz_m"][0][0] < 250010
    assert 250090 < report["bounds_enz_m"][1][0] < 250110


def test_landxml_surface_in_alignment_file_and_invisible_triangles(tmp_path):
    source = (EXAMPLES / "terrain-tin.xml").read_text()
    surfaces = source[source.index("  <Surfaces>"):source.index("</LandXML>")]
    surfaces = surfaces.replace("<F>40 42 55</F>", '<F i="1">40 42 55</F>')
    xml = tmp_path / "alignment.xml"
    xml.write_text((EXAMPLES / "crossing.xml").read_text().replace("</LandXML>", surfaces + "</LandXML>"))
    config = _config({"type": "landxml_surface", "surface": "Existing ground", "corridor_m": 12})
    config["landxml"] = xml.name
    assert _project(_project_file(tmp_path, config)).report["terrain"]["triangle_count"] == 3


def test_terrain_crs_mismatch_and_source_overwrite_are_rejected(tmp_path):
    source = EXAMPLES / "terrain-tin.xml"
    config = _config({"type": "landxml_surface", "path": str(source), "project_crs": "EPSG:3857"})
    path = _project_file(tmp_path, config)
    with pytest.raises(ValueError, match="same EPSG code"):
        _project(path)
    with pytest.raises(ValueError, match="overwrite an input file"):
        _project(path, glb_path=source)


def test_dem_outside_corridor_is_reported(tmp_path):
    raster = tmp_path / "far.tif"
    _raster(raster, np.full((20, 20), 39.0), from_origin(349990, 650000, 1, 1))
    config = _config({"type": "dem", "path": raster.name, "project_crs": "EPSG:5179"})
    with pytest.raises(ValueError, match="no usable triangles"):
        _project(_project_file(tmp_path, config))
