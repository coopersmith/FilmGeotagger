"""Where and in which time zone a frame was, once the solver has said *when* (COO-116).

The solver gives every frame a time and an interval. This module turns the trail — phone
photos, NFC taps, user pins — into a location and a UTC offset for each frame, and says how
much to trust them:

* An anchored frame takes its photo's GPS and offset exactly. A frame the user pinned takes
  the pin (`location_source` "user") and counts as an anchor for interpolating its neighbours.
* Otherwise the trail points inside the frame's interval decide. If they sit within
  `TIGHT_M` of their centroid, that centroid is the location (`ok`). If they are spread out
  but the frame lies between two anchors within `INTERPOLATE_M` of each other, the location
  is interpolated linearly in time between those anchors (`ok`). Otherwise the frame is
  `ambiguous`: the distinct clusters are kept for the UI to offer, and no pin is invented
  (PLAN.md: never invent a pin). No trail at all and no anchors close enough is `none`.
* The offset comes from the nearest trail point in time. If points inside the interval
  disagree — a travel day — the frame is flagged `offset_disputed` and the nearest wins.
"""

from __future__ import annotations

import bisect
from dataclasses import dataclass, field
from datetime import datetime

from filmgeo.align.solve import Assignment, Solution
from filmgeo.events import haversine_m
from filmgeo.signals.base import TrailPoint

TIGHT_M = 300.0          # trail points this close together are one place
INTERPOLATE_M = 2000.0   # anchors this close can be interpolated between
CLUSTER_M = 300.0        # greedy clustering radius for the ambiguous case


@dataclass
class Cluster:
    lat: float
    lon: float
    count: int
    spread_m: float
    first: datetime
    last: datetime
    label: str | None = None


def _centroid(points: list[tuple[float, float]]) -> tuple[float, float, float]:
    lat = sum(p[0] for p in points) / len(points)
    lon = sum(p[1] for p in points) / len(points)
    spread = max(haversine_m((lat, lon), p) for p in points)
    return lat, lon, spread


def clusters(points: list[TrailPoint], radius_m: float = CLUSTER_M) -> list[Cluster]:
    """Greedy clustering by distance to a running centroid; biggest cluster first."""
    groups: list[list[TrailPoint]] = []
    for p in points:
        for g in groups:
            lat, lon, _ = _centroid([(q.lat, q.lon) for q in g])
            if haversine_m((lat, lon), (p.lat, p.lon)) <= radius_m:
                g.append(p)
                break
        else:
            groups.append([p])
    out = []
    for g in groups:
        lat, lon, spread = _centroid([(q.lat, q.lon) for q in g])
        label = next((q.label for q in g if q.label), None)
        out.append(Cluster(lat, lon, len(g), spread, min(q.time for q in g), max(q.time for q in g), label))
    out.sort(key=lambda c: -c.count)
    return out


class Trail:
    """Time-sorted trail with the two lookups the derivation needs."""

    def __init__(self, points: list[TrailPoint]):
        self.points = sorted(points, key=lambda p: p.time)
        self.times = [p.time for p in self.points]

    def within(self, lo: datetime, hi: datetime) -> list[TrailPoint]:
        a, b = bisect.bisect_left(self.times, lo), bisect.bisect_right(self.times, hi)
        return self.points[a:b]

    def nearest(self, t: datetime, need_offset: bool = False) -> TrailPoint | None:
        best, best_gap = None, None
        i = bisect.bisect_left(self.times, t)
        for j in range(max(0, i - 50), min(len(self.points), i + 50)):
            p = self.points[j]
            if need_offset and p.tzoffset is None:
                continue
            gap = abs((p.time - t).total_seconds())
            if best_gap is None or gap < best_gap:
                best, best_gap = p, gap
        return best


def _interpolate(a: Assignment, prev: Assignment, nxt: Assignment) -> tuple[float, float]:
    span = (nxt.time - prev.time).total_seconds()
    f = 0.5 if span <= 0 else (a.time - prev.time).total_seconds() / span
    f = min(1.0, max(0.0, f))
    return prev.lat + f * (nxt.lat - prev.lat), prev.lon + f * (nxt.lon - prev.lon)


def place(solution: Solution, trail_points: list[TrailPoint], pins: dict[int, tuple[float, float]] | None = None) -> Solution:
    """Fill location, location_source, clusters, tzoffset and offset_disputed on every frame.

    `pins` are the user's place facts by 0-based frame index: a pinned frame is located there,
    whatever the trail says, and serves as an anchor for interpolating the frames around it.
    """
    trail = Trail(trail_points)
    located = [p for p in trail.points if p.has_location]
    loc_trail = Trail(located)
    frames = solution.assignments
    pins = pins or {}
    for i, (lat, lon) in pins.items():
        if 0 <= i < len(frames):
            frames[i].lat, frames[i].lon = lat, lon
    anchored = [i for i, a in enumerate(frames) if (a.source in ("anchored", "locked") and a.lat is not None) or i in pins]
    anchored_all = [i for i, a in enumerate(frames) if a.source in ("anchored", "locked")]

    for i, a in enumerate(frames):
        # -- location --------------------------------------------------------------------
        if i in pins:
            a.location, a.location_source, a.clusters = "ok", "user", []
        elif a.source in ("anchored", "locked") and a.lat is not None:
            a.location, a.location_source = "ok", "anchor"
        else:
            a.lat = a.lon = None
            inside = loc_trail.within(a.t_lo, a.t_hi)
            prev = next((frames[k] for k in reversed(anchored) if k < i), None)
            nxt = next((frames[k] for k in anchored if k > i), None)
            near_anchors = (
                prev is not None and nxt is not None
                and haversine_m((prev.lat, prev.lon), (nxt.lat, nxt.lon)) <= INTERPOLATE_M
            )
            if inside:
                lat, lon, spread = _centroid([(p.lat, p.lon) for p in inside])
                if spread <= TIGHT_M:
                    a.lat, a.lon, a.location, a.location_source = lat, lon, "ok", "trail"
                elif near_anchors:
                    a.lat, a.lon = _interpolate(a, prev, nxt)
                    a.location, a.location_source = "ok", "interpolated"
                else:
                    a.location, a.location_source = "ambiguous", "trail"
                    a.clusters = clusters(inside)
            elif near_anchors:
                a.lat, a.lon = _interpolate(a, prev, nxt)
                a.location, a.location_source = "ok", "interpolated"
            else:
                a.location, a.location_source = "none", None

        # -- offset ----------------------------------------------------------------------
        if a.source in ("anchored", "locked") and a.tzoffset is not None:
            continue
        offsets = {p.tzoffset for p in trail.within(a.t_lo, a.t_hi) if p.tzoffset is not None}
        # A roll that crosses a zone change: the anchored frames either side carry different
        # offsets, and every frame between them is in dispute even if the trail is thin there.
        prev_a = next((frames[k] for k in reversed(anchored_all) if k < i), None)
        next_a = next((frames[k] for k in anchored_all if k > i), None)
        if prev_a is not None and next_a is not None and prev_a.tzoffset is not None and next_a.tzoffset is not None \
                and prev_a.tzoffset != next_a.tzoffset:
            offsets |= {prev_a.tzoffset, next_a.tzoffset}
        a.offset_disputed = len(offsets) > 1
        a.offsets = sorted(offsets)
        p = trail.nearest(a.time, need_offset=True)
        a.tzoffset = p.tzoffset if p else None
    return solution
