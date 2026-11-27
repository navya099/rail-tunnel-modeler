import json
from pathlib import Path

import numpy as np
import pytest
import rasterio
import trimesh
from rasterio.transform import from_origin

from rail_tunnel_modeler.cli import _project, main
from rail_tunnel_modeler.earthworks import _cut_surface, _slope_profile
from rail_tunnel_modeler.terrain_profile import TerrainSections, first_daylight

EXAMPLES = Path(__file__).resolve().parents[1] / "examples"


def _config():
    config = json.loads((EXAMPLES / "portals.json").read_text())
    config["landxml"] = str(EXAMPLES / config["landxml"])
    for tunnel in config["tunnels"]:
        tunnel["section"] = str(EXAMPLES / tunnel["section"])
    return config


def _save(folder, config):
    path = folder / "project.json"
    path.write_text(json.dumps(config), encoding="utf-8")
    return path


def test_portal_project_exports_walls_and_benched_slopes(tmp_path):
    glb_path = tmp_path / "portals.glb"
    report_path = tmp_path / "portals.json"
    assert main([str(EXAMPLES / "portals.json"), "--glb", str(glb_path),
                 "--report", str(report_path)]) == 0
    report = json.loads(report_path.read_text())
    entrance, exit_report = report["portals"]
    assert entrance["internal_station_m"] == 1030
    assert exit_report["internal_station_m"] == 1070
    assert entrance["approach_cut"]["left"][0]["internal_station_m"] == 1012
    assert exit_report["approach_cut"]["right"][-1]["internal_station_m"] == 1088
    for portal in report["portals"]:
        for rows in portal["approach_cut"].values():
            assert len(rows) == 10
            assert all(row["result"] == "daylight" for row in rows)
    scene = trimesh.load_scene(glb_path)
    for name in ("Entrance", "Exit"):
        assert scene.geometry[f"Portal/{name}/Headwall"].is_volume
        left = scene.geometry[f"Portal/{name}/Cut/left"]
        assert len(left.faces) > 0
        assert not left.is_volume
        assert f"Portal/{name}/Cut/right" in scene.geometry
    assert len(scene.geometry["Portal/Entrance/Cut/left"].faces) > len(scene.geometry["Portal/Exit/Cut/left"].faces)


def test_box_headwall_keeps_both_compartments_open_without_terrain(tmp_path):
    config = _config()
    config["tunnels"][0]["section"] = str(EXAMPLES / "box.json")
    del config["terrain"]
    config["portals"] = [config["portals"][0]]
    del config["portals"][0]["approach_cut"]
    project = _project(_save(tmp_path, config))
    wall = project.portals[0].wall
    origin = project.report["model_origin_enz_m"]
    halfway = 250030 - 0.55
    locations = np.array([[halfway, 450002, 3], [halfway, 449998, 3],
                          [halfway, 450000, 3]]) - origin
    assert wall.contains(locations).tolist() == [False, False, True]
    assert wall.bounds[:, 0] + origin[0] == pytest.approx([250029.2, 250030.0])


def test_slope_reports_first_daylight_and_missing_data():
    distances, rises = _slope_profile(0.5, [(6.0, 1.2)], 12)
    assert first_daylight([(0, 12, 10, 0)], distances, rises, 0) == pytest.approx(("daylight", 6.2))
    assert first_daylight([(0, 3, 10, 0), (5, 12, 10, 0)], distances, rises, 0) == ("terrain_gap", None)
    assert first_daylight([(4, 12, 10, 0)], distances, rises, 0) == ("no_terrain", None)
    assert first_daylight([(0, 12, 40, 0)], distances, rises, 0) == ("no_daylight", None)
    assert first_daylight([(0, 12, 0, 0)], distances, rises, 0) == ("no_cut", 0.0)
    # A falling terrain segment crosses the level bench before the next sloped segment.
    assert first_daylight([(0, 12, 10, -1)], distances, rises, 0) == pytest.approx(("daylight", 4.0))


def test_cut_mesh_does_not_connect_missing_rows():
    rows = [np.array([[x, 0, 0], [x, 1, 1]]) for x in range(5)]
    rows[2] = None
    mesh = _cut_surface(rows, right_side=False)
    assert len(mesh.faces) == 4
    assert all(not (2 > face[:, 0].min() and 2 < face[:, 0].max()) for face in mesh.triangles)


def test_surface_profile_uses_triangles_across_a_tile_seam():
    def tile(x0, x1):
        points = [[x0, 0, 10 + x0], [x1, 0, 10 + x1], [x1, 1, 10 + x1], [x0, 1, 10 + x0]]
        return trimesh.Trimesh(points, [[0, 1, 2], [0, 2, 3]], process=False)

    terrain = TerrainSections([tile(0, 5), tile(5, 12)])
    intervals = terrain.intervals(np.array([0, 0.5, 0]), np.array([1, 0]), 12)
    assert min(span[0] for span in intervals) == 0
    assert max(span[1] for span in intervals) == 12
    assert all(span[2:] == pytest.approx((10, 1)) for span in intervals)
    distances, rises = _slope_profile(1 / 3, [], 12)
    assert first_daylight(intervals, distances, rises, 0) == pytest.approx(("daylight", 5.0))


def test_portal_dem_cut_uses_full_resolution_despite_display_stride(tmp_path):
    raster_path = tmp_path / "terrain.tif"
    y = np.arange(81)[:, None]
    values = np.broadcast_to(19.0 + 4.0 * np.sin(y * 0.77), (81, 141)).astype("float32")
    with rasterio.open(raster_path, "w", driver="GTiff", width=141, height=81, count=1,
                       dtype="float32", crs="EPSG:5179", transform=from_origin(249980, 450040, 1, 1)) as dataset:
        dataset.write(values, 1)

    config = _config()
    config["terrain"] = {"type": "dem", "path": raster_path.name, "project_crs": "EPSG:5179", "corridor_m": 2}
    config["portals"] = [config["portals"][0]]
    dense = _project(_save(tmp_path, config)).report
    config["terrain"]["dem_stride"] = 5
    coarse = _project(_save(tmp_path, config)).report
    assert dense["terrain"]["triangle_count"] > coarse["terrain"]["triangle_count"]
    assert dense["portals"] == coarse["portals"]
    assert all(row["result"] == "daylight" for row in coarse["portals"][0]["approach_cut"]["left"])


def test_approach_requires_terrain(tmp_path):
    config = _config()
    del config["terrain"]
    with pytest.raises(ValueError, match="approach cut requires terrain"):
        _project(_save(tmp_path, config))
