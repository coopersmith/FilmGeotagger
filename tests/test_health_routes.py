from datetime import datetime, timedelta, timezone

from filmgeo.photos.library import Asset
from filmgeo.signals.base import Window, collect
from filmgeo.signals.health_routes import HealthRoutes, name_date, parse_gpx
from filmgeo.signals.photos_trail import PhotosTrail

UTC = timezone.utc

GPX = """<?xml version="1.0" encoding="UTF-8"?>
<gpx version="1.1" creator="Apple Health Export" xmlns="http://www.topografix.com/GPX/1/1">
  <trk><name>Route 2026-04-05 10:12am</name><trkseg>
{pts}
  </trkseg></trk>
</gpx>"""
PT = '    <trkpt lon="{lon}" lat="{lat}"><ele>{ele}</ele><time>{t}</time><extensions><speed>1.2</speed></extensions></trkpt>'


def route(path, start, n, seconds=5, lat0=40.69, lon0=-73.99):
    pts = "\n".join(PT.format(lon=lon0 + i * 1e-5, lat=lat0 + i * 1e-5, ele=10 + i, t=(start + timedelta(seconds=seconds * i)).strftime("%Y-%m-%dT%H:%M:%SZ")) for i in range(n))
    path.write_text(GPX.format(pts=pts))
    return path


def test_parse_subsamples_and_keeps_utc(tmp_path):
    p = route(tmp_path / "route_2026-04-05_10.12am.gpx", datetime(2026, 4, 5, 14, 12, 3, tzinfo=UTC), n=600)   # 50 min at 5 s
    pts = parse_gpx(p)
    assert 49 <= len(pts) <= 51 and pts[0][0] == datetime(2026, 4, 5, 14, 12, 3, tzinfo=UTC)
    assert all(b[0] - a[0] >= timedelta(seconds=60) for a, b in zip(pts, pts[1:]))
    assert pts[0][1:3] == (40.69, -73.99) and pts[0][3] == 10.0
    assert name_date(p) == datetime(2026, 4, 5).date() and name_date(tmp_path / "x.gpx") is None
    (tmp_path / "bad.gpx").write_text("<gpx><trk>")
    assert parse_gpx(tmp_path / "bad.gpx") == []


def test_adapter_filters_by_window_and_borrows_the_offset(tmp_path):
    d = tmp_path / "apple_health_export" / "workout-routes"
    d.mkdir(parents=True)
    route(d / "route_2026-04-05_10.12am.gpx", datetime(2026, 4, 5, 14, 12, 0, tzinfo=UTC), n=120)     # inside
    route(d / "route_2026-03-01_08.00am.gpx", datetime(2026, 3, 1, 13, 0, 0, tzinfo=UTC), n=120)      # outside: never opened
    route(d / "route_2026-04-30_09.00pm.gpx", datetime(2026, 5, 1, 1, 0, 0, tzinfo=UTC), n=120)      # name-date inside, points just past the window end
    photos = PhotosTrail([Asset("a", "a.heic", datetime(2026, 4, 5, 9, 0, tzinfo=timezone(timedelta(hours=-4))), -14400, 40.7, -74.0)])
    h = HealthRoutes(tmp_path, offset_at=photos.offset_at)
    w = Window(datetime(2026, 4, 1, tzinfo=UTC), datetime(2026, 5, 1, tzinfo=UTC))
    assert [p.name for p in h.files(w)] == ["route_2026-04-05_10.12am.gpx", "route_2026-04-30_09.00pm.gpx"]
    pts = h.trail_points(w)
    assert len(pts) == 10 and all(p.source == "health" and p.tzoffset == -14400 for p in pts)
    assert pts[0].time == datetime(2026, 4, 5, 14, 12, tzinfo=UTC) and pts[0].label == "2026-04-05_10.12am" and pts[0].ref.endswith(".gpx")
    assert all(w.contains(p.time) for p in pts)
    ev = collect([photos, h], w)
    assert {p.source for p in ev.trail} == {"photos", "health"} and ev.trail == sorted(ev.trail, key=lambda p: p.time)
    assert HealthRoutes(tmp_path / "missing").trail_points(w) == [] and h.constraints() == []


def test_offset_at_uses_the_nearest_photo_by_instant():
    photos = PhotosTrail([
        Asset("a", "a.heic", datetime(2026, 4, 5, 9, 0, tzinfo=timezone(timedelta(hours=-4))), -14400, 40.7, -74.0),
        Asset("b", "b.heic", datetime(2026, 4, 20, 9, 0, tzinfo=timezone(timedelta(hours=1))), 3600, 38.7, -9.1),
    ])
    assert photos.offset_at(datetime(2026, 4, 5, 15, 0, tzinfo=UTC)) == -14400
    assert photos.offset_at(datetime(2026, 4, 19, 15, 0, tzinfo=UTC)) == 3600
    assert photos.offset_at(datetime(2026, 6, 1, tzinfo=UTC)) is None
