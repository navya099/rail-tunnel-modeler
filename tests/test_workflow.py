import json
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from xml.etree.ElementTree import fromstring

import numpy as np
import pytest
import trimesh
from shapely.geometry import box

from rail_tunnel_modeler.cli import _project, main
from rail_tunnel_modeler.landxml import LandXML, _plan_element
from rail_tunnel_modeler.loft import SectionKeyframe, section_samples
from rail_tunnel_modeler.model import compare
from rail_tunnel_modeler.section_core.models import TunnelParams
from rail_tunnel_modeler.sections import boundaries, read_section

EXAMPLES = Path(__file__).resolve().parents[1] / "examples"


def test_crossing_from_snapshot_to_report_and_glb(tmp_path):
    report_path = tmp_path / "report.json"
    glb_path = tmp_path / "model.glb"
    assert main([str(EXAMPLES / "crossing.json"), "--report", str(report_path), "--glb", str(glb_path)]) == 0
    report = json.loads(report_path.read_text())
    assert report["result"] == "clear"
    assert report["minimum_clearance_m"] == pytest.approx(2.46223809)
    assert report["upper_alignment_at_witness"] == "Box"
    lower, upper = report["witnesses"]
    assert 1045.6 <= lower["internal_station_m"] <= 1054.4
    assert 2049.9 < upper["internal_station_m"] < 2050.1
    assert np.linalg.norm(np.subtract(lower["point_enz_m"], upper["point_enz_m"])) == pytest.approx(
        report["minimum_clearance_m"]
    )
    model = trimesh.load_scene(glb_path)
    expected = {"NATM/outer", "NATM/inner/1", "Box/outer", "Box/inner/1", "Box/inner/2",
                "Witness/1", "Witness/2"}
    assert set(model.geometry) == expected
    assert all(solid.is_volume for solid in model.geometry.values())


def test_crossing_that_intersects(tmp_path):
    project = json.loads((EXAMPLES / "crossing.json").read_text())
    for tunnel in project["tunnels"]:
        tunnel["section"] = str(EXAMPLES / tunnel["section"])
    (tmp_path / "crossing.xml").write_text(
        (EXAMPLES / "crossing.xml").read_text().replace("2000 12", "2000 8").replace("2100 12", "2100 8")
    )
    project_path = tmp_path / "project.json"
    project_path.write_text(json.dumps(project))
    report_path = tmp_path / "report.json"
    assert main([str(project_path), "--report", str(report_path)]) == 0
    report = json.loads(report_path.read_text())
    assert report["result"] == "contact_or_overlap"
    assert report["minimum_clearance_m"] == 0
    a, b = report["witnesses"]
    assert a["point_enz_m"] == b["point_enz_m"]
    assert 1045.6 <= a["internal_station_m"] <= 1054.4
    assert 2040 <= b["internal_station_m"] <= 2060


@pytest.mark.parametrize("offset, size, expected, distance", [
    ([0, 0, 0], [1, 1, 1], "containment", 0),
    ([2, 0, 0], [2, 2, 2], "contact_or_overlap", 0),
    ([3, 4, 0], [2, 2, 2], "clear", np.sqrt(5)),
])
def test_solid_pair(offset, size, expected, distance):
    a = trimesh.creation.box()
    a.apply_scale(2)
    b = trimesh.creation.box(extents=size)
    b.apply_translation(offset)
    status, found, points = compare(SimpleNamespace(outer=a), SimpleNamespace(outer=b))
    assert status == expected
    assert found == pytest.approx(distance)
    assert np.linalg.norm(np.subtract(*points)) == pytest.approx(distance)


def test_invalid_natm_outer_is_rejected():
    with pytest.raises(ValueError, match="positive side thickness"):
        boundaries(TunnelParams(ts=0))


def test_curve_and_spiral_bearings():
    curve = _plan_element(fromstring(
        '<Curve length="157.079632679" radius="100" rot="ccw">'
        '<Start>0 0</Start><Center>100 0</Center><End>100 100</End></Curve>'
    ))
    point, bearing = curve.at(curve.length / 2)
    assert point == pytest.approx([100 / np.sqrt(2), 100 - 100 / np.sqrt(2)])
    assert bearing == pytest.approx(np.pi / 4)
    clockwise = _plan_element(fromstring(
        '<Curve length="157.079632679" radius="100" rot="cw">'
        '<Start>0 0</Start><Center>-100 0</Center><End>-100 100</End></Curve>'
    ))
    assert clockwise.at(clockwise.length / 2)[0] == pytest.approx([point[0], -point[1]])
    spiral = _plan_element(fromstring(
        '<Spiral length="40" radiusStart="INF" radiusEnd="100" rot="ccw" spiType="clothoid">'
        '<Start>0 0</Start><End>2.659057311 39.840296023</End></Spiral>'
    ))
    assert spiral.at(0)[1] == pytest.approx(0, abs=1e-7)
    assert spiral.at(40)[1] == pytest.approx(0.2, abs=1e-7)


def test_vertical_curve_and_station_equation(tmp_path):
    xml = (EXAMPLES / "crossing.xml").read_text().replace(
        "<PVI>1000 0</PVI><PVI>1100 0</PVI>",
        '<PVI>1000 0</PVI><ParaCurve length="20">1050 5</ParaCurve><PVI>1100 0</PVI>',
    ).replace(
        '<Alignment name="Lower" staStart="1000" length="100">',
        '<Alignment name="Lower" staStart="1000" length="100">'
        '<StaEquation staInternal="1050" staBack="1050" staAhead="1200"/>',
    )
    path = tmp_path / "curve.xml"
    path.write_text(xml)
    alignment = LandXML(path).alignment("Lower", "Design")
    stations, points, _ = alignment.sample(1000, 1100, 20)
    assert 1040 in stations
    assert 1060 in stations
    assert points[stations == 1050][0, 2] == pytest.approx(4.5)
    assert alignment.displayed_station(1049) == 1049
    assert alignment.displayed_station(1050) == 1200
    assert alignment.displayed_station(1090) == 1240


def test_multi_arch_transition_workflow(tmp_path):
    glb_path = tmp_path / "transition.glb"
    project = _project(EXAMPLES / "section-transition.json", glb_path=glb_path, max_step=7)
    lower, upper = project.tunnels
    report, points = project.report, project.points
    assert report["result"] == "clear"
    assert report["minimum_clearance_m"] == pytest.approx(2.0562, abs=0.01)
    assert np.linalg.norm(np.subtract(*points)) == pytest.approx(report["minimum_clearance_m"])
    assert {1031, 1073}.issubset(lower.stations)
    assert len(lower.inner) == 2
    assert len(upper.inner) == 3
    assert all(mesh.is_volume for tunnel in (lower, upper) for mesh in (tunnel.outer, *tunnel.inner))

    rings = lower.outer.vertices.reshape(len(lower.stations), -1, 3)
    start = np.where(lower.stations == 1031)[0][0]
    end = np.where(lower.stations == 1073)[0][0]
    assert rings[0, :, 1:] == pytest.approx(rings[start, :, 1:])
    assert rings[-1, :, 1:] == pytest.approx(rings[end, :, 1:])
    middle = (start + end) // 2
    assert lower.stations[middle] == pytest.approx(1052)
    assert rings[middle, :, 1:] == pytest.approx((rings[start, :, 1:] + rings[end, :, 1:]) / 2)
    glb = trimesh.load_scene(glb_path)
    assert {"2-Arch/inner/2", "3-Arch/inner/3"}.issubset(glb.geometry)
    assert all(solid.is_volume for solid in glb.geometry.values())


def test_multi_arch_geometry_rejects_overlap_and_accepts_defined_envelope():
    params = read_section(EXAMPLES / "multi-arch.json", 2)
    inner, outer = boundaries(params)
    assert len(inner.geoms) == 3
    assert outer.equals(outer.convex_hull)
    custom = box(-22, -2, 22, 12)
    assert boundaries(params, list(custom.exterior.coords))[1].equals(custom)
    with pytest.raises(ValueError, match="inner inside outer"):
        boundaries(params, list(box(-1, -1, 1, 1).exterior.coords))
    with pytest.raises(ValueError, match="arch interiors overlap"):
        boundaries(replace(params, gap_L=0.5, gap_R=0.5))


def test_section_transition_matches_different_rings_and_datums():
    start_inner, start_outer = boundaries(TunnelParams())
    end_inner, end_outer = box(-4, 2, 4, 8), box(-4.4, 1.6, 4.4, 8.4)
    outer, inner = section_samples(
        [SectionKeyframe(0, start_inner, start_outer, 0),
         SectionKeyframe(20, end_inner, end_outer, 2)], [0, 4, 20]
    )
    assert len(outer[0][0]) == len(outer[2][0])
    assert inner[2][0][:, 1].min() == pytest.approx(0)
    assert inner[2][0][:, 1].max() == pytest.approx(6)
    assert outer[1][0] == pytest.approx(0.8 * outer[0][0] + 0.2 * outer[2][0])


def test_transition_rejects_changed_compartment_count():
    params = read_section(EXAMPLES / "box.json", 0)
    single = boundaries(replace(params, box_columns=0))
    double = boundaries(params)
    with pytest.raises(ValueError, match="same number of compartments"):
        section_samples([SectionKeyframe(0, *single, 0), SectionKeyframe(20, *double, 0)], [0, 20])
