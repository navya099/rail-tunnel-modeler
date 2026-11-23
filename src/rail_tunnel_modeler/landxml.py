"""Evaluate metric LandXML alignments by continuous station."""

from bisect import bisect_left, bisect_right
from dataclasses import dataclass
from math import atan2, ceil, dist, isfinite, pi

import numpy as np
from defusedxml.ElementTree import parse
from scipy.integrate import quad_vec


def _xy(element, name):
    # LandXML coordinate text is Northing Easting [Elevation].
    north, east, *_ = map(float, element.findtext(name, "").split())
    if not isfinite(east) or not isfinite(north):
        raise ValueError(f"{name}: expected finite coordinates")
    return np.array([east, north])


def _bearing(vector):
    return atan2(vector[1], vector[0])


def _direction(angle):
    return np.array([np.cos(angle), np.sin(angle)])


@dataclass
class PlanElement:
    start: np.ndarray
    end: np.ndarray
    length: float
    azimuth: float
    k_start: float
    k_end: float

    def at(self, offset):
        turn = self.k_start * offset + (self.k_end - self.k_start) * offset**2 / (2 * self.length)
        tangent = self.azimuth + turn
        if self.k_start != self.k_end:
            def direction(s):
                return _direction(self.azimuth + self.k_start * s
                                  + (self.k_end - self.k_start) * s**2 / (2 * self.length))

            delta, _ = quad_vec(direction, 0, offset)
        elif self.k_start:
            # Stable circular chord formula, including small sweeps.
            delta = offset * np.sinc(turn / (2 * pi)) * _direction(self.azimuth + turn / 2)
        else:
            delta = offset * _direction(self.azimuth)
        return self.start + delta, tangent


def _plan_element(element):
    start, end = _xy(element, "Start"), _xy(element, "End")
    chord = end - start
    length = float(element.get("length", np.linalg.norm(chord)))
    if not isfinite(length) or length <= 0:
        raise ValueError(f"{element.tag}: length must be positive and finite")
    if element.tag == "Line":
        plan = PlanElement(start, end, length, _bearing(chord), 0.0, 0.0)
    elif element.tag in {"Curve", "Spiral"}:
        rotation = element.get("rot", "").lower()
        if rotation not in {"cw", "ccw"}:
            raise ValueError(f"{element.tag}: rot must be cw or ccw")
        sign = 1 if rotation == "ccw" else -1

        def curvature(key):
            radius = float(element.attrib[key])
            if radius <= 0 or np.isnan(radius):
                raise ValueError(f"{element.tag}: {key} must be positive")
            return sign / radius

        if element.tag == "Curve":
            k = curvature("radius")
            if not k:
                raise ValueError("Curve: radius must be finite")
            center = _xy(element, "Center")
            if abs(dist(start, center) - 1 / abs(k)) > 0.005:
                raise ValueError("Curve: radius does not match its center and start")
            plan = PlanElement(start, end, length, _bearing(start - center) + sign * pi / 2, k, k)
        else:
            if element.get("spiType", "").lower() != "clothoid":
                raise ValueError("Spiral: only clothoids are supported")
            k_start, k_end = curvature("radiusStart"), curvature("radiusEnd")
            plan = PlanElement(start, end, length, 0.0, k_start, k_end)
            displacement, _ = plan.at(length)
            plan.azimuth = _bearing(chord) - _bearing(displacement - start)
    else:
        raise ValueError(f"Unsupported LandXML plan element {element.tag}")
    if dist(plan.at(length)[0], end) > 0.005:
        raise ValueError(f"{element.tag}: calculated end differs from declared end by more than 5 mm")
    return plan


@dataclass
class Profile:
    stations: list[float]
    elevations: list[float]
    grades: np.ndarray
    curves: dict[int, float]

    def at(self, station):
        index = max(0, min(bisect_right(self.stations, station) - 1, len(self.grades) - 1))
        for pvi in (index, index + 1):
            if pvi not in self.curves:
                continue
            length = self.curves[pvi]
            begin = self.stations[pvi] - length / 2
            offset = station - begin
            if 0 <= offset <= length:
                grade_in, grade_out = self.grades[pvi - 1:pvi + 1]
                begin_z = self.elevations[pvi] - grade_in * length / 2
                return begin_z + grade_in * offset + (grade_out - grade_in) * offset**2 / (2 * length)
        return self.elevations[index] + self.grades[index] * (station - self.stations[index])

    def breakpoints(self):
        points = set(self.stations)
        for index, length in self.curves.items():
            points.update((self.stations[index] - length / 2, self.stations[index] + length / 2))
        return points


def _profile(element, name):
    profiles = element.findall("./Profile/ProfAlign")
    if name is not None:
        profiles = [p for p in profiles if p.get("name") == name]
    if len(profiles) != 1:
        raise ValueError("Specify one unambiguous LandXML ProfAlign")
    stations, elevations, curves = [], [], {}
    for node in profiles[0]:
        if node.tag not in {"PVI", "ParaCurve"}:
            raise ValueError(f"Unsupported LandXML profile element {node.tag}")
        station, elevation = map(float, (node.text or "").split())
        if not all(map(isfinite, (station, elevation))) or (stations and station <= stations[-1]):
            raise ValueError("Profile requires finite values and increasing stations")
        if node.tag == "ParaCurve":
            length = float(node.attrib["length"])
            if not isfinite(length) or length <= 0:
                raise ValueError("ParaCurve: length must be positive and finite")
            curves[len(stations)] = length
        stations.append(station)
        elevations.append(elevation)
    if len(stations) < 2 or 0 in curves or len(stations) - 1 in curves:
        raise ValueError("Profile requires at least two points with PVIs at its ends")
    for index in range(len(stations) - 1):
        if (curves.get(index, 0.0) + curves.get(index + 1, 0.0)) / 2 > stations[index + 1] - stations[index]:
            raise ValueError("Vertical curves overlap or extend beyond the profile")
    return Profile(stations, elevations, np.diff(elevations) / np.diff(stations), curves)


@dataclass
class Alignment:
    name: str
    elements: list[PlanElement]
    limits: list[float]
    profile: Profile
    equations: list[tuple[float, float]]

    def at(self, station, *, from_left=False):
        search = bisect_left if from_left else bisect_right
        index = min(search(self.limits, station) - 1, len(self.elements) - 1)
        xy, heading = self.elements[index].at(station - self.limits[index])
        return np.array([*xy, self.profile.at(station)]), heading

    def displayed_station(self, station):
        index = bisect_right(self.equations, (station, float("inf"))) - 1
        if index < 0:
            return station
        internal, ahead = self.equations[index]
        return ahead + station - internal

    def sample(self, start, end, max_step, section_stations=()):
        if not (self.limits[0] - 1e-7 <= start < end <= self.limits[-1] + 1e-7
                and self.profile.stations[0] - 1e-7 <= start < end <= self.profile.stations[-1] + 1e-7):
            raise ValueError(f"{self.name}: segment stations must be within both plan and profile")
        start = max(start, self.limits[0], self.profile.stations[0])
        end = min(end, self.limits[-1], self.profile.stations[-1])
        if end - start <= 1e-7:
            raise ValueError(f"{self.name}: zero-length modeling segment")
        for i, boundary in enumerate(self.limits[1:-1], 1):
            if start < boundary < end:
                before = self.elements[i - 1]
                turn = self.elements[i].azimuth - before.at(before.length)[1]
                if abs((turn + pi) % (2 * pi) - pi) > 1e-3:
                    raise ValueError(f"{self.name}: horizontal tangent changes abruptly at {boundary:.3f} m")
        required = [start, *(s for s in section_stations if start + 1e-7 < s < end - 1e-7), end]
        limits = sorted(set(required) | {
            s for s in [*self.limits, *self.profile.breakpoints()]
            if start < s < end and all(abs(s - fixed) > 1e-7 for fixed in required)
        })
        stations = [start]
        for left, right in zip(limits, limits[1:]):
            if right - left <= 1e-7:
                continue
            i = bisect_right(self.limits, (left + right) / 2) - 1
            k = max(abs(self.elements[i].k_start), abs(self.elements[i].k_end))
            count = ceil(max((right - left) / max_step, (right - left) * k / np.deg2rad(2)))
            stations.extend(np.linspace(left, right, count + 1)[1:])
        frames = [self.at(s) for s in stations[:-1]]
        frames.append(self.at(stations[-1], from_left=True))
        return np.array(stations), np.array([pt for pt, _ in frames]), np.array([h for _, h in frames])


class LandXML:
    def __init__(self, path):
        self.root = parse(path).getroot()
        for element in self.root.iter():
            element.tag = element.tag.rsplit("}", 1)[-1]
        if self.root.tag != "LandXML":
            raise ValueError("Expected a LandXML document")
        units = self.root.find("./Units/Metric")
        if units is None or units.get("linearUnit", "").lower() not in {"meter", "metre"}:
            raise ValueError("LandXML must declare metric units in meters")

    def alignment(self, name, profile_name=None):
        matches = [elm for elm in self.root.findall("./Alignments/Alignment") if elm.get("name") == name]
        if len(matches) != 1:
            raise ValueError(f"Expected one LandXML alignment named {name!r}")
        element = matches[0]
        geometry = element.find("CoordGeom")
        if geometry is None or not len(geometry):
            raise ValueError(f"{name}: missing CoordGeom elements")
        elements = [_plan_element(child) for child in geometry]
        limits = [float(element.get("staStart", 0))]
        if not isfinite(limits[0]):
            raise ValueError(f"{name}: staStart must be finite")
        for i, plan in enumerate(elements):
            if i and dist(elements[i - 1].end, plan.start) > 0.005:
                raise ValueError(f"{name}: discontinuity between plan elements")
            limits.append(limits[-1] + plan.length)
        equations = sorted((float(eq.attrib["staInternal"]), float(eq.attrib["staAhead"]))
                           for eq in element.findall("StaEquation"))
        if (any(not (limits[0] <= station <= limits[-1] and isfinite(ahead)) for station, ahead in equations)
                or len({station for station, _ in equations}) != len(equations)):
            raise ValueError(f"{name}: invalid station equations")
        return Alignment(name, elements, limits, _profile(element, profile_name), equations)
