"""Optional native 3D view of the two checked tunnel volumes."""

import numpy as np
import pyvista as pv


def _polydata(mesh):
    return pv.PolyData(mesh.vertices, np.column_stack((np.full(len(mesh.faces), 3), mesh.faces)))


def show(tunnels, points, report):
    plotter = pv.Plotter(title="Rail Tunnel Modeler")
    plotter.set_background("#18242d")
    for tunnel, color in zip(tunnels, ("#67b8ed", "#f6a05b")):
        plotter.add_mesh(_polydata(tunnel.outer), color=color, opacity=0.25)
        for part in tunnel.inner:
            plotter.add_mesh(_polydata(part), color=color, opacity=0.8)
    plotter.add_points(np.unique(points, axis=0), color="#ff4866", point_size=14,
                       render_points_as_spheres=True)
    if np.linalg.norm(points[0] - points[1]) > 1e-8:
        plotter.add_mesh(pv.Line(*points), color="#ff4866", line_width=4)
    labels = [f"Clearance: {report['minimum_clearance_m']:.3f} m ({report['result']})"]
    labels.extend(f"{w['tunnel']}: STA {w['displayed_station_m']:.3f} m"
                  for w in report["witnesses"])
    plotter.add_text("\n".join(labels), color="white", font_size=11)
    plotter.add_axes()
    plotter.show()
