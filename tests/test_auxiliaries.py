import json
from pathlib import Path

import numpy as np
import pytest
import trimesh

from rail_tunnel_modeler.cli import _project

EXAMPLES = Path(__file__).resolve().parents[1] / "examples"


def _pair(report, first, second):
    return next(check for check in report["checks"]
                if [witness["tunnel"] for witness in check["witnesses"]] == [first, second])


def _aux_config():
    return json.loads((EXAMPLES / "auxiliaries.json").read_text())


def _write_config(folder, config):
    config["landxml"] = str(EXAMPLES / config["landxml"])
    for segment in config["tunnels"] + config["auxiliaries"]:
        if "section" in segment:
            segment["section"] = str(EXAMPLES / segment["section"])
    path = folder / "project.json"
    path.write_text(json.dumps(config), encoding="utf-8")
    return path


def test_all_auxiliaries_and_allowed_connections(tmp_path):
    glb_path = tmp_path / "auxiliaries.glb"
    project = _project(EXAMPLES / "auxiliaries.json", glb_path=glb_path)
    report, tunnels = project.report, project.tunnels
    assert len(tunnels) == 6
    assert len(report["checks"]) == 15
    for tunnel in tunnels[1:]:
        check = _pair(report, "Main", tunnel.name)
        assert check["result"] == "allowed_connection"
        assert check["minimum_clearance_m"] > 0
        assert all(mesh.is_volume for mesh in (tunnel.outer, *tunnel.inner))
    shaft = tunnels[-1]
    assert shaft.stations.tolist() == pytest.approx([0, 29])
    assert np.allclose(shaft.centers[0, :2], shaft.centers[1, :2])
    level = shaft.centers[0] + [1.3, 0, 5]
    assert shaft.station_at(level) == pytest.approx((5, 5))
    assert _pair(report, "Main", shaft.name)["witnesses"][1]["station_basis"] == "shaft_vertical_distance"
    scene = trimesh.load_scene(glb_path)
    assert all(f"{tunnel.name}/outer" in scene.geometry for tunnel in tunnels)


def test_overlap_outside_box_remains_a_clash(tmp_path):
    config = _aux_config()
    branch = config["auxiliaries"][2]
    branch["connection"]["allow_overlap_box_m"][1] = 4
    config["auxiliaries"] = [branch]
    report = _project(_write_config(tmp_path, config)).report
    assert report["result"] == "contact_or_overlap"
    assert report["minimum_clearance_m"] == 0
    assert report["witnesses"][0]["tunnel"] == "Main"
    check = _pair(report, "Main", "Branch")
    assert check["result"] == "contact_or_overlap"


def test_connection_box_applies_only_to_the_declared_pair(tmp_path):
    config = _aux_config()
    other = json.loads((EXAMPLES / "crossing.json").read_text())["tunnels"][1]
    config["tunnels"].append(other)
    shaft = config["auxiliaries"][-1]
    shaft["connection"]["station"] = 1050
    shaft["connection"]["allow_overlap_box_m"][2] = 40
    config["auxiliaries"] = [shaft]
    report = _project(_write_config(tmp_path, config)).report
    assert report["result"] == "contact_or_overlap"
    assert _pair(report, "Main", shaft["name"])["result"] == "allowed_connection"
    assert _pair(report, "Box", shaft["name"])["result"] == "contact_or_overlap"


def test_missing_contact_is_reported_as_unconnected(tmp_path):
    config = _aux_config()
    gallery = config["auxiliaries"][1]
    gallery["connection"]["offset_m"] = [14, 1]
    gallery["end_enz_m"][1] -= 18
    config["auxiliaries"] = [gallery]
    report = _project(_write_config(tmp_path, config)).report
    assert report["result"] == "unconnected"
    assert report["minimum_clearance_m"] > 0


def test_box_that_covers_the_entire_auxiliary(tmp_path):
    config = _aux_config()
    shaft = config["auxiliaries"][-1]
    shaft["connection"]["allow_overlap_box_m"][2] = 100
    config["auxiliaries"] = [shaft]
    report = _project(_write_config(tmp_path, config)).report
    assert report["result"] == "allowed_connection"
    assert report["minimum_clearance_m"] is None


def test_descending_shaft_and_connection_validation(tmp_path):
    config = _aux_config()
    shaft = config["auxiliaries"][-1]
    shaft["end_elevation_m"] = -20
    config["auxiliaries"] = [shaft]
    path = _write_config(tmp_path, config)
    _, model = _project(path).tunnels
    assert model.outer.is_volume and model.inner[0].is_volume
    assert model.stations.tolist() == pytest.approx([0, 21])
    shaft["connection"]["station"] = 1101
    with pytest.raises(ValueError, match="connection station must be within its main tunnel"):
        _project(_write_config(tmp_path, config))
