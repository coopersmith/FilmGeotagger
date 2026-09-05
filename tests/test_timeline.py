import json
from datetime import datetime, timedelta, timezone

from filmgeo.photos.library import Asset
from filmgeo.signals.base import Window
from filmgeo.signals.photos_trail import PhotosTrail
from filmgeo.signals.timeline import Timeline, parse_latlng, parse_time

UTC = timezone.utc
W = Window(datetime(2026, 4, 1, tzinfo=UTC), datetime(2026, 5, 1, tzinfo=UTC))


def test_parsers():
    assert parse_latlng("40.6935°, -73.9911°") == (40.6935, -73.9911)
    assert parse_latlng("40.6935, -73.9911") == (40.6935, -73.9911) and parse_latlng(None) is None and parse_latlng("x") is None
    t = parse_time("2026-04-05T10:12:00.000-04:00")
    assert t == datetime(2026, 4, 5, 14, 12, tzinfo=UTC) and t.utcoffset() == timedelta(hours=-4)
    assert parse_time("2026-04-05T14:12:00Z") == datetime(2026, 4, 5, 14, 12, tzinfo=UTC) and parse_time("nope") is None


def test_current_timeline_json(tmp_path):
    data = {
        "semanticSegments": [
            {"startTime": "2026-04-05T10:00:00.000-04:00", "endTime": "2026-04-05T10:05:00.000-04:00",
             "timelinePath": [{"point": "40.6900°, -73.9900°", "time": "2026-04-05T10:00:00.000-04:00"},
                              {"point": "40.6901°, -73.9901°", "time": "2026-04-05T10:00:30.000-04:00"},    # < 60 s: subsampled away
                              {"point": "40.6905°, -73.9905°", "time": "2026-04-05T10:02:00.000-04:00"}]},
            {"startTime": "2026-04-05T10:05:00.000-04:00", "endTime": "2026-04-05T11:20:00.000-04:00",
             "visit": {"hierarchyLevel": 0, "probability": 0.9, "topCandidate": {"placeId": "p-home", "semanticType": "HOME", "probability": 0.8,
                                                                                  "placeLocation": {"latLng": "40.6935°, -73.9911°"}}}},
            {"startTime": "2026-04-05T11:20:00.000-04:00", "endTime": "2026-04-05T11:40:00.000-04:00",
             "activity": {"start": {"latLng": "40.6935°, -73.9911°"}, "end": {"latLng": "40.7000°, -73.9950°"}, "distanceMeters": 900,
                          "topCandidate": {"type": "WALKING", "probability": 0.7}}},
            {"startTime": "2026-06-01T10:00:00.000-04:00", "endTime": "2026-06-01T11:00:00.000-04:00",
             "visit": {"topCandidate": {"placeLocation": {"latLng": "1°, 1°"}, "semanticType": "WORK"}}},                  # outside the window
        ],
        "rawSignals": [
            {"position": {"LatLng": "40.6950°, -73.9920°", "accuracyMeters": 12, "timestamp": "2026-04-06T09:00:00.000-04:00"}},
            {"position": {"LatLng": "40.6950°, -73.9920°", "accuracyMeters": 900, "timestamp": "2026-04-06T09:05:00.000-04:00"}},  # too coarse
            {"wifiScan": {"deliveryTime": "2026-04-06T09:00:00.000-04:00"}},
        ],
        "userLocationProfile": {"frequentPlaces": [{"placeId": "p-home", "placeLocation": "40.6935°, -73.9911°", "label": "HOME"}]},
    }
    (tmp_path / "Timeline.json").write_text(json.dumps(data))
    pts = Timeline(tmp_path).trail_points(W)
    paths = [p for p in pts if p.source == "timeline"]
    visits = [p for p in pts if p.source == "visit"]
    assert [p.time for p in paths] == [datetime(2026, 4, 5, 14, 0, tzinfo=UTC), datetime(2026, 4, 5, 14, 2, tzinfo=UTC),
                                       datetime(2026, 4, 5, 15, 20, tzinfo=UTC), datetime(2026, 4, 5, 15, 40, tzinfo=UTC),
                                       datetime(2026, 4, 6, 13, 0, tzinfo=UTC)]
    assert all(p.tzoffset == -14400 for p in pts)                         # from the timestamps themselves
    assert paths[2].label == "Walking" and paths[4].ref == "raw"
    assert [p.time.astimezone(UTC).strftime("%H:%M") for p in visits] == ["14:05", "14:35", "15:05", "15:20"] and all(p.label == "Home" for p in visits)
    assert visits[0].lat == 40.6935 and pts == sorted(pts, key=lambda p: p.time)


def test_legacy_records_and_semantic_history(tmp_path):
    (tmp_path / "Records.json").write_text(json.dumps({"locations": [
        {"latitudeE7": 406935000, "longitudeE7": -739911000, "accuracy": 20, "timestamp": "2026-04-10T13:00:00Z"},
        {"latitudeE7": 406935000, "longitudeE7": -739911000, "accuracy": 20, "timestamp": "2026-04-10T13:00:20Z"},   # subsampled
        {"latitudeE7": 406940000, "longitudeE7": -739920000, "accuracy": 20, "timestamp": "2026-04-10T13:03:00Z"},
        {"latitudeE7": 406940000, "longitudeE7": -739920000, "accuracy": 500, "timestamp": "2026-04-10T13:04:00Z"},  # coarse
        {"latitudeE7": 10000000, "longitudeE7": 10000000, "accuracy": 5, "timestamp": "2025-01-01T00:00:00Z"},          # outside
    ]}))
    sem = tmp_path / "Semantic Location History" / "2026"
    sem.mkdir(parents=True)
    (sem / "2026_APRIL.json").write_text(json.dumps({"timelineObjects": [
        {"placeVisit": {"location": {"latitudeE7": 406935000, "longitudeE7": -739911000, "name": "787 Coffee Co.", "address": "Montague St"},
                        "duration": {"startTimestamp": "2026-04-11T12:00:00Z", "endTimestamp": "2026-04-11T12:40:00Z"}}},
        {"activitySegment": {"startLocation": {"latitudeE7": 406935000, "longitudeE7": -739911000}, "endLocation": {"latitudeE7": 407000000, "longitudeE7": -739950000},
                             "duration": {"startTimestamp": "2026-04-11T12:40:00Z", "endTimestamp": "2026-04-11T13:00:00Z"}, "activityType": "WALKING",
                             "waypointPath": {"waypoints": [{"latE7": 406935000, "lngE7": -739911000}, {"latE7": 406960000, "lngE7": -739930000}, {"latE7": 407000000, "lngE7": -739950000}]}}},
    ]}))
    (tmp_path / "junk.json").write_text(json.dumps({"other": 1}))
    (tmp_path / "broken.json").write_text("{")
    photos = PhotosTrail([Asset("a", "a.heic", datetime(2026, 4, 10, 9, 0, tzinfo=timezone(timedelta(hours=-4))), -14400, 40.7, -74.0)])
    tl = Timeline(tmp_path, offset_at=photos.offset_at)
    assert sorted(s for s, _ in tl.files()) == ["records", "semantic"]
    pts = tl.trail_points(W)
    rec = [p for p in pts if p.ref.startswith("records")]
    assert [p.time for p in rec] == [datetime(2026, 4, 10, 13, 0, tzinfo=UTC), datetime(2026, 4, 10, 13, 3, tzinfo=UTC)]
    assert all(p.tzoffset == -14400 for p in rec)                          # UTC stamps borrow the photo's offset
    vis = [p for p in pts if p.source == "visit"]
    assert [p.time.astimezone(UTC).strftime("%H:%M") for p in vis] == ["12:00", "12:30", "12:40"] and all(p.label == "787 Coffee Co." for p in vis)
    act = [p for p in pts if p.ref.startswith("activity")]
    assert len(act) == 3 and act[0].label == "Walking" and act[-1].time == datetime(2026, 4, 11, 13, 0, tzinfo=UTC)   # start+waypoints+end, deduped by time
    assert Timeline(tmp_path / "missing").trail_points(W) == [] and tl.constraints() == []
