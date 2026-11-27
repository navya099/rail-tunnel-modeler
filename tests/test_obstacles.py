import json
from pathlib import Path

import numpy as np
import pytest
import trimesh

from rail_tunnel_modeler.cli import _project, main
from rail_tunnel_modeler.obstacles import load_obstacles

EXAMPLES = Path(__file__).resolve().parents[1] / "examples"


def _config():
    config = json.loads((EXAMPLES / "obstacles.json").read_text())
    config["landxml"] = str(EXAMPLES / config["landxml"])
    config["tunnels"][0]["section"] = str(EXAMPLES / config["tunnels"][0]["section"])
    return config


def _write(folder, config):
    path = folder / "project.json"
    path.write_text(json.dumps(config), encoding="utf-8")
    return path


def test_obstacle_example_reports_clash_and_insufficient_clearance(tmp_path):
    report_path = tmp_path / "report.json"
    glb_path = tmp_path / "obstacles.glb"
    assert main([str(EXAMPLES / "obstacles.json"), "--report", str(report_path),
                 "--glb", str(glb_path)]) == 0
    report = json.loads(report_path.read_text())
    road, rail, utility = report["obstacle_checks"]
    assert road["result"] == "below_clearance"
    assert road["minimum_clearance_m"] == pytest.approx(0.86223809)
    assert road["required_clearance_m"] == 1
    assert road["internal_station_m"] == pytest.approx(1050, abs=3.5)
    assert np.linalg.norm(np.subtract(road["tunnel_point_enz_m"], road["obstacle_point_enz_m"])) == pytest.approx(
        road["minimum_clearance_m"]
    )
    assert rail["result"] == "contact_or_overlap"
    assert rail["minimum_clearance_m"] == 0
    reported = np.asarray(rail["tunnel_point_enz_m"])
    assert rail["tunnel_point_enz_m"] == rail["obstacle_point_enz_m"]
    assert np.all(np.abs(reported - [250035, 450000, 2.5]) <= [2.5 + 1e-8, 16 + 1e-8, 2 + 1e-8])
    assert utility["result"] == "clear"
    assert utility["required_clearance_m"] == 0.4
    assert utility["minimum_clearance_m"] > 4
    scene = trimesh.load_scene(glb_path)
    assert {"Obstacle/Existing road tunnel/1", "Obstacle/Utility pipe/2", "Witness/3"} <= set(scene.geometry)


def test_glb_tunnel_can_be_reimported_in_project_coordinates(tmp_path):
    path = tmp_path / "existing.glb"
    previous = _project(EXAMPLES / "crossing.json", glb_path=path)
    config = _config()
    config["obstacles"] = [{"name": "Imported tunnel", "kind": "rail_tunnel", "glb": {
        "path": path.name, "origin_enz_m": previous.report["model_origin_enz_m"], "nodes": ["Box/outer"]
    }}]
    project_path = _write(tmp_path, config)
    project = _project(project_path)
    check = project.report["obstacle_checks"][0]
    assert check["result"] == "clear"
    assert check["minimum_clearance_m"] == pytest.approx(2.46223809, abs=1e-6)
    assert check["obstacle_point_enz_m"][2] == pytest.approx(11.6, abs=1e-6)
    assert len(project.obstacles[0].meshes) == 1
    with pytest.raises(ValueError, match="overwrite an input file"):
        _project(project_path, glb_path=path)


def test_glb_instances_preserve_transforms_and_disconnected_parts(tmp_path):
    glb_path = tmp_path / "asset.glb"
    component_a = trimesh.creation.box().apply_translation([100.0, 0.0, 0.0])
    component_b = trimesh.creation.box(extents=[0.5, 1.0, 1.5])
    scene = trimesh.Scene()
    transform = trimesh.transformations.translation_matrix([1.0, 3.0, -2.0])
    scene.add_geometry(trimesh.util.concatenate([component_a, component_b]), node_name="Asset", transform=transform)
    glb_path.write_bytes(scene.export(file_type="glb"))

    config = _config()
    config["obstacles"] = [{"name": "Asset", "kind": "structure",
                            "glb": {"path": glb_path.name, "origin_enz_m": [250050, 450000, 0]}}]
    project = _project(_write(tmp_path, config))
    assert len(project.obstacles[0].meshes) == 2
    check = project.report["obstacle_checks"][0]
    assert check["result"] == "containment"
    point = np.asarray(check["obstacle_point_enz_m"])
    assert np.all(np.abs(point - [250051, 450002, 3]) <= [0.25, 0.75, 0.5])


def test_open_glb_geometry_is_rejected(tmp_path):
    path = tmp_path / "surface.glb"
    patch = trimesh.Trimesh(vertices=[[0, 0, 0], [1, 0, 0], [0, 1, 0]],
                            faces=[[0, 1, 2]], process=False)
    path.write_bytes(trimesh.Scene(patch).export(file_type="glb"))
    spec = {"name": "Surface", "kind": "structure", "glb": {"path": path.name, "origin_enz_m": [0, 0, 0]}}
    with pytest.raises(ValueError, match="closed solid meshes"):
        load_obstacles([spec], tmp_path, np.zeros(3))


def test_bearing_rotates_original_box_extents(tmp_path):
    spec = {"name": "Box", "kind": "structure",
            "box": {"center_enz_m": [10, 20, 30], "size_m": [8, 4, 2], "bearing_deg": 90}}
    obstacle, = load_obstacles([spec], tmp_path, np.array([10, 20, 30]))
    assert obstacle.meshes[0].bounds == pytest.approx(np.array([[-4, -2, -1], [4, 2, 1]]))


def test_obstacles_are_checked_against_auxiliary_shafts(tmp_path):
    config = json.loads((EXAMPLES / "auxiliaries.json").read_text())
    config["landxml"] = str(EXAMPLES / config["landxml"])
    config["tunnels"][0]["section"] = str(EXAMPLES / config["tunnels"][0]["section"])
    config["auxiliaries"] = [config["auxiliaries"][-1]]
    config["obstacles"] = [{"name": "External shaft intake", "kind": "structure",
                            "box": {"center_enz_m": [250087, 450000, 22], "size_m": [0.5, 0.5, 0.5]}}]
    main_result, shaft_result = _project(_write(tmp_path, config)).report["obstacle_checks"]
    assert main_result["result"] == "clear"
    assert shaft_result["result"] == "containment"
    assert shaft_result["station_basis"] == "shaft_vertical_distance"
    assert shaft_result["internal_station_m"] == pytest.approx(21, abs=0.25)
