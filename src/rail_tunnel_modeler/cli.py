"""Model tunnels and check clearance from a project JSON file."""

import argparse
import json
from dataclasses import dataclass
from itertools import cycle
from math import isfinite
from pathlib import Path
from xml.etree.ElementTree import ParseError

import numpy as np
import trimesh
from defusedxml.common import DefusedXmlException

from .auxiliaries import place_auxiliary, shaft_section
from .checks import validate_pairs
from .cut_and_cover import CutAndCoverModel, build_cut_and_cover, prepare_cut_and_cover
from .landxml import LandXML
from .loft import SectionKeyframe
from .model import Tunnel, build_tunnel, sweep_tunnel
from .obstacles import (OBSTACLE_COLOR, Obstacle, check_obstacles, load_obstacles, obstacle_files,
                        obstacle_witness_lines)
from .portals import PortalModel, build_portals, prepare_portal
from .rail_clearance import (RAIL_STYLE, RailShape, check_rail_shapes, facility_shape, rail_intrusion_points,
                             track_range, track_shapes)
from .sections import boundaries, read_section
from .terrain import Terrain, load_terrain
from .terrain_profile import TerrainSections


@dataclass
class ProjectResult:
    report: dict
    tunnels: list[Tunnel]
    points: np.ndarray
    rail_shapes: list[RailShape]
    terrain: Terrain | None
    portals: list[PortalModel]
    obstacles: list[Obstacle]
    cut_and_cover: list[CutAndCoverModel]


def _output(path, source_files):
    if path is not None and path.resolve() in source_files:
        raise ValueError(f"{path}: output path would overwrite an input file")


def _section_keyframes(spec, project_folder, sources, start, end):
    datum = spec["datum"]
    if datum not in {"FL", "RL"}:
        raise ValueError("datum must be FL or RL")
    if "sections" in spec:
        if {"section", "snapshot_index", "outer_boundary"} & spec.keys():
            raise ValueError("Use section or sections, and put section options on each keyframe")
        entries = spec["sections"]
        if not isinstance(entries, list) or not entries:
            raise ValueError("sections must be a nonempty list of station keyframes")
        stations = [float(entry["station"]) for entry in entries]
        if (any(not start <= station <= end for station in stations)
                or any(b - a <= 1e-7 for a, b in zip(stations, stations[1:]))):
            raise ValueError("Section keyframe stations must increase within the modeled interval")
    else:
        entries, stations = [spec], [start]

    result = []
    for entry, station in zip(entries, stations):
        section_path = project_folder / entry["section"]
        sources.add(section_path.resolve())
        index = entry.get("snapshot_index", 0)
        if type(index) is not int:
            raise ValueError("snapshot_index must be an integer")
        section = read_section(section_path, index)
        datum_y = getattr(section, datum)
        if not isfinite(datum_y):
            raise ValueError("Section datum must be finite")
        inner, outer = boundaries(section, entry.get("outer_boundary"))
        result.append(SectionKeyframe(station, inner, outer, datum_y, section.RL - datum_y, section.kind))
    return result


def _rail_inputs(config, main_names):
    tracks = {}
    for track in config.get("clearances", []):
        host = track["tunnel"]
        if host not in main_names:
            raise ValueError(f"{track['name']}: clearance tunnel must name a main tunnel")
        group = tracks.setdefault(host, {})
        if track["name"] in group:
            raise ValueError(f"{host}: clearance track names must be unique")
        group[track["name"]] = track
    facilities = {}
    for facility in config.get("facilities", []):
        host = facility["tunnel"]
        if host not in tracks:
            raise ValueError(f"{facility['name']}: facility needs a clearance track in its tunnel")
        group = facilities.setdefault(host, {})
        if facility["name"] in group:
            raise ValueError(f"{host}: facility names must be unique")
        group[facility["name"]] = facility
    return tracks, facilities


def _project(path, glb_path=None, report_path=None, max_step=2.0):
    if not isfinite(max_step) or max_step <= 0:
        raise ValueError("--max-step must be positive and finite")
    config = json.loads(path.read_text(encoding="utf-8-sig"))
    landxml_path = path.parent / config["landxml"]
    segments = config["tunnels"]
    auxiliaries = config.get("auxiliaries", [])
    terrain_spec = config.get("terrain")
    portal_specs = config.get("portals", [])
    obstacle_specs = config.get("obstacles", [])
    names = [item["name"] for item in (*segments, *auxiliaries)]
    if not segments or len(set(names)) != len(names):
        raise ValueError("Specify at least one main tunnel and use unique model names")
    if terrain_spec is None and any("cut_and_cover" in spec for spec in segments):
        raise ValueError("Cut-and-cover excavation requires terrain")
    tracks, facilities = _rail_inputs(config, {segment["name"] for segment in segments})
    if len(names) < 2 and not (tracks or terrain_spec is not None or portal_specs or obstacle_specs):
        raise ValueError("A single tunnel needs a clearance track, terrain, portals, or obstacles")
    if (len({spec["name"] for spec in portal_specs}) != len(portal_specs)
            or len({(spec["tunnel"], spec["end"]) for spec in portal_specs}) != len(portal_specs)):
        raise ValueError("Portals require unique names and distinct tunnel ends")
    sources = {path.resolve(), landxml_path.resolve()} | obstacle_files(obstacle_specs, path.parent)
    if terrain_spec is not None and "path" in terrain_spec:
        sources.add((path.parent / terrain_spec["path"]).resolve())
    if glb_path and report_path and glb_path.resolve() == report_path.resolve():
        raise ValueError("--glb and --report must use different output paths")

    landxml = LandXML(landxml_path)
    inputs = []
    excavations = []
    for spec in segments:
        alignment = landxml.alignment(spec["alignment"], spec.get("profile"))
        start, end = float(spec["start_station"]), float(spec["end_station"])
        keyframes = _section_keyframes(spec, path.parent, sources, start, end)
        section_stations = [keyframe.station for keyframe in keyframes]
        for track in tracks.get(spec["name"], {}).values():
            section_stations.extend(track_range(track, start, end))
        frame = alignment.sample(start, end, max_step, section_stations)
        inputs.append((spec["name"], alignment, frame, keyframes))
        if "cut_and_cover" in spec:
            excavations.append(prepare_cut_and_cover(spec["cut_and_cover"], *inputs[-1]))
    main_by_name = {name: (alignment, frame, keyframes) for name, alignment, frame, keyframes in inputs}
    portal_inputs = [prepare_portal(spec, main_by_name, max_step) for spec in portal_specs]
    additional_inputs = []
    connections = {}
    for spec in auxiliaries:
        parent_name = spec["connection"]["tunnel"]
        if parent_name not in main_by_name:
            raise ValueError(f"{spec['name']}: connection tunnel must name a main tunnel")
        parent_axis, parent_frame, _ = main_by_name[parent_name]
        axis, connection = place_auxiliary(spec, parent_axis, parent_frame[0])
        keyframes = ([shaft_section(spec)] if axis.vertical
                     else _section_keyframes(spec, path.parent, sources, 0, axis.length))
        frame = axis.sample(max_step, [keyframe.station for keyframe in keyframes])
        additional_inputs.append((axis.name, axis, frame, keyframes))
        connections[axis.name] = connection
    _output(glb_path, sources)
    _output(report_path, sources)

    centerlines = [entry[2][1] for entry in (*inputs, *additional_inputs)]
    axes = np.vstack(centerlines)
    origin = (axes.min(axis=0) + axes.max(axis=0)) / 2
    tunnels = [build_tunnel(*entry, origin) for entry in inputs]
    tunnels.extend(sweep_tunnel(*entry, origin, vertical=entry[1].vertical)
                   for entry in additional_inputs)
    if len(tunnels) > 1:
        primary, checks, points = validate_pairs(tunnels, connections, origin)
    else:
        primary = {"result": "not_checked", "minimum_clearance_m": None, "witnesses": [],
                   "upper_alignment_at_witness": None}
        checks, points = [], np.empty((0, 3))
    report = {
        **primary,
        "model_origin_enz_m": origin.tolist(),
        "maximum_station_step_m": max_step,
    }
    if connections or len(checks) > 1:
        report["checks"] = checks
    rail_shapes = []
    for tunnel, (name, _, frame, keyframes) in zip(tunnels, inputs):
        for track in tracks.get(name, {}).values():
            rail_shapes.extend(track_shapes(track, tunnel, frame, keyframes))
        for facility in facilities.get(name, {}).values():
            rail_shapes.append(facility_shape(facility, tunnel, origin))
    if rail_shapes:
        report["rail_checks"] = check_rail_shapes(rail_shapes, {t.name: t for t in tunnels}, origin)
    terrain = None
    cut_terrain = None
    corridors = [portal.approach.corridor for portal in portal_inputs if portal.approach is not None]
    corridors.extend(excavation.cut.corridor for excavation in excavations)
    if terrain_spec is not None:
        terrain = load_terrain(terrain_spec, path.parent, landxml_path, landxml, centerlines, origin,
                               extra_corridors=corridors)
        report["terrain"] = terrain.summary(origin)
        cut_terrain = terrain
        if corridors and terrain.source == "dem" and terrain_spec.get("dem_stride", 1) > 1:
            cut_terrain = load_terrain({**terrain_spec, "dem_stride": 1}, path.parent, landxml_path,
                                       landxml, [], origin, extra_corridors=corridors, allow_empty=True)
    terrain_sections = TerrainSections(cut_terrain.tiles) if corridors and cut_terrain is not None else None
    portals = build_portals(portal_inputs, terrain_sections, origin)
    if portals:
        report["portals"] = [portal.report for portal in portals]
    cut_and_cover = build_cut_and_cover(excavations, terrain_sections, origin)
    if cut_and_cover:
        report["cut_and_cover"] = [excavation.report for excavation in cut_and_cover]
    obstacles = load_obstacles(obstacle_specs, path.parent, origin, config.get("obstacle_clearance_m", 0))
    if obstacles:
        report["obstacle_checks"] = check_obstacles(tunnels, obstacles, origin)
    result = ProjectResult(report, tunnels, points, rail_shapes, terrain, portals, obstacles, cut_and_cover)
    if glb_path:
        _write_glb(glb_path, result)
    return result


def _colored_mesh(mesh, color):
    result = mesh.copy()
    result.visual = trimesh.visual.TextureVisuals(material=trimesh.visual.material.PBRMaterial(
        baseColorFactor=color, alphaMode="BLEND" if color[3] < 255 else "OPAQUE", roughnessFactor=1.0,
    ))
    return result


def _write_glb(path, result):
    scene = trimesh.Scene()
    colors = cycle(([79, 158, 219], [228, 138, 70], [85, 190, 144], [181, 144, 220]))
    for tunnel, color in zip(result.tunnels, colors):
        outer = _colored_mesh(tunnel.outer, [*color, 96])
        scene.add_geometry(outer, geom_name=f"{tunnel.name}/outer")
        for i, part in enumerate(tunnel.inner, 1):
            inner = _colored_mesh(part, [*color, 32 if result.rail_shapes else 255])
            scene.add_geometry(inner, geom_name=f"{tunnel.name}/inner/{i}")
    for shape in result.rail_shapes:
        color, _ = RAIL_STYLE[shape.kind]
        mesh = _colored_mesh(shape.mesh, color)
        scene.add_geometry(mesh, geom_name=f"{shape.tunnel}/{shape.kind}/{shape.name}")
    if result.terrain:
        for i, tile in enumerate(result.terrain.tiles, 1):
            scene.add_geometry(_colored_mesh(tile, [175, 165, 132, 112]), geom_name=f"Terrain/{i}")
    for portal in result.portals:
        scene.add_geometry(_colored_mesh(portal.wall, [185, 194, 202, 255]),
                           geom_name=f"Portal/{portal.name}/Headwall")
        for side, slope in portal.slopes.items():
            scene.add_geometry(_colored_mesh(slope, [177, 107, 70, 255]),
                               geom_name=f"Portal/{portal.name}/Cut/{side}")
    for excavation in result.cut_and_cover:
        prefix = f"CutAndCover/{excavation.tunnel}"
        scene.add_geometry(_colored_mesh(excavation.formation, [222, 177, 106, 255]),
                           geom_name=f"{prefix}/Formation")
        for side, slope in excavation.slopes.items():
            scene.add_geometry(_colored_mesh(slope, [177, 107, 70, 255]), geom_name=f"{prefix}/Cut/{side}")
    for obstacle in result.obstacles:
        for i, mesh in enumerate(obstacle.meshes, 1):
            scene.add_geometry(_colored_mesh(mesh, OBSTACLE_COLOR), geom_name=f"Obstacle/{obstacle.name}/{i}")
    points = result.points
    if len(points) == 2 and np.linalg.norm(points[0] - points[1]) <= 1e-8:
        points = points[:1]
    obstacle_points = [point for line in obstacle_witness_lines(result.report)
                       for point in (line[:1] if np.linalg.norm(line[0] - line[1]) <= 1e-8 else line)]
    for i, point in enumerate([*points, *rail_intrusion_points(result.report), *obstacle_points], 1):
        marker = trimesh.creation.icosphere(subdivisions=2, radius=0.25)
        marker.apply_translation(point)
        marker.visual.face_colors = [255, 40, 70, 255]
        scene.add_geometry(marker, geom_name=f"Witness/{i}")
    # glTF uses Y up; report coordinates remain East, North, Elevation.
    scene.apply_transform(trimesh.transformations.rotation_matrix(-np.pi / 2, [1, 0, 0]))
    path.write_bytes(scene.export(file_type="glb"))


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("project", type=Path, help="Project JSON file")
    parser.add_argument("--glb", type=Path, help="Export a 3D model in local coordinates")
    parser.add_argument("--report", type=Path, help="Save the JSON clearance report")
    parser.add_argument("--max-step", type=float, default=2.0, help="Maximum station step in meters")
    parser.add_argument("--view", action="store_true", help="Open the desktop 3D viewer (viewer extra)")
    args = parser.parse_args(argv)
    try:
        if args.view:
            try:
                from .viewer import show
            except ModuleNotFoundError as exc:
                if exc.name != "pyvista":
                    raise
                raise ValueError('Install the viewer with pip install ".[viewer]"') from exc
        project = _project(args.project, args.glb, args.report, args.max_step)
        result = json.dumps(project.report, ensure_ascii=False, indent=2, allow_nan=False)
        if args.report:
            args.report.write_text(result + "\n", encoding="utf-8")
        print(result)
        if args.view:
            show(project)
    except (OSError, ValueError, KeyError, TypeError, AttributeError, ParseError, DefusedXmlException) as exc:
        parser.exit(2, f"Error: {exc}\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
