from datetime import datetime, timezone

from filmgeo.photos.library import Asset
from filmgeo.signals.base import Window
from filmgeo.signals.photos_trail import PhotosTrail

UTC = timezone.utc


def asset(uuid, day, **kw):
    base = dict(filename=f"{uuid}.jpg", date=datetime(2026, 4, day, 12, tzinfo=UTC), tzoffset=-14400, lat=41.0, lon=-71.0)
    base.update(kw)
    return Asset(uuid=uuid, **base)


def test_trail_excludes_film_and_keeps_metadata_only_assets():
    assets = [
        asset("a", 1),
        asset("b", 2, derivative=None),                # no local preview: still a trail point
        asset("c", 3, keywords=["Film"]),              # a scan: never evidence
        asset("d", 4, lat=None, lon=None),             # time-only point still carries the offset
        asset("e", 20),
    ]
    pts = PhotosTrail(assets).trail_points(Window(datetime(2026, 4, 1, tzinfo=UTC), datetime(2026, 4, 10, tzinfo=UTC)))
    assert [p.ref for p in pts] == ["a", "b", "d"]
    assert pts[2].tzoffset == -14400 and not pts[2].has_location
    assert PhotosTrail(assets).constraints() == []


def test_is_scan_catches_untagged_copies():
    from filmgeo.photos.library import lab_key

    assert asset("a", 1, filename="000070440001.jpg").is_scan                      # Indie Film Lab, no keyword
    assert asset("b", 1, filename="348542_0012_Original.jpg").is_scan             # Richard Photo Lab, Photos' suffix
    assert asset("c", 1, filename="20200820-354581_0006_Original.jpg").is_scan    # dated export prefix
    assert asset("d", 1, filename="IMG_0001.HEIC", make="NORITSU KOKI").is_scan  # renamed, scanner make survives
    assert not asset("e", 1, filename="IMG_0001.HEIC", make="Apple").is_scan
    assert asset("f", 1, filename="x.jpg", keywords=["Film"]).is_scan
    assert lab_key("874466_0012.jpg") == ("874466", 12) and lab_key("IMG_1.jpg") == (None, None)


def test_trail_excludes_untagged_scan_copies():
    assets = [asset("a", 1), asset("copy", 2, filename="000070440002.jpg")]
    pts = PhotosTrail(assets).trail_points(Window(datetime(2026, 4, 1, tzinfo=UTC), datetime(2026, 4, 10, tzinfo=UTC)))
    assert [p.ref for p in pts] == ["a"]
