from datetime import datetime, timedelta, timezone

import numpy as np

from filmgeo.align.solve import Assignment, Solution
from filmgeo.geo import clusters, place
from filmgeo.signals.base import TrailPoint

UTC = timezone.utc
T0 = datetime(2026, 4, 2, 9, tzinfo=UTC)
HOME = (41.5536, -71.1929)
TOWN = (41.5560, -71.1900)          # ~400 m from HOME
FAR = (40.6936, -73.9910)           # Brooklyn


def h(hours):
    return T0 + timedelta(hours=hours)


def tp(hours, latlon, off=-14400, source="photos", label=None):
    return TrailPoint(h(hours), latlon[0], latlon[1], source, tzoffset=off, label=label)


def frame(i, time, lo, hi, source="interpolated", latlon=None, off=None):
    a = Assignment(i, 0, source, time, lo, hi, 0.5, 0.0)
    if latlon:
        a.lat, a.lon = latlon
    a.tzoffset = off
    return a


def solve_with(frames, points):
    return place(Solution([0] * len(frames), None, 0.0, frames), points).assignments


def test_anchor_keeps_its_own_gps_and_offset():
    a = frame(0, h(1), h(1), h(1), "anchored", HOME, -14400)
    out = solve_with([a], [tp(1, FAR, off=7200)])
    assert (out[0].lat, out[0].lon, out[0].tzoffset) == (*HOME, -14400)
    assert out[0].location == "ok" and out[0].location_source == "anchor"


def test_tight_trail_gives_centroid():
    a = frame(0, h(2), h(1), h(3))
    pts = [tp(1.5, HOME), tp(2.5, (HOME[0] + 0.001, HOME[1]))]     # ~110 m apart
    out = solve_with([a], pts)
    assert out[0].location == "ok" and out[0].location_source == "trail"
    assert abs(out[0].lat - (HOME[0] + 0.0005)) < 1e-6 and out[0].tzoffset == -14400


def test_spread_trail_between_close_anchors_interpolates():
    a0 = frame(0, h(0), h(0), h(0), "anchored", HOME, -14400)
    a1 = frame(1, h(2), h(0), h(4))
    a2 = frame(2, h(4), h(4), h(4), "anchored", TOWN, -14400)
    out = solve_with([a0, a1, a2], [tp(1, HOME), tp(3, FAR)])      # FAR makes the spread huge
    assert out[1].location == "ok" and out[1].location_source == "interpolated"
    assert abs(out[1].lat - (HOME[0] + TOWN[0]) / 2) < 1e-6         # halfway in time -> halfway in space


def test_spread_trail_far_anchors_is_ambiguous_with_clusters():
    a0 = frame(0, h(0), h(0), h(0), "anchored", HOME, -14400)
    a1 = frame(1, h(2), h(0), h(4))
    a2 = frame(2, h(4), h(4), h(4), "anchored", FAR, -14400)
    pts = [tp(1, HOME), tp(1.2, HOME, label="4398 Main Rd", source="nfc"), tp(3, FAR), tp(3.1, FAR)]
    out = solve_with([a0, a1, a2], pts)
    assert out[1].location == "ambiguous" and out[1].lat is None    # never invent a pin
    cs = out[1].clusters
    assert len(cs) == 2 and cs[0].count == 2 and cs[0].label == "4398 Main Rd"


def test_no_trail_and_no_anchors_is_none():
    out = solve_with([frame(0, h(2), h(1), h(3))], [tp(10, HOME)])
    assert out[0].location == "none" and out[0].lat is None and out[0].tzoffset == -14400


def test_no_trail_but_close_anchors_interpolates():
    a0 = frame(0, h(0), h(0), h(0), "anchored", HOME, -14400)
    a1 = frame(1, h(1), h(0), h(4))
    a2 = frame(2, h(4), h(4), h(4), "anchored", TOWN, -14400)
    out = solve_with([a0, a1, a2], [])
    assert out[1].location_source == "interpolated" and out[1].tzoffset is None


def test_offset_from_nearest_point_and_disputed_on_travel_day():
    a = frame(0, h(2.4), h(0), h(5))
    pts = [tp(0, HOME, off=-14400), tp(2.5, FAR, off=7200), tp(5, FAR, off=7200)]
    out = solve_with([a], pts)
    assert out[0].tzoffset == 7200 and out[0].offset_disputed
    quiet = solve_with([frame(0, h(2), h(0), h(5))], [tp(0, HOME), tp(5, HOME)])
    assert not quiet[0].offset_disputed


def test_clusters_greedy():
    pts = [tp(0, HOME), tp(1, (HOME[0] + 0.001, HOME[1])), tp(2, FAR), tp(3, TOWN)]
    cs = clusters(pts)
    assert [c.count for c in cs] == [2, 1, 1]
    assert cs[0].spread_m < 100


def test_user_pin_places_the_frame_and_anchors_interpolation():
    from filmgeo.geo import place
    from filmgeo.align.solve import Assignment

    def asg(i, t, source="interpolated", lat=None, lon=None):
        return Assignment(i, 0, source, t, t - timedelta(hours=1), t + timedelta(hours=1), 0.5, 0.0, lat=lat, lon=lon,
                          tzoffset=-14400 if source != "interpolated" else None)

    t0 = datetime(2026, 4, 2, 10, tzinfo=timezone.utc)
    sol = Solution([0, 0, 0], np.zeros((3, 1)), 0.0, [
        asg(0, t0, "anchored", 41.0, -71.0), asg(1, t0 + timedelta(hours=3)), asg(2, t0 + timedelta(hours=6)),
    ])
    place(sol, [], pins={2: (41.01, -71.0)})
    a = sol.assignments
    assert (a[2].location, a[2].location_source, a[2].lat, a[2].lon) == ("ok", "user", 41.01, -71.0)
    assert a[1].location == "ok" and a[1].location_source == "interpolated" and 41.0 < a[1].lat < 41.01


def test_offset_dispute_between_anchors_in_different_zones():
    from filmgeo.align.solve import Assignment
    from filmgeo.geo import place

    t0 = datetime(2026, 7, 14, 10, tzinfo=timezone.utc)
    mk = lambda i, t, src="interpolated", off=None, lat=None, lon=None: Assignment(i, 0, src, t, t - timedelta(hours=6), t + timedelta(hours=6), 0.5, 0.0,
                                                                                    tzoffset=off, lat=lat, lon=lon)
    # Anchored in New York (-4 h), then anchored in Lisbon (+1 h) two days later; two frames between.
    sol = Solution([0] * 4, np.zeros((4, 1)), 0.0, [
        mk(0, t0, "anchored", -14400, 40.7, -74.0), mk(1, t0 + timedelta(hours=20)), mk(2, t0 + timedelta(hours=40)),
        mk(3, t0 + timedelta(days=2), "anchored", 3600, 38.7, -9.1),
    ])
    trail = [TrailPoint(t0 + timedelta(hours=18), 40.7, -74.0, "photos", tzoffset=-14400), TrailPoint(t0 + timedelta(hours=44), 38.7, -9.1, "photos", tzoffset=3600)]
    place(sol, trail)
    a = sol.assignments
    assert a[1].offset_disputed and a[1].offsets == [-14400, 3600] and a[1].tzoffset == -14400   # nearest trail point wins, both offered
    assert a[2].offset_disputed and a[2].offsets == [-14400, 3600] and a[2].tzoffset == 3600
    assert not a[0].offset_disputed and a[0].offsets == [] and not a[3].offset_disputed
    # Same zone on both sides: no dispute from the anchors.
    sol2 = Solution([0] * 3, np.zeros((3, 1)), 0.0, [mk(0, t0, "anchored", -14400, 40.7, -74.0), mk(1, t0 + timedelta(hours=5)), mk(2, t0 + timedelta(hours=10), "anchored", -14400, 40.7, -74.0)])
    place(sol2, trail[:1])
    assert not sol2.assignments[1].offset_disputed and sol2.assignments[1].offsets == []      # no trail inside its interval, anchors agree
