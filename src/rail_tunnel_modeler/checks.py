"""Evaluate tunnel pairs, allowing overlap only inside a declared connection box."""

from itertools import combinations

import numpy as np
import trimesh

from .model import CollisionIndex, compare_solids


def _connected_pair(parent, child, child_name, connection, origin):
    status, distance, points = compare_solids(parent, child)
    if status == "clear":
        return "unconnected", distance, points

    center = connection.center - origin
    # Work near the Boolean's origin and keep the original model for display/export.
    portion = child.mesh.copy().apply_translation(-center)
    box = trimesh.creation.box(extents=connection.extents)
    try:
        remaining = portion.difference(box, engine="manifold")
    except (RuntimeError, ValueError) as exc:
        raise ValueError(f"{child_name}: could not trim the declared connection box") from exc
    if remaining.is_empty:
        return "allowed_connection", None, points

    remaining.apply_translation(center)
    pieces = remaining.split(only_watertight=False)
    if not len(pieces) or any(not piece.is_volume for piece in pieces):
        raise ValueError(f"{child_name}: connection trimming did not produce closed solids")
    outside = (compare_solids(parent, CollisionIndex(piece)) for piece in pieces)
    status, distance, points = min(outside, key=lambda result: result[1])
    return ("allowed_connection" if status == "clear" else status), distance, points


def _witnesses(pair, points, origin):
    witnesses = []
    for tunnel, point in zip(pair, points):
        station, displayed = tunnel.station_at(point)
        world, _ = tunnel.alignment.at(station)
        witness = {
            "tunnel": tunnel.name,
            "alignment": tunnel.alignment.name,
            "internal_station_m": station,
            "displayed_station_m": displayed,
            "point_enz_m": (point + origin).tolist(),
            "alignment_elevation_m": float(world[2]),
        }
        if tunnel.vertical:
            witness["station_basis"] = "shaft_vertical_distance"
        witnesses.append(witness)
    return witnesses


def validate_pairs(tunnels, connections, origin):
    results = []
    indexed = [(tunnel, CollisionIndex(tunnel.outer)) for tunnel in tunnels]
    for (first, first_index), (second, second_index) in combinations(indexed, 2):
        connection = connections.get(second.name)
        attached_pair = connection is not None and connection.parent == first.name
        if attached_pair:
            status, distance, points = _connected_pair(first_index, second_index, second.name, connection, origin)
        else:
            status, distance, points = compare_solids(first_index, second_index)
        witnesses = _witnesses((first, second), points, origin)
        elevation_difference = witnesses[0]["alignment_elevation_m"] - witnesses[1]["alignment_elevation_m"]
        result = {
            "result": status,
            "minimum_clearance_m": distance,
            "witnesses": witnesses,
            "upper_alignment_at_witness": (
                None if abs(elevation_difference) <= 1e-6
                else (first.name if elevation_difference > 0 else second.name)
            ),
        }
        if attached_pair:
            result["connection"] = {
                "tunnel": connection.parent,
                "station_m": connection.station,
                "center_enz_m": connection.center.tolist(),
                "box_extents_enz_m": connection.extents.tolist(),
            }
        results.append((result, points))

    def priority(result_and_points):
        report, _ = result_and_points
        distance = report["minimum_clearance_m"]
        severity = {"contact_or_overlap": 0, "containment": 0, "unconnected": 1,
                    "clear": 2, "allowed_connection": 2}
        return severity[report["result"]], np.inf if distance is None else distance

    primary, points = min(results, key=priority)
    return primary, [result for result, _ in results], points
