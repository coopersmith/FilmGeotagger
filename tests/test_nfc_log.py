from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

from filmgeo.photos.library import Asset
from filmgeo.signals.base import Window
from filmgeo.signals.nfc_log import NfcLog, local_offset_for, parse
from filmgeo.signals.photos_trail import PhotosTrail

FIXTURE = Path(__file__).parent / "fixtures" / "nfc_log.txt"


def test_parse_both_entry_shapes_and_quirks():
    entries = parse(FIXTURE.read_text())
    assert len(entries) == 5                      # the title block and the time-less block are skipped
    full, minimal, italy, loaded = entries[0], entries[2], entries[3], entries[4]
    assert full.local_time == datetime(2024, 4, 29, 17, 34)
    assert (full.lat, full.lon) == (41.10000000000001, -71.20000000000002)
    assert full.address == "1 Example Rd\nSometown RI 02800\nUnited States"
    assert (full.camera, full.film) == ("Mamiya 7II", "Kodak Portra 160")
    assert full.notes.startswith("Three shots")
    assert full.event is None
    assert entries[1].notes == "f11, 125" and entries[1].camera == "Leica M7"   # U+FFFC attachment ignored
    assert minimal.camera is None and minimal.film is None and minimal.local_time == datetime(2026, 6, 20, 7, 58)
    assert italy.address.endswith("Italy") and italy.notes is None
    assert loaded.event == "loaded" and loaded.camera == "Contax T2"
    assert full.line == 4 and loaded.line > italy.line


def test_points_resolve_offset_and_window():
    entries = parse(FIXTURE.read_text())
    log = NfcLog(entries, offset_for=local_offset_for(ZoneInfo("America/New_York")))
    pts = log.points()
    assert pts[0].time == datetime(2024, 4, 29, 17, 34, tzinfo=timezone(timedelta(hours=-4)))
    assert pts[0].tzoffset == -4 * 3600 and pts[0].source == "nfc" and pts[0].camera == "Mamiya 7II"
    assert pts[0].label == "1 Example Rd"                       # film stock in the note is stale: never surfaced
    w = Window(datetime(2026, 8, 1, tzinfo=timezone.utc), datetime(2026, 8, 31, tzinfo=timezone.utc))
    assert [p.ref for p in log.trail_points(w)] == ["line 30", "line 39"]
    assert log.constraints() == []


def test_offset_from_nearest_phone_photo():
    rome, ny = ZoneInfo("Europe/Rome"), ZoneInfo("America/New_York")
    assets = [
        Asset("a", "a.jpg", datetime(2026, 8, 11, 9, 0, tzinfo=rome), 7200, 43.5, 11.3),
        Asset("b", "b.jpg", datetime(2026, 8, 20, 9, 0, tzinfo=ny), -14400, 40.7, -74.0),
        Asset("f", "f.jpg", datetime(2026, 8, 12, 11, 0, tzinfo=ny), -14400, 40.7, -74.0, keywords=["Film"]),
    ]
    trail = PhotosTrail(assets)
    assert trail.offset_for(datetime(2026, 8, 12, 11, 37)) == 7200          # Italy, film scan ignored
    assert trail.offset_for(datetime(2026, 8, 19, 23, 0)) == -14400
    assert trail.offset_for(datetime(2026, 9, 30, 0, 0)) is None            # nothing within 3 days

    log = NfcLog(parse(FIXTURE.read_text()), offset_for=trail.offset_for)
    italy = [p for p in log.points() if p.ref == "line 30"][0]
    assert italy.time.utcoffset() == timedelta(hours=2)
    assert not any(p.time.year == 2024 for p in log.points())               # unresolvable offsets are dropped


def test_repeated_taps_collapse_to_one_entry():
    text = FIXTURE.read_text()
    dup = "--\n🕑 Jun 20, 2026 at 7:58 AM\n📍40.7 , -74.0\n🗺️ 2 Sample St\n"
    assert len(parse(text + dup + dup)) == len(parse(text)) + 1
