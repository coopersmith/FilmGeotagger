"""The write planner and argfile, and — when exiftool is installed — a real round-trip on a JPEG."""

from __future__ import annotations

import shutil
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from PIL import Image

from filmgeo.signals.user_facts import RollFacts
from filmgeo.write import exiftool as w

UTC = timezone.utc


def frame(number, source, status, iso, tzoffset=-14400, lat=40.69, lon=-73.99, conf=0.9):
    return {"number": number, "source": source, "status": status, "time": iso, "tzoffset": tzoffset,
            "t_lo": iso, "t_hi": iso, "confidence": conf, "lat": lat, "lon": lon, "anchor_uuid": "u" if source == "anchored" else None}


ASSIGN = {
    "roll": "r", "origin": "/scans/r",
    "frames": [
        frame(1, "anchored", "confirmed", "2026-04-04T14:01:49.864000+00:00"),           # UTC-dated photo, NY offset
        frame(2, "interpolated", "confirmed", "2026-04-04T10:30:00-04:00", lat=None, lon=None, conf=0.31),
        frame(3, "locked", "confirmed", "2026-04-05T11:08:00-04:00", conf=1.0),
        frame(4, "interpolated", "proposed", "2026-04-06T09:00:00-04:00"),
        frame(5, "skipped", "proposed", "2026-04-06T09:00:02-04:00"),
    ],
}


@pytest.fixture
def roll(tmp_path):
    files = {}
    for n in range(1, 6):
        p = tmp_path / f"000070440{n:03d}.jpg"
        Image.new("RGB", (64, 48), (n * 40, 90, 120)).save(p, "JPEG")
        files[n] = p
    facts = RollFacts("r", camera="Mamiya 7II", film="Kodak Portra 400", lab="Indie Film Lab")
    return tmp_path, files, facts


def test_local_stamp_renders_in_the_frames_offset():
    local, off, inst = w.local_stamp("2026-04-04T14:01:49.864000+00:00", -14400)
    assert (local, off) == ("2026:04:04 10:01:49", "-04:00")
    assert inst == datetime(2026, 4, 4, 14, 1, 49, tzinfo=UTC)
    assert w.local_stamp("2026-07-14T15:32:10+01:00", None)[:2] == ("2026:07:14 15:32:10", "+01:00")
    assert w.local_stamp("2026-07-14T15:32:10+01:00", 19800)[:2] == ("2026:07:14 20:02:10", "+05:30")


def test_plan_writes_confirmed_frames_only(roll):
    folder, files, facts = roll
    p = w.plan("r", folder, ASSIGN, facts, files)
    assert [f.number for f in p.frames] == [1, 2, 3]
    assert [(s.number, s.why) for s in p.skipped] == [(4, "not confirmed"), (5, "skipped")]
    f1, f2, f3 = p.frames
    assert (f1.local, f1.offset, f1.lat, f1.lon) == ("2026:04:04 10:01:49", "-04:00", 40.69, -73.99)
    assert f1.keywords == ["Film", "Mamiya 7II", "Kodak Portra 400", "Indie Film Lab", "filmgeo:anchored", "filmgeo:conf:high"]
    assert f2.keywords[-3:] == ["filmgeo:interpolated", "filmgeo:conf:low", "filmgeo:location-unknown"]
    assert f3.keywords[-2:] == ["filmgeo:manual", "filmgeo:conf:high"]
    assert (f1.make, f1.model) == ("Mamiya", "7II")
    assert w.split_camera("Leica M7") == ("Leica", "M7") and w.split_camera("Holga") == (None, "Holga") and w.split_camera(None) == (None, None)


def test_argfile_has_the_full_tag_set_and_never_overwrites_originals(roll):
    folder, files, facts = roll
    p = w.plan("r", folder, ASSIGN, facts, files)
    text = p.argfile_text()
    assert "-overwrite_original" not in text
    assert text.count("-execute") == 2                              # three files, one process
    a1 = p.frames[0].args()
    assert "-EXIF:DateTimeOriginal=2026:04:04 10:01:49" in a1
    assert "-EXIF:OffsetTimeOriginal=-04:00" in a1
    assert "-XMP-photoshop:DateCreated=2026-04-04T10:01:49-04:00" in a1
    assert "-FileModifyDate=2026:04:04 10:01:49-04:00" in a1        # explicit, with the capture offset (M0)
    assert "-EXIF:GPSLatitude=40.69" in a1 and "-EXIF:GPSLatitudeRef=N" in a1
    assert "-EXIF:GPSLongitude=73.99" in a1 and "-EXIF:GPSLongitudeRef=W" in a1
    assert "-XMP-exif:GPSLongitude=-73.99" in a1
    assert "-XMP-dc:Subject+=Kodak Portra 400" in a1 and "-IPTC:Keywords+=filmgeo:anchored" in a1
    assert a1.index("-XMP-dc:Subject-=Film") < a1.index("-XMP-dc:Subject+=Film")   # remove-then-add: no duplicates
    assert "-EXIF:Make=Mamiya" in a1 and "-EXIF:Model=7II" in a1
    assert a1[-1] == str(files[1])
    a2 = p.frames[1].args()
    assert not any(x.startswith("-EXIF:GPS") for x in a2) and "-XMP-dc:Subject+=filmgeo:location-unknown" in a2


def test_plan_refuses_a_roll_whose_frames_live_in_photos(roll):
    folder, files, facts = roll
    with pytest.raises(w.WriteError, match="Photos library"):
        w.plan("00007044-k12", None, {"roll": "00007044-k12", "origin": "00007044", "frames": []}, facts)
    p = w.plan("r", folder, {**ASSIGN, "frames": ASSIGN["frames"][:1] + [frame(9, "anchored", "confirmed", "2026-04-04T10:00:00-04:00")]}, facts, files)
    assert [(s.number, s.why) for s in p.skipped] == [(9, "no file")]


@pytest.mark.skipif(shutil.which("exiftool") is None, reason="exiftool not installed")
def test_round_trip_through_exiftool(roll, tmp_path):
    folder, files, facts = roll
    p = w.plan("r", folder, ASSIGN, facts, files)
    argfile = w.save_argfile(p, tmp_path / "writes")
    warnings = w.apply(argfile)
    assert warnings == [], warnings
    tags = w.read_tags([files[1], files[2]])
    t1 = tags[files[1].resolve()]
    assert t1["ExifIFD:DateTimeOriginal"] == "2026:04:04 10:01:49"
    assert t1["ExifIFD:OffsetTimeOriginal"] == "-04:00"
    assert t1["XMP-photoshop:DateCreated"] == "2026:04:04 10:01:49-04:00"
    assert abs(t1["GPS:GPSLatitude"] - 40.69) < 1e-4 and t1["GPS:GPSLongitudeRef"] == "W"
    assert abs(t1["Composite:GPSLongitude"] + 73.99) < 1e-4
    assert set(t1["XMP-dc:Subject"]) >= {"Film", "Mamiya 7II", "Kodak Portra 400", "filmgeo:anchored", "filmgeo:conf:high"}
    assert (t1["IFD0:Make"], t1["IFD0:Model"]) == ("Mamiya", "7II")
    # FileModifyDate is the capture *instant*: 10:01:49 -04:00, whatever this Mac's zone is.
    mtime = datetime.fromtimestamp(files[1].stat().st_mtime, tz=UTC)
    assert abs((mtime - datetime(2026, 4, 4, 14, 1, 49, tzinfo=UTC)).total_seconds()) < 1
    assert "GPS:GPSLatitude" not in tags[files[2].resolve()] and "filmgeo:location-unknown" in tags[files[2].resolve()]["XMP-dc:Subject"]
    # exiftool kept the originals; unconfirmed frames were not touched.
    assert files[1].with_name(files[1].name + "_original").exists()
    assert not files[4].with_name(files[4].name + "_original").exists()
    # A second write replaces the provenance keywords rather than stacking them.
    cur = w.current_tags(files)
    assert cur[1].date == "2026:04:04 10:01:49" and cur[1].provenance == ["filmgeo:anchored", "filmgeo:conf:high"]
    assert cur[4].date is None and cur[4].provenance == []
    p2 = w.plan("r", folder, {**ASSIGN, "frames": [frame(1, "locked", "confirmed", "2026-04-04T14:01:49+00:00", conf=1.0)]}, facts, files, current=cur)
    assert "-XMP-dc:Subject-=filmgeo:anchored" in p2.frames[0].args()
    for f in files.values():  # exiftool refuses to overwrite a stale _original
        f.with_name(f.name + "_original").unlink(missing_ok=True)
    assert w.apply(w.save_argfile(p2, tmp_path / "writes2")) == []
    t1b = w.read_tags([files[1]])[files[1].resolve()]
    for tag in ("XMP-dc:Subject", "IPTC:Keywords"):
        subj = t1b[tag]
        assert "filmgeo:manual" in subj and "filmgeo:anchored" not in subj and subj.count("Film") == 1, (tag, subj)


@pytest.mark.skipif(shutil.which("exiftool") is None, reason="exiftool not installed")
def test_full_cycle_backup_write_verify_record_restore_clear(roll, tmp_path):
    import hashlib
    import json

    from filmgeo.write import ops

    folder, files, facts = roll
    sha = lambda p: hashlib.sha256(Path(p).read_bytes()).hexdigest()
    before = {n: sha(p) for n, p in files.items()}
    p = w.plan("r", folder, ASSIGN, facts, files)
    res = ops.write_roll(p, tmp_path / "writes")
    assert res.ok and res.warnings == [] and [c.number for c in res.checks] == [1, 2, 3]
    assert sorted(x.name for x in res.backed_up) == [files[n].name for n in (1, 2, 3)]
    assert sha(folder / ops.BACKUP_DIR / files[1].name) == before[1]              # the backup is the pristine scan
    log = json.loads(res.record.read_text())
    assert log["roll"] == "r" and len(log["writes"]) == 1
    wr = log["writes"][0]
    assert [f["number"] for f in wr["frames"]] == [1, 2, 3] and all(f["verified"] for f in wr["frames"])
    assert [s["why"] for s in wr["skipped"]] == ["not confirmed", "skipped"]

    # Verification notices a file that no longer says what was written.
    w.exiftool(["-overwrite_original", "-EXIF:OffsetTimeOriginal=+02:00", str(files[2])])
    bad = ops.verify(p)
    assert not bad[1].ok and "OffsetTimeOriginal" in bad[1].problems[0] and bad[0].ok

    # A second write goes through even though _original files exist (the backup is the pristine copy).
    res2 = ops.write_roll(p, tmp_path / "writes")
    assert res2.ok and res2.backed_up == [] and len(json.loads(res2.record.read_text())["writes"]) == 2
    assert sha(folder / ops.BACKUP_DIR / files[1].name) == before[1]              # never overwritten

    # Clear: provenance only, then everything.
    removed = ops.clear(folder, files=[files[1], files[4]])
    assert list(removed) == [files[1].name] and "filmgeo:anchored" in removed[files[1].name]
    t1 = w.read_tags([files[1]])[files[1].resolve()]
    assert not any(k.startswith("filmgeo:") for k in t1["XMP-dc:Subject"]) and "Kodak Portra 400" in t1["XMP-dc:Subject"]
    assert t1["ExifIFD:DateTimeOriginal"] == "2026:04:04 10:01:49"
    ops.clear(folder, everything=True, files=[files[1]])
    t1 = w.read_tags([files[1]])[files[1].resolve()]
    assert "ExifIFD:DateTimeOriginal" not in t1 and "GPS:GPSLatitude" not in t1 and "IFD0:Model" not in t1
    assert "Kodak Portra 400" in t1["XMP-dc:Subject"]

    # Restore: the backup is the pristine scan, so it wins over _original (which after a re-write
    # or a clear is itself a written file); a file with neither is left alone. All bytes back.
    done = ops.restore(folder, files=[files[1], files[2], files[4]])
    assert [(r.file, r.how) for r in done] == [(files[1].name, "backup"), (files[2].name, "backup"), (files[4].name, "nothing to restore")]
    assert sha(files[1]) == before[1] and sha(files[2]) == before[2] and sha(files[4]) == before[4]
    assert not files[1].with_name(files[1].name + "_original").exists()
    # With no backup, _original is the fallback.
    w.exiftool(["-EXIF:Model=Test", str(files[4])])
    assert [(r.file, r.how) for r in ops.restore(folder, files=[files[4]])] == [(files[4].name, "_original")] and sha(files[4]) == before[4]


@pytest.mark.skipif(shutil.which("exiftool") is None, reason="exiftool not installed")
def test_sidecar_written_and_only_changed_frames_rewrite(roll, tmp_path):
    import json

    from filmgeo.align.overrides import RollOverrides
    from filmgeo.write import ops, sidecar

    folder, files, facts = roll
    facts.frame(3).when = "2026-04-05"
    ov = RollOverrides("r")
    ov.frame(3).anchor = "u3"
    ov.frame(1).confirmed = True
    verdicts = {1: {"match": "u", "confidence": 0.9, "evidence": "same sofa", "candidates": ["u"], "clues": {}}}
    p = w.plan("r", folder, ASSIGN, facts, files)
    res = ops.write_roll(p, tmp_path / "writes", assignments=ASSIGN, verdicts=verdicts, facts=facts, overrides=ov)
    assert res.ok and res.sidecar == folder / "filmgeo.json"
    side = json.loads(res.sidecar.read_text())
    assert side["roll"] == "r" and [f["number"] for f in side["frames"]] == [1, 2, 3]
    f1 = side["frames"][0]
    first_f1 = dict(f1)
    assert f1["local"] == "2026:04:04 10:01:49" and f1["offset"] == "-04:00" and f1["evidence"] == "same sofa" and f1["verified"] is True
    assert f1["source"] == "anchored" and f1["anchor_uuid"] == "u" and f1["interval"] == [ASSIGN["frames"][0]["time"]] * 2
    assert side["facts"]["camera"] == "Mamiya 7II" and side["facts"]["frames"]["3"]["when"] == "2026-04-05"
    assert side["overrides"]["3"]["anchor"] == "u3" and side["overrides"]["1"]["confirmed"] is True

    # Nothing changed: the next plan writes nothing.
    p2 = w.plan("r", folder, ASSIGN, facts, files, written=sidecar.written_frames(folder))
    assert p2.frames == [] and [(s.number, s.why) for s in p2.skipped][:3] == [(1, "unchanged"), (2, "unchanged"), (3, "unchanged")]
    assert len(w.plan("r", folder, ASSIGN, facts, files, written=sidecar.written_frames(folder), force=True).frames) == 3
    # Frame 2 moved by an hour: only it is written, and the sidecar keeps frames 1 and 3.
    moved = {**ASSIGN, "frames": [dict(f, time="2026-04-04T11:30:00-04:00") if f["number"] == 2 else f for f in ASSIGN["frames"]]}
    p3 = w.plan("r", folder, moved, facts, files, written=sidecar.written_frames(folder))
    assert [f.number for f in p3.frames] == [2]
    res3 = ops.write_roll(p3, tmp_path / "writes", assignments=moved, verdicts=verdicts, facts=facts, overrides=ov)
    side = json.loads(res3.sidecar.read_text())
    assert [f["number"] for f in side["frames"]] == [1, 2, 3] and side["frames"][1]["local"] == "2026:04:04 11:30:00"
    assert side["frames"][0] == first_f1                                     # untouched frames keep their record

    # Reopening on a machine without .filmgeo/: adopt seeds facts and overrides from the sidecar.
    made = sidecar.adopt("r", folder, tmp_path / "f", tmp_path / "o")
    assert made == ["facts", "overrides"]
    rf = RollFacts.load("r", tmp_path / "f")
    assert rf.camera == "Mamiya 7II" and rf.frames[3].when == "2026-04-05"
    assert RollOverrides.load("r", tmp_path / "o").frames[3].anchor == "u3"
    assert sidecar.adopt("r", folder, tmp_path / "f", tmp_path / "o") == []      # never overwrites what exists
