import json
from pathlib import Path

import numpy as np
import pytest
import trimesh

from rail_tunnel_modeler.cli import _project, main

EXAMPLES = Path(__file__).resolve().parents[1] / "examples"


def _find(report, track, envelope="gauge", facility=None):
    return next(check for check in report["rail_checks"]
                if (check["track"], check["envelope"], check.get("facility")) == (track, envelope, facility))


def _project_file(folder, config):
    config["landxml"] = str(EXAMPLES / config["landxml"])
    for tunnel in config["tunnels"]:
        if "section" in tunnel:
            tunnel["section"] = str(EXAMPLES / tunnel["section"])
    path = folder / "project.json"
    path.write_text(json.dumps(config), encoding="utf-8")
    return path


def test_rail_clearance_report_and_exports(tmp_path):
    report_path = tmp_path / "report.json"
    glb_path = tmp_path / "model.glb"
    assert main([str(EXAMPLES / "rail-clearance.json"), "--report", str(report_path),
                 "--glb", str(glb_path)]) == 0
    report = json.loads(report_path.read_text())
    assert report["result"] == "not_checked"
    assert len(report["rail_checks"]) == 8
    assert _find(report, "Down track")["result"] == "clear"
    outside = _find(report, "Up track")
    assert outside["result"] == "intrusion"
    assert outside["outside_volume_m3"] == pytest.approx(133.5)
    assert 1021 <= outside["location"]["internal_station_m"] <= 1081
    assert _find(report, "Down track", facility="Sign")["result"] == "intrusion"
    sign_vehicle = _find(report, "Down track", "vehicle", "Sign")
    assert sign_vehicle["result"] == "clear"
    assert sign_vehicle["minimum_clearance_m"] == pytest.approx(0.028)
    equipment = _find(report, "Down track", "vehicle", "Equipment")
    assert equipment["result"] == "intrusion"
    assert equipment["location"]["internal_station_m"] == pytest.approx(1064, abs=0.5)
    scene = trimesh.load_scene(glb_path)
    assert {"Main/gauge/Down track", "Main/vehicle/Down track", "Main/facility/Sign",
            "Witness/1"}.issubset(scene.geometry)
    assert all(solid.is_volume for solid in scene.geometry.values())


def test_compartment_column_is_outside_inner_voids(tmp_path):
    config = json.loads((EXAMPLES / "rail-clearance.json").read_text())
    config["tunnels"][0]["section"] = "box.json"
    config["clearances"] = [config["clearances"][0]]
    config["clearances"][0]["track_offset_m"] = 0
    config["facilities"] = []
    report, (tunnel,), _, _ = _project(_project_file(tmp_path, config))
    assert len(tunnel.inner) == 2
    check = _find(report, "Down track")
    assert check["result"] == "intrusion"
    assert check["outside_volume_m3"] > 0


def test_rail_level_changes_through_a_section_transition(tmp_path):
    config = json.loads((EXAMPLES / "rail-clearance.json").read_text())
    payload = json.loads((EXAMPLES / "clearance-box.json").read_text())
    two = [{"payload": payload}, {"payload": {**payload, "RL": 0.9}}]
    snapshot = tmp_path / "sections.json"
    snapshot.write_text(json.dumps(two), encoding="utf-8")
    tunnel = config["tunnels"][0]
    del tunnel["section"]
    tunnel["sections"] = [{"station": 1021, "section": str(snapshot)},
                          {"station": 1081, "section": str(snapshot), "snapshot_index": 1}]
    track = config["clearances"][0]
    del track["vehicle"]
    config["clearances"] = [track]
    config["facilities"] = []
    report, _, _, (gauge,) = _project(_project_file(tmp_path, config))
    world = gauge.mesh.vertices + report["model_origin_enz_m"]
    start = world[np.isclose(world[:, 0], 250021, rtol=0, atol=1e-6)]
    end = world[np.isclose(world[:, 0], 250081, rtol=0, atol=1e-6)]
    assert start[:, 2].min() == pytest.approx(0.522)
    assert end[:, 2].min() == pytest.approx(0.95)


def test_cant_rotates_only_the_configured_profile(tmp_path):
    config = json.loads((EXAMPLES / "rail-clearance.json").read_text())
    track = config["clearances"][0]
    track["vehicle"]["cant_deg"] = 10
    config["clearances"] = [track]
    config["facilities"] = []
    _, _, _, (gauge, vehicle) = _project(_project_file(tmp_path, config))
    # The first contour follows the eastbound alignment; the right side is south.
    floor = vehicle.mesh.vertices[vehicle.mesh.vertices[:, 0] == vehicle.mesh.vertices[:, 0].min()]
    assert floor[:, 2].min() == pytest.approx(0.472 + 0.1 * np.cos(np.deg2rad(10)) - 1.55 * np.sin(np.deg2rad(10)))
    assert gauge.mesh.bounds[0, 2] == pytest.approx(0.522)


def test_track_interval_must_be_inside_host(tmp_path):
    config = json.loads((EXAMPLES / "rail-clearance.json").read_text())
    config["clearances"][0]["start_station"] = 1009
    with pytest.raises(ValueError, match="clearance stations must be within"):
        _project(_project_file(tmp_path, config))
