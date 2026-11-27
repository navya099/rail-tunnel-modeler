import json
from pathlib import Path

import numpy as np
import pytest
import rasterio
import trimesh
from rasterio.transform import from_origin

from rail_tunnel_modeler.cli import _project, main

EXAMPLES = Path(__file__).resolve().parents[1] / "examples"


def _config():
    config = json.loads((EXAMPLES / "cut-and-cover.json").read_text())
    config["landxml"] = str(EXAMPLES / config["landxml"])
    config["tunnels"][0]["section"] = str(EXAMPLES / config["tunnels"][0]["section"])
    config["terrain"]["path"] = str(EXAMPLES / config["terrain"]["path"])
    return config


def _write(folder, config):
    path = folder / "project.json"
    path.write_text(json.dumps(config), encoding="utf-8")
    return path


def test_cut_and_cover_exports_formation_and_daylight(tmp_path):
    glb_path = tmp_path / "excavation.glb"
    report_path = tmp_path / "report.json"
    assert main([str(EXAMPLES / "cut-and-cover.json"), "--glb", str(glb_path),
                 "--report", str(report_path)]) == 0
    report = json.loads(report_path.read_text())["cut_and_cover"][0]
    assert len(report["sections"]) == 21
    middle = report["sections"][10]
    assert middle["internal_station_m"] == 1050
    assert middle["ground_elevation_m"] == pytest.approx(28)
    assert middle["box_top_elevation_m"] == pytest.approx(6.9)
    assert middle["cover_m"] == pytest.approx(21.1)
    assert middle["formation_width_m"] == pytest.approx(11.3)
    assert middle["formation_elevation_m"] == pytest.approx(-0.65)
    assert all(row["result"] == "daylight" for side in report["daylight"].values() for row in side)
    scene = trimesh.load_scene(glb_path)
    floor = scene.geometry["CutAndCover/Cut and cover/Formation"]
    assert not floor.is_volume
    assert len(floor.faces) == 40
    assert {"CutAndCover/Cut and cover/Cut/left", "CutAndCover/Cut and cover/Cut/right",
            "Cut and cover/outer", "Terrain/1"} <= set(scene.geometry)


def test_formation_respects_section_transition_and_rl_datum(tmp_path):
    config = _config()
    tunnel = config["tunnels"][0]
    del tunnel["section"]
    data = json.loads((EXAMPLES / "box.json").read_text())
    wide = tmp_path / "wide.json"
    wide.write_text(json.dumps({**data, "box_width": 10}), encoding="utf-8")
    tunnel["datum"] = "RL"
    tunnel["sections"] = [{"station": 1040, "section": str(EXAMPLES / "box.json")},
                          {"station": 1060, "section": wide.name}]

    project = _project(_write(tmp_path, config))
    cuts, = project.cut_and_cover
    points = {item["internal_station_m"]: item for item in cuts.report["sections"]}
    assert [points[station]["formation_width_m"] for station in (1030, 1050, 1070)] == pytest.approx(
        [11.3, 12.3, 13.3]
    )
    assert points[1050]["formation_elevation_m"] == pytest.approx(-1.122)
    assert points[1050]["box_top_elevation_m"] == pytest.approx(6.428)
    xyz = cuts.formation.vertices + project.report["model_origin_enz_m"]
    middle = xyz[np.isclose(xyz[:, 0], 250050, rtol=0, atol=1e-8)]
    assert sorted(middle[:, 1]) == pytest.approx([449993.6, 450005.9])


def test_dem_nodata_makes_cover_unknown_and_keeps_the_design_floor(tmp_path):
    raster_path = tmp_path / "terrain.tif"
    values = np.full((80, 140), 27, dtype=np.float32)
    values[39:41, 69:71] = -9999
    with rasterio.open(raster_path, "w", driver="GTiff", width=140, height=80, count=1,
                       dtype="float32", crs="EPSG:5179", transform=from_origin(249980, 450040, 1, 1),
                       nodata=-9999) as dataset:
        dataset.write(values, 1)
    config = _config()
    config["terrain"] = {"type": "dem", "path": raster_path.name, "project_crs": "EPSG:5179", "dem_stride": 4}
    project = _project(_write(tmp_path, config))
    excavation, = project.cut_and_cover
    points = {item["internal_station_m"]: item for item in excavation.report["sections"]}
    assert points[1050]["ground_elevation_m"] is None
    assert points[1050]["cover_m"] is None
    assert points[1040]["cover_m"] == pytest.approx(20.1)
    assert len(excavation.formation.faces) == 40
    assert excavation.report["daylight"]["left"][10]["result"] == "daylight"


@pytest.mark.parametrize("change, message", [
    ("natm", "requires Box sections"),
    ("no_terrain", "requires terrain"),
    ("negative_space", "working_space_m"),
])
def test_cut_and_cover_validates_inputs(tmp_path, change, message):
    config = _config()
    tunnel = config["tunnels"][0]
    if change == "natm":
        tunnel["section"] = str(EXAMPLES / "natm.json")
    elif change == "no_terrain":
        del config["terrain"]
    else:
        tunnel["cut_and_cover"]["working_space_m"] = [-1, 1]
    with pytest.raises(ValueError, match=message):
        _project(_write(tmp_path, config))
