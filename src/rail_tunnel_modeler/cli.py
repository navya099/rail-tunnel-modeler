"""Model tunnels and check clearance from a project JSON file."""

import argparse
import json
from itertools import cycle
from math import isfinite
from pathlib import Path
from xml.etree.ElementTree import ParseError

import numpy as np
import trimesh
from defusedxml.common import DefusedXmlException

from .auxiliaries import place_auxiliary, shaft_section
from .checks import validate_pairs
from .landxml import LandXML
from .loft import SectionKeyframe
from .model import build_tunnel, sweep_tunnel
from .sections import boundaries, read_section


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
        result.append(SectionKeyframe(station, inner, outer, datum_y))
    return result


def _project(path, glb_path=None, report_path=None, max_step=2.0):
    if not isfinite(max_step) or max_step <= 0:
        raise ValueError("--max-step must be positive and finite")
    config = json.loads(path.read_text(encoding="utf-8-sig"))
    landxml_path = path.parent / config["landxml"]
    segments = config["tunnels"]
    auxiliaries = config.get("auxiliaries", [])
    names = [item["name"] for item in (*segments, *auxiliaries)]
    if not segments or len(names) < 2 or len(set(names)) != len(names):
        raise ValueError("Specify at least one main tunnel and two uniquely named models in total")
    sources = {path.resolve(), landxml_path.resolve()}
    if glb_path and report_path and glb_path.resolve() == report_path.resolve():
        raise ValueError("--glb and --report must use different output paths")

    landxml = LandXML(landxml_path)
    inputs = []
    for spec in segments:
        alignment = landxml.alignment(spec["alignment"], spec.get("profile"))
        start, end = float(spec["start_station"]), float(spec["end_station"])
        keyframes = _section_keyframes(spec, path.parent, sources, start, end)
        frame = alignment.sample(start, end, max_step, [keyframe.station for keyframe in keyframes])
        inputs.append((spec["name"], alignment, frame, keyframes))
    main_by_name = {name: (alignment, frame[0]) for name, alignment, frame, _ in inputs}
    additional_inputs = []
    connections = {}
    for spec in auxiliaries:
        parent_name = spec["connection"]["tunnel"]
        if parent_name not in main_by_name:
            raise ValueError(f"{spec['name']}: connection tunnel must name a main tunnel")
        axis, connection = place_auxiliary(spec, *main_by_name[parent_name])
        keyframes = ([shaft_section(spec)] if axis.vertical
                     else _section_keyframes(spec, path.parent, sources, 0, axis.length))
        frame = axis.sample(max_step, [keyframe.station for keyframe in keyframes])
        additional_inputs.append((axis.name, axis, frame, keyframes))
        connections[axis.name] = connection
    _output(glb_path, sources)
    _output(report_path, sources)

    axes = np.vstack([entry[2][1] for entry in (*inputs, *additional_inputs)])
    origin = (axes.min(axis=0) + axes.max(axis=0)) / 2
    tunnels = [build_tunnel(*entry, origin) for entry in inputs]
    tunnels.extend(sweep_tunnel(*entry, origin, vertical=entry[1].vertical)
                   for entry in additional_inputs)
    primary, checks, points = validate_pairs(tunnels, connections, origin)
    report = {
        **primary,
        "model_origin_enz_m": origin.tolist(),
        "maximum_station_step_m": max_step,
    }
    if connections or len(checks) > 1:
        report["checks"] = checks
    if glb_path:
        _write_glb(glb_path, tunnels, points)
    return report, tunnels, points


def _write_glb(path, tunnels, points):
    scene = trimesh.Scene()
    colors = cycle(([79, 158, 219], [228, 138, 70], [85, 190, 144], [181, 144, 220]))
    for tunnel, color in zip(tunnels, colors):
        outer = tunnel.outer.copy()
        outer.visual = trimesh.visual.TextureVisuals(material=trimesh.visual.material.PBRMaterial(
            baseColorFactor=[*color, 96], alphaMode="BLEND", roughnessFactor=1.0,
        ))
        scene.add_geometry(outer, geom_name=f"{tunnel.name}/outer")
        for i, part in enumerate(tunnel.inner, 1):
            part.visual.face_colors = [*color, 255]
            scene.add_geometry(part, geom_name=f"{tunnel.name}/inner/{i}")
    for i, point in enumerate(points if np.linalg.norm(points[0] - points[1]) > 1e-8 else points[:1], 1):
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
        report, meshes, points = _project(args.project, args.glb, args.report, args.max_step)
        result = json.dumps(report, ensure_ascii=False, indent=2, allow_nan=False)
        if args.report:
            args.report.write_text(result + "\n", encoding="utf-8")
        print(result)
        if args.view:
            show(meshes, points, report)
    except (OSError, ValueError, KeyError, TypeError, AttributeError, ParseError, DefusedXmlException) as exc:
        parser.exit(2, f"Error: {exc}\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
