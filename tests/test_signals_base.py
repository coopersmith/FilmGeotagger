from datetime import datetime, timedelta, timezone

import pytest

from filmgeo.signals.base import Constraint, TrailPoint, Window, collect, effective_window, frame_bounds

UTC = timezone.utc


def dt(day, hour=0):
    return datetime(2026, 4, day, hour, tzinfo=UTC)


def test_window_rejects_naive_and_reversed():
    with pytest.raises(ValueError):
        Window(datetime(2026, 4, 1), dt(2))
    with pytest.raises(ValueError):
        Window(dt(2), dt(1))
    w = Window.around(dt(10), dt(12), pad_days=2)
    assert w.start == dt(8) and w.end == dt(14)
    assert w.contains(dt(9)) and not w.contains(dt(15))


def test_constraint_validation():
    with pytest.raises(ValueError):
        Constraint("frame", "user")                    # no frame number
    with pytest.raises(ValueError):
        Constraint("roll", "user", frame=3)            # roll constraints are roll-wide
    with pytest.raises(ValueError):
        Constraint("frame", "user", frame=1, t_lo=dt(5), t_hi=dt(4))
    c = Constraint("frame", "user", frame=1, lat=1.0, lon=2.0)
    assert c.has_place and not c.has_time


def test_effective_window_intersects_roll_constraints():
    default = Window(dt(1), dt(30))
    cs = [
        Constraint("roll", "user", t_lo=dt(5)),
        Constraint("roll", "lab", t_hi=dt(20)),
        Constraint("frame", "user", frame=3, t_lo=dt(25)),   # frame-level: ignored here
    ]
    w = effective_window(cs, default)
    assert (w.start, w.end) == (dt(5), dt(20))
    with pytest.raises(ValueError):
        effective_window([Constraint("roll", "user", t_lo=dt(25), t_hi=dt(28)), Constraint("roll", "x", t_hi=dt(10))], default)


def test_frame_bounds_propagate_monotonically():
    window = Window(dt(1), dt(30))
    cs = [Constraint("frame", "user", frame=3, t_lo=dt(10), t_hi=dt(11))]
    b = frame_bounds(cs, 5, window)
    assert b[2] == (dt(10), dt(11))
    assert b[0] == (dt(1), dt(11)) and b[1] == (dt(1), dt(11))     # earlier frames end by frame 3's end
    assert b[3] == (dt(10), dt(30)) and b[4] == (dt(10), dt(30))   # later frames start after its start


def test_frame_bounds_two_facts_bracket_the_middle():
    window = Window(dt(1), dt(30))
    cs = [
        Constraint("frame", "user", frame=2, t_lo=dt(4), t_hi=dt(5)),
        Constraint("frame", "user", frame=6, t_lo=dt(12), t_hi=dt(13)),
        Constraint("frame", "user", frame=99, t_lo=dt(20)),          # out of range: ignored
    ]
    b = frame_bounds(cs, 7, window)
    assert b[3] == (dt(4), dt(13))
    assert b[6] == (dt(12), dt(30))


class _Fake:
    name = "fake"

    def __init__(self, pts, cs):
        self._pts, self._cs = pts, cs

    def trail_points(self, window):
        return [p for p in self._pts if window.contains(p.time)]

    def constraints(self):
        return list(self._cs)


def test_collect_merges_and_sorts():
    a = _Fake([TrailPoint(dt(3), 1, 1, "a")], [Constraint("roll", "a", t_lo=dt(2))])
    b = _Fake([TrailPoint(dt(2), 1, 1, "b"), TrailPoint(dt(9), 1, 1, "b")], [])
    ev = collect([a, b], Window(dt(1), dt(5)))
    assert [p.source for p in ev.trail] == ["b", "a"]
    assert len(ev.roll_constraints()) == 1
