"""Lay out working space, excavation surfaces, and cover for cut-and-cover boxes."""

from dataclasses import dataclass
from math import isfinite

import numpy as np
import trimesh

from .earthworks import EarthworkCut, cut_slopes, formation_surface, prepare_earthwork
from .loft import section_samples
from .section_core.constants import TunnelType


@dataclass(frozen=True)
class CutAndCoverInput:
    tunnel: str
    alignment: object
    cut: EarthworkCut
    crown_offsets: np.ndarray


@dataclass(frozen=True)
class CutAndCoverModel:
    tunnel: str
    formation: trimesh.Trimesh
    slopes: dict[str, trimesh.Trimesh]
    report: dict


def prepare_cut_and_cover(spec, tunnel, alignment, frame, keyframes):
    if any(keyframe.kind != TunnelType.BOX for keyframe in keyframes):
        raise ValueError(f"{tunnel}: cut_and_cover requires Box sections throughout the tunnel segment")
    working_space = np.asarray(spec.get("working_space_m", [1.0, 1.0]), dtype=float)
    if working_space.shape != (2,) or not np.isfinite(working_space).all() or np.any(working_space < 0):
        raise ValueError("working_space_m must contain two finite nonnegative values [left, right]")
    overdig = float(spec.get("overdig_m", 0))
    if not isfinite(overdig) or overdig < 0:
        raise ValueError("overdig_m must be finite and nonnegative")

    outside, *_ = section_samples(keyframes, frame[0])
    minimum = np.asarray([sample[0].min(axis=0) for sample in outside])
    maximum = np.asarray([sample[0].max(axis=0) for sample in outside])
    toes = np.column_stack((minimum[:, 0] - working_space[0], maximum[:, 0] + working_space[1],
                            minimum[:, 1] - overdig))
    return CutAndCoverInput(tunnel, alignment, prepare_earthwork(spec, frame, toes), maximum[:, 1])


def build_cut_and_cover(inputs, terrain, origin):
    if inputs and terrain is None:
        raise ValueError("Cut-and-cover excavation requires terrain")
    models = []
    for item in inputs:
        slopes, daylight = cut_slopes(item.cut, item.alignment, terrain, origin, item.tunnel)
        formation = formation_surface(item.cut, origin)
        if formation is None:
            raise ValueError(f"{item.tunnel}: could not form a cut-and-cover excavation floor")
        stats = []
        stations, centers, _ = item.cut.frame
        for station, center, toes, crown in zip(stations, centers, item.cut.toes, item.crown_offsets):
            left, right, bottom = toes
            ground = terrain.elevation_at(center[:2] - origin[:2])
            crown_elevation = float(center[2] + crown)
            if ground is not None:
                ground += origin[2]
            cover = None if ground is None else ground - crown_elevation
            stats.append({
                "internal_station_m": float(station),
                "displayed_station_m": float(item.alignment.displayed_station(station)),
                "ground_elevation_m": ground,
                "box_top_elevation_m": crown_elevation,
                "cover_m": cover,
                "formation_elevation_m": float(center[2] + bottom),
                "formation_width_m": float(right - left),
            })
        models.append(CutAndCoverModel(item.tunnel, formation, slopes,
                                      {"tunnel": item.tunnel, "sections": stats, "daylight": daylight}))
    return models
