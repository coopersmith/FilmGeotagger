from pathlib import Path

from PIL import Image

from filmgeo.verify.outing import OutingAnswer, OutingGroup, Outings, contact_sheet, events_summary


def test_contact_sheet_tiles_and_tolerates_missing(tmp_path):
    paths = []
    for i in range(7):
        p = tmp_path / f"f{i}.jpg"
        Image.new("RGB", (600 + 50 * i, 400), (i * 30, 100, 100)).save(p)
        paths.append(str(p))
    paths[3] = str(tmp_path / "missing.jpg")
    out = contact_sheet(paths, list(range(1, 8)), tmp_path / "sheet.jpg", tile=100, columns=4)
    with Image.open(out) as im:
        assert im.size == (400, 200)                       # 7 tiles -> 4 x 2
        assert im.getpixel((350, 150)) == (24, 24, 24)      # empty 8th tile is background
        assert im.getpixel((350, 50)) == (60, 60, 60)       # missing 4th tile is grey


def test_outings_roundtrip_and_pairs(tmp_path):
    answer = OutingAnswer(
        groups=[OutingGroup(frames=[1, 2, 3, 4], description="park, red coat", confidence=0.9),
                OutingGroup(frames=[5, 6], description="kitchen", confidence=0.4),
                OutingGroup(frames=[7, 8, 9, 12], description="beach", confidence=0.8),
                OutingGroup(frames=[10, 11], description="?", confidence=0.7)],
        out_of_sequence=[12], notes="frame 12 looks like the beach day")
    o = Outings.from_answer("r", answer, "m")
    o.save(tmp_path)
    back = Outings.load("r", tmp_path)
    assert back.groups == o.groups and back.out_of_sequence == [12] and back.model == "m"
    assert Outings.load("nope", tmp_path) is None
    pairs = back.same_outing_pairs(12)
    assert pairs == {(0, 1), (1, 2), (2, 3), (6, 7), (7, 8), (9, 10)}   # low-confidence group and #12 excluded
    assert (8, 11) not in pairs                                            # non-adjacent never pairs


def test_events_summary_counts_per_day():
    from datetime import datetime, timezone

    from filmgeo.events import Event

    t = datetime(2026, 4, 4, 10, tzinfo=timezone.utc)
    evs = [Event(0, t, t, None, None, 0, 3), Event(1, t.replace(hour=15), t.replace(hour=16), None, None, 0, 5),
           Event(2, t.replace(day=9), t.replace(day=9), None, None, 0, 1)]
    assert events_summary(evs) == "Sat 4 Apr: 8 phone photos\nThu 9 Apr: 1 phone photos"
    assert events_summary([]) == "no phone photos in the window"
