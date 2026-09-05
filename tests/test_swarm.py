import json
from datetime import datetime, timedelta, timezone

from filmgeo.photos.library import Asset
from filmgeo.signals.base import Window
from filmgeo.signals.photos_trail import PhotosTrail
from filmgeo.signals.swarm import Swarm, load_checkins, load_visits, parse_utc

UTC = timezone.utc


def export(tmp_path):
    (tmp_path / "checkins1.json").write_text(json.dumps({"count": 2, "items": [
        {"id": "c1", "createdAt": "2026-04-05 15:12:00.000000", "lat": 40.6935, "lng": -73.9911, "timeZoneOffset": -240,
         "venue": {"id": "v1", "name": "787 Coffee Co.", "url": ""}, "type": "checkin", "hacc": 10},
        {"id": "c2", "createdAt": "2026-03-01 12:00:00.000000", "lat": 41.0, "lng": -71.0, "timeZoneOffset": -300, "venue": {"name": "Elsewhere"}},
        {"id": "c3", "createdAt": "2026-04-20 09:00:00.000000", "lat": None, "lng": None, "timeZoneOffset": -240, "venue": {"name": "no fix"}},
    ]}))
    (tmp_path / "checkins2.json").write_text(json.dumps({"count": 1, "items": [
        {"id": "c4", "createdAt": "2026-04-11 12:08:19.000000", "lat": 40.6936, "lng": -73.9911, "timeZoneOffset": -240, "venue": {"name": "Brooklyn Heights Promenade"}},
    ]}))
    (tmp_path / "visits.json").write_text(json.dumps({"count": 2, "items": [
        {"id": "v1", "timeArrived": "2026-04-05 14:00:00.000000", "timeDeparted": "2026-04-05 15:10:00.000000", "latitude": 40.6934, "longitude": -73.9912,
         "city": "Brooklyn", "state": "New York", "locationType": "Venue", "isTraveling": False},
        {"id": "v2", "timeArrived": "2026-04-30 23:50:00.000000", "timeDeparted": None, "latitude": 40.7, "longitude": -74.0, "city": "Brooklyn", "locationType": "Home"},
        {"id": "v3", "timeArrived": "2026-06-01 10:00:00.000000", "timeDeparted": "2026-06-01 11:00:00.000000", "latitude": 1.0, "longitude": 1.0, "city": "Far", "locationType": "Venue"},
    ]}))
    return tmp_path


def test_parse_and_load(tmp_path):
    d = export(tmp_path)
    assert parse_utc("2026-08-17 20:25:35.000000") == datetime(2026, 8, 17, 20, 25, 35, tzinfo=UTC)
    cis = load_checkins(d)
    assert [c.id for c in cis] == ["c2", "c1", "c4"] and cis[1].tzoffset == -14400 and cis[1].venue == "787 Coffee Co."
    vs = load_visits(d)
    assert [v.id for v in vs] == ["v1", "v2", "v3"] and vs[1].departed is None and vs[0].kind == "Venue"
    assert load_checkins(tmp_path / "nope") == [] and load_visits(tmp_path / "nope") == []


def test_trail_points_checkins_and_visits_inside_the_window(tmp_path):
    d = export(tmp_path)
    photos = PhotosTrail([Asset("a", "a.heic", datetime(2026, 4, 30, 20, 0, tzinfo=timezone(timedelta(hours=-4))), -14400, 40.7, -74.0)])
    s = Swarm(d, offset_at=photos.offset_at)
    w = Window(datetime(2026, 4, 1, tzinfo=UTC), datetime(2026, 5, 1, tzinfo=UTC))
    pts = s.trail_points(w)
    cis = [p for p in pts if p.source == "swarm"]
    assert [p.label for p in cis] == ["787 Coffee Co.", "Brooklyn Heights Promenade"] and all(p.tzoffset == -14400 for p in cis)
    assert cis[0].time == datetime(2026, 4, 5, 15, 12, tzinfo=UTC) and cis[0].ref == "c1"
    vis = [p for p in pts if p.source == "visit"]
    v1 = [p for p in vis if p.ref == "v1"]
    assert [p.time for p in v1] == [datetime(2026, 4, 5, 14, 0, tzinfo=UTC), datetime(2026, 4, 5, 14, 30, tzinfo=UTC), datetime(2026, 4, 5, 15, 0, tzinfo=UTC), datetime(2026, 4, 5, 15, 10, tzinfo=UTC)]
    assert v1[0].label == "Venue: Brooklyn" and all(p.tzoffset == -14400 for p in v1)      # offset from the check-in an hour later
    v2 = [p for p in vis if p.ref == "v2"]
    assert len(v2) == 1 and v2[0].tzoffset == -14400                                           # no departure: one point; offset from the photo
    assert not [p for p in vis if p.ref == "v3"]
    assert pts == sorted(pts, key=lambda p: p.time) and s.constraints() == []
    assert Swarm(d, visits=False).trail_points(w) == cis
    assert Swarm(tmp_path / "missing").trail_points(w) == []
