from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

from filmgeo.signals.base import Window, effective_window, frame_bounds
from filmgeo.signals.user_facts import FrameFact, RollFacts, UserFacts, parse_period

NY = ZoneInfo("America/New_York")


def test_parse_period_granularities():
    assert parse_period("2026", NY) == (datetime(2026, 1, 1, tzinfo=NY), datetime(2027, 1, 1, tzinfo=NY))
    assert parse_period("2026-04", NY) == (datetime(2026, 4, 1, tzinfo=NY), datetime(2026, 5, 1, tzinfo=NY))
    assert parse_period("2026-12", NY)[1] == datetime(2027, 1, 1, tzinfo=NY)
    lo, hi = parse_period("2026-04-12", NY)
    assert lo == datetime(2026, 4, 12, tzinfo=NY) and hi - lo == timedelta(days=1)
    lo, hi = parse_period("2026-04-12 14:05", NY)
    assert lo == datetime(2026, 4, 12, 14, 5, tzinfo=NY) and hi - lo == timedelta(minutes=1)
    lo, hi = parse_period("2026-04-12T14:05:30", NY)
    assert hi - lo == timedelta(seconds=1)
    with pytest.raises(ValueError):
        parse_period("April", NY)
    with pytest.raises(ValueError):
        parse_period("12/04/2026", NY)


def test_roundtrip(tmp_path):
    f = RollFacts("r1", window_from="2026-04", window_to="2026-04", tz="America/New_York",
                  camera="Leica M7", film="Kodak Portra 400", lab="Indie Film Lab", notes="Tiverton weekend")
    f.frame(12).when = "2026-04-12"
    f.frame(12).lat, f.frame(12).lon = 41.62, -71.19
    f.frame(20).same_day_as = 12
    f.frame(30)                        # touched but empty: must not be persisted
    p = f.save(tmp_path)
    back = RollFacts.load("r1", tmp_path)
    assert back == RollFacts("r1", window_from="2026-04", window_to="2026-04", tz="America/New_York",
                             camera="Leica M7", film="Kodak Portra 400", lab="Indie Film Lab",
                             notes="Tiverton weekend",
                             frames={12: FrameFact(12, when="2026-04-12", lat=41.62, lon=-71.19),
                                     20: FrameFact(20, same_day_as=12)})
    assert RollFacts.load("missing", tmp_path) == RollFacts("missing")
    assert p.name == "r1.json"


def test_constraints_from_facts():
    f = RollFacts("r1", window_from="2026-04", window_to="2026-04", tz="America/New_York")
    f.frame(12).when = "2026-04-12"
    f.frame(13).skip = True
    f.frame(5).lat, f.frame(5).lon, f.frame(5).place_name = 38.7, -9.1, "Lisbon"
    cs = UserFacts(f).constraints()
    roll = [c for c in cs if c.scope == "roll"]
    assert len(roll) == 1 and roll[0].t_lo == datetime(2026, 4, 1, tzinfo=NY) and roll[0].t_hi == datetime(2026, 5, 1, tzinfo=NY)
    by_frame = {c.frame: c for c in cs if c.scope == "frame"}
    assert by_frame[12].t_lo == datetime(2026, 4, 12, tzinfo=NY)
    assert by_frame[13].skip and not by_frame[13].has_time
    assert by_frame[5].has_place and by_frame[5].note == "Lisbon"

    default = Window(datetime(2026, 1, 1, tzinfo=NY), datetime(2026, 12, 31, tzinfo=NY))
    w = effective_window(cs, default)
    assert (w.start, w.end) == (datetime(2026, 4, 1, tzinfo=NY), datetime(2026, 5, 1, tzinfo=NY))
    b = frame_bounds(cs, 36, w)
    assert b[0][1] == datetime(2026, 4, 13, tzinfo=NY)          # frame 1 must end by the end of 12 April
    assert b[35][0] == datetime(2026, 4, 12, tzinfo=NY)         # frame 36 starts no earlier than 12 April


def test_trail_points_only_for_pinned_moments():
    f = RollFacts("r1", tz="America/New_York")
    f.frame(1).when, f.frame(1).lat, f.frame(1).lon = "2026-04-12 14:05", 41.6, -71.2
    f.frame(2).when, f.frame(2).lat, f.frame(2).lon = "2026-04-12", 41.6, -71.2       # a whole day: not a point
    f.frame(3).when = "2026-04-12 15:00"                                                # no place: not a point
    w = Window(datetime(2026, 4, 1, tzinfo=NY), datetime(2026, 5, 1, tzinfo=NY))
    pts = UserFacts(f).trail_points(w)
    assert [p.ref for p in pts] == ["frame 1"] and pts[0].source == "user"


def test_validate_reports_contradictions():
    f = RollFacts("r1", window_from="2026-05", window_to="2026-04", tz="UTC")
    f.frame(10).when = "2026-04-20"
    f.frame(4).when = "2026-04-25"          # earlier in the roll, later in time
    f.frame(50).when = "nonsense"
    f.frame(7).lat = 1.0
    f.frame(8).same_day_as = 8
    problems = f.validate(n_frames=36)
    assert any("ends" in p for p in problems)
    assert any("frame 10 is dated before frame 4" in p for p in problems)
    assert any("frame 50 does not exist" in p for p in problems)
    assert any("cannot read 'nonsense'" in p for p in problems)
    assert any("frame 7: place" in p for p in problems)
    assert any("frame 8: same-day-as itself" in p for p in problems)
    assert RollFacts("ok", window_from="2026-04", window_to="2026-04").validate() == []
