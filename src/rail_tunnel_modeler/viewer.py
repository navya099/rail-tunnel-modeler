"""Optional native 3D view of the checked tunnel volumes."""

from itertools import cycle

import numpy as np
import pyvista as pv

from .obstacles import OBSTACLE_COLOR, obstacle_witness_lines
from .rail_clearance import RAIL_STYLE, rail_intrusion_points


def _polydata(mesh):
    return pv.PolyData(mesh.vertices, np.column_stack((np.full(len(mesh.faces), 3), mesh.faces)))


def show(project):
    tunnels, points, report, rail_shapes = project.tunnels, project.points, project.report, project.rail_shapes
    plotter = pv.Plotter(title="Rail Tunnel Modeler")
    plotter.set_background("#18242d")
    colors = cycle(("#67b8ed", "#f6a05b", "#55be90", "#b590dc"))
    for tunnel, color in zip(tunnels, colors):
        plotter.add_mesh(_polydata(tunnel.outer), color=color, opacity=0.25, label=tunnel.name)
        for part in tunnel.inner:
            plotter.add_mesh(_polydata(part), color=color, opacity=0.12 if rail_shapes else 0.8)
    labels_used = set()
    for shape in rail_shapes:
        color, label = RAIL_STYLE[shape.kind]
        plotter.add_mesh(_polydata(shape.mesh), color=np.array(color[:3]) / 255.0,
                         opacity=color[3] / 255.0, label=label if label not in labels_used else None)
        labels_used.add(label)
    obstacle_lines = obstacle_witness_lines(report)
    markers = [*points, *rail_intrusion_points(report), *(point for line in obstacle_lines for point in line)]
    if markers:
        plotter.add_points(np.unique(markers, axis=0), color="#ff4866", point_size=14,
                           render_points_as_spheres=True)
    if len(points) == 2 and np.linalg.norm(points[0] - points[1]) > 1e-8:
        plotter.add_mesh(pv.Line(*points), color="#ff4866", line_width=4)
    for line in obstacle_lines:
        if np.linalg.norm(line[0] - line[1]) > 1e-8:
            plotter.add_mesh(pv.Line(*line), color="#ff4866", line_width=4)
    distance = report["minimum_clearance_m"]
    labels = []
    if report["result"] != "not_checked":
        labels.append(report["result"] if distance is None else f"Clearance: {distance:.3f} m ({report['result']})")
    labels.extend(f"{w['tunnel']}: STA {w['displayed_station_m']:.3f} m"
                  for w in report["witnesses"])
    if rail_shapes:
        intrusions = [check for check in report["rail_checks"] if check["result"] == "intrusion"]
        labels.append(f"Rail intrusions: {len(intrusions)}")
        for check in intrusions[:3]:
            location = check["location"]["displayed_station_m"]
            name = check.get("facility", check["track"])
            labels.append(f"{name}: STA {location:.3f} m")
    cut_label = "Cut slope"
    for portal in project.portals:
        plotter.add_mesh(_polydata(portal.wall), color="#b9c2ca", label=portal.name)
        for slope in portal.slopes.values():
            plotter.add_mesh(_polydata(slope), color="#b16b46", label=cut_label)
            cut_label = None
    if project.obstacles:
        obstacle_label = "External objects"
        color = np.array(OBSTACLE_COLOR[:3]) / 255.0
        for obstacle in project.obstacles:
            for mesh in obstacle.meshes:
                plotter.add_mesh(_polydata(mesh), color=color, opacity=0.86, label=obstacle_label)
                obstacle_label = None
        labels.append(f"External conflicts: {len(obstacle_lines)}")
    if project.terrain:
        terrain_actors = []
        for index, tile in enumerate(project.terrain.tiles):
            actor = plotter.add_mesh(_polydata(tile), color="#b3a781", opacity=0.36,
                                     label="Terrain" if index == 0 else None)
            terrain_actors.append(actor)

        def toggle_terrain():
            visible = not terrain_actors[0].GetVisibility()
            for actor in terrain_actors:
                actor.SetVisibility(visible)
            plotter.render()

        plotter.add_key_event("t", toggle_terrain)
        labels.append("T: Toggle terrain")
    if labels:
        plotter.add_text("\n".join(labels), color="white", font_size=11)
    if len(tunnels) > 2 or rail_shapes or project.terrain or project.portals or project.obstacles:
        plotter.add_legend(loc="upper right")
    plotter.add_axes()
    plotter.show()
