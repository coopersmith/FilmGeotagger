"""The local review API on a synthetic roll: a fake loader stands in for the library and vectors.

The roll has five frames over a twelve-photo pool in three events (day 2 morning, day 2
afternoon, day 9). Verification anchored frame 1 to the morning and frame 5 to day 9; the
frames between are interpolated. Every test drives the app through `TestClient` and checks
both the response and what landed on disk, because the files are the contract with the CLI
and with M4's write step.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

import numpy as np
import pytest
from fastapi.testclient import TestClient
from PIL import Image

from filmgeo import events as ev, retrieve
from filmgeo.align import pipeline
from filmgeo.align.overrides import RollOverrides
from filmgeo.align.pipeline import FrameRef, Verdict
from filmgeo.api.app import create_app
from filmgeo.api.state import Store
from filmgeo.photos.library import Asset
from filmgeo.signals.base import TrailPoint, Window
from filmgeo.signals.user_facts import RollFacts

UTC = timezone.utc
T0 = datetime(2026, 4, 1, tzinfo=UTC)
KEY = "roll-x"
ORIGIN = "/scans/roll-x"


def at(day, hour=0, minute=0):
    return T0 + timedelta(days=day - 1, hours=hour, minutes=minute)


def _jpeg(path, colour):
    Image.new("RGB", (64, 48), colour).save(path, "JPEG")
    return str(path)


@pytest.fixture
def world(tmp_path):
    """Pool, frames, sims and verdicts; images on disk so thumbnails are real."""
    img = tmp_path / "img"
    img.mkdir()
    pool = []
    # Three events of four photos twenty minutes apart (the 45-minute gap rule splits anything wider).
    spec = [(2, 9, 41.0, -71.0), (2, 14, 41.2, -71.0), (9, 12, 42.0, -70.0)]
    for j in range(12):
        day, hour, lat, lon = spec[j // 4]
        minute = 20 * (j % 4)
        u = f"P{j:02d}"
        pool.append(Asset(u, f"{u}.heic", at(day, hour, minute), -14400, lat, lon, derivative=_jpeg(img / f"{u}.jpg", (j * 20, 80, 120))))
    frames = [FrameRef(n, f"frame{n}", _jpeg(img / f"f{n}.jpg", (200, n * 40, 40))) for n in range(1, 6)]
    rng = np.random.default_rng(0)
    sims = rng.uniform(0.5, 0.7, (5, 12))
    sims[0, 1] = 0.95      # frame 1 looks like P01 (day 2, 09:20)
    sims[4, 9] = 0.95      # frame 5 looks like P09 (day 9, 12:20)
    verdicts = {1: Verdict(["P01", "P05", "P09"], "P01", 0.9, "same sofa", {"time_of_day": "morning", "indoor": True}),
                5: Verdict(["P09", "P01", "P05"], "P09", 0.85, "same harbour")}
    return {"pool": pool, "frames": frames, "sims": sims, "verdicts": verdicts}


class FakeLoader:
    """`pipeline.run` without the library: window from facts, else the fixture's default."""

    def __init__(self, world):
        self.world = world
        self.calls = []

    def __call__(self, origin, alias=None, assets=None, facts=None, overrides=None, widen=False):
        w = self.world
        facts = facts or RollFacts(alias)
        lo, hi = facts.window()
        window = Window(lo, hi) if lo and hi else Window(at(1), at(31))
        self.calls.append((origin, alias, window))
        pool = [a for a in w["pool"] if window.contains(a.date)]
        event_ids, events = ev.segment(pool)
        cols = [j for j, a in enumerate(w["pool"]) if window.contains(a.date)]
        sims = w["sims"][:, cols]
        cands = {}
        for i, f in enumerate(w["frames"]):
            seen, out = {}, []
            for j in np.argsort(-sims[i]):
                e = event_ids[j]
                if seen.get(e, 0) >= 1:
                    continue
                seen[e] = 1
                out.append(retrieve.Candidate(pool[j], float(sims[i, j]), {"siglip": float(sims[i, j])}))
            cands[f.number] = out[:6]
        trail = [TrailPoint(a.date, a.lat, a.lon, "photos", tzoffset=a.tzoffset, ref=a.uuid) for a in pool]
        return pipeline.solve_run(alias or origin, origin, w["frames"], facts, window, "facts" if lo and hi else "default",
                                  pool, events, event_ids, sims, cands, dict(w["verdicts"]), trail, None,
                                  overrides or RollOverrides(alias or origin))


@pytest.fixture
def store(tmp_path, world):
    data = tmp_path / "data"
    loader = FakeLoader(world)
    s = Store(data_dir=data, loader=loader, assets_loader=lambda: list(world["pool"]), origins={KEY: ORIGIN})
    s.loader_calls = loader.calls
    return s


@pytest.fixture
def client(store):
    return TestClient(create_app(store))


def frames_by_number(rows):
    return {f["number"]: f for f in rows}


# -- listing and reading ---------------------------------------------------------------------


def test_list_rolls_before_and_after_loading(client, store):
    rows = client.get("/api/rolls").json()
    assert [r["key"] for r in rows] == [KEY]
    assert rows[0]["origin"] == ORIGIN and not rows[0]["aligned"]
    client.get(f"/api/rolls/{KEY}")
    row = client.get("/api/rolls").json()[0]
    assert row["aligned"] and row["n_frames"] == 5 and row["anchored"] == 2 and row["loaded"]
    assert (store.assignments_dir / f"{KEY}.json").exists()
    assert client.get("/api/rolls/nope").status_code == 404


def test_roll_header_and_frames(client):
    roll = client.get(f"/api/rolls/{KEY}").json()
    assert roll["origin"] == ORIGIN and roll["n_frames"] == 5 and len(roll["events"]) == 3
    assert roll["anchored"] == 2 and roll["verified_frames"] == 2 and "frames" not in roll
    frames = client.get(f"/api/rolls/{KEY}/frames").json()
    f = frames_by_number(frames)
    assert f[1]["source"] == "anchored" and f[1]["anchor"]["uuid"] == "P01" and f[1]["time"] == at(2, 9, 20).isoformat()
    assert f[5]["source"] == "anchored" and f[5]["anchor"]["uuid"] == "P09"
    assert all(f[n]["source"] == "interpolated" for n in (2, 3, 4))
    assert f[1]["verdict"]["evidence"] == "same sofa" and f[1]["verdict"]["match"] == "P01"
    assert f[1]["candidates"][0]["uuid"] == "P01" and f[1]["candidates"][0]["verdict"] == "match"
    assert f[1]["image"] == f"/api/rolls/{KEY}/frames/1/image"
    assert f[1]["candidates"][0]["image"] == "/api/photos/P01/image"
    assert f[3]["interval_text"].startswith("between") and not f[3]["locked"] and f[3]["status"] == "proposed"
    assert client.get(f"/api/rolls/{KEY}/frames/3").json()["number"] == 3
    assert client.get(f"/api/rolls/{KEY}/frames/9").status_code == 404


def test_roll_header_estimates_claude_cost(client, monkeypatch):
    from filmgeo.align import pipeline as pl

    roll = client.get(f"/api/rolls/{KEY}").json()
    assert roll["cost"] == {"verified_frames": 2, "k": 12, "verify_usd": 0.14, "outing_usd": 0.0, "usd": 0.14, "model": None}
    monkeypatch.setattr(pl, "verdicts_meta", lambda key: {"k": 6, "model": "m"})
    roll = client.get(f"/api/rolls/{KEY}").json()
    assert roll["cost"]["k"] == 6 and roll["cost"]["usd"] == 0.07 and roll["cost"]["model"] == "m"


def test_possible_photos_are_inside_the_interval(client):
    f = frames_by_number(client.get(f"/api/rolls/{KEY}/frames").json())
    # Frame 3 sits between the day-2 morning anchor and the day-9 anchor: nothing from before
    # 08:50 on day 2 or after day 9, one photo per event, most similar first.
    poss = f[3]["possible"]
    assert poss and all(f[3]["t_lo"] <= p["time"] <= f[3]["t_hi"] for p in poss)
    assert len({p["event"] for p in poss}) == len(poss)
    assert [p["score"] for p in poss] == sorted((p["score"] for p in poss), reverse=True)
    # An anchored frame's possible photos are its occasion: the chosen one is among them.
    assert any(p["uuid"] == "P01" and p["verdict"] == "match" for p in f[1]["possible"])
    assert all("2026-04-02" in p["time"] for p in f[1]["possible"])
    assert len(f[1]["possible"]) == 4                              # the whole occasion, not one per event
    # Lock frame 2 to the afternoon and frame 3's possible photos shrink to the afternoon .. day 9.
    f = frames_by_number(client.put(f"/api/rolls/{KEY}/frames/2/assign", json={"anchor": "P05"}).json())
    assert all(p["time"] >= at(2, 14).isoformat() for p in f[3]["possible"])


def test_frames_stay_in_scan_order(client):
    frames = client.get(f"/api/rolls/{KEY}/frames").json()
    times = [datetime.fromisoformat(f["time"]) for f in frames]
    assert times == sorted(times) and all(b - a >= timedelta(seconds=2) for a, b in zip(times, times[1:]))


def test_frame_trail_is_the_points_inside_the_interval(client):
    f = client.get(f"/api/rolls/{KEY}/frames/1").json()          # anchored on day 2 morning: occasion 08:50-10:00
    pts = client.get(f"/api/rolls/{KEY}/frames/1/trail").json()
    assert [p["ref"] for p in pts] == ["P00", "P01", "P02", "P03"]
    assert pts[0]["lat"] == 41.0 and pts[0]["source"] == "photos" and pts[0]["tzoffset"] == -14400
    assert all(f["t_lo"] <= p["time"] <= f["t_hi"] for p in pts)
    wide = client.get(f"/api/rolls/{KEY}/frames/1/trail?pad_minutes=600").json()
    assert len(wide) == 8                                          # the afternoon event too
    assert client.get(f"/api/rolls/{KEY}/frames/9/trail").status_code == 404


def test_roll_photos_by_event_or_range(client):
    by_event = client.get(f"/api/rolls/{KEY}/photos?event=1").json()
    assert [p["uuid"] for p in by_event] == ["P04", "P05", "P06", "P07"] and by_event[0]["event"] == 1
    assert by_event[0]["image"] == "/api/photos/P04/image"
    from urllib.parse import quote

    rng = client.get(f"/api/rolls/{KEY}/photos?start={quote(at(2, 9, 10).isoformat())}&end={quote(at(2, 9, 50).isoformat())}").json()
    assert [p["uuid"] for p in rng] == ["P01", "P02"]
    assert client.get(f"/api/rolls/{KEY}/photos").status_code == 422
    assert len(client.get(f"/api/rolls/{KEY}/photos?event=1&limit=2").json()) == 2
    # Any pool photo, not only a shortlisted one, can be the anchor.
    f = frames_by_number(client.put(f"/api/rolls/{KEY}/frames/2/assign", json={"anchor": "P04"}).json())
    assert f[2]["source"] == "locked" and f[2]["anchor"]["uuid"] == "P04"


# -- thumbnails ------------------------------------------------------------------------------


def test_thumbnails_are_cached_by_content_key(client, store):
    r = client.get(f"/api/rolls/{KEY}/frames/2/image")
    assert r.status_code == 200 and r.headers["content-type"] == "image/jpeg"
    assert (store.thumbs_dir / "small" / "frame2.jpg").exists()
    big = client.get("/api/photos/P05/image?size=large")
    assert big.status_code == 200 and (store.thumbs_dir / "large" / "P05.jpg").exists()
    with Image.open(store.thumbs_dir / "small" / "frame2.jpg") as im:
        assert max(im.size) <= 240
    assert client.get("/api/photos/nope/image").status_code == 404
    assert client.get("/api/photos/P05/image?size=huge").status_code == 422
    assert "immutable" in r.headers["cache-control"]


def test_unreadable_image_is_403(client, world, monkeypatch):
    import filmgeo.api.app as appmod

    def denied(*a, **k):
        raise PermissionError("Operation not permitted")

    monkeypatch.setattr(appmod, "thumbnail", denied)
    r = client.get("/api/photos/P05/image")
    assert r.status_code == 403 and "Terminal.app" in r.json()["detail"]


# -- overrides -------------------------------------------------------------------------------


def test_pick_a_photo_locks_the_frame_and_moves_its_neighbours(client, store):
    before = frames_by_number(client.get(f"/api/rolls/{KEY}/frames").json())
    r = client.put(f"/api/rolls/{KEY}/frames/3/assign", json={"anchor": "P06"})
    assert r.status_code == 200
    f = frames_by_number(r.json())
    assert f[3]["source"] == "locked" and f[3]["locked"] and f[3]["time"] == at(2, 14, 40).isoformat()
    assert f[3]["anchor"]["uuid"] == "P06" and f[3]["lat"] == 41.2 and f[3]["override"]["anchor"] == "P06"
    # Frame 2 is now squeezed between the morning anchor and the afternoon lock's occasion.
    assert at(2, 9, 20) <= datetime.fromisoformat(f[2]["time"]) <= at(2, 14, 40)
    assert datetime.fromisoformat(f[2]["t_hi"]) <= at(2, 15, 10)
    assert datetime.fromisoformat(before[2]["t_hi"]) > at(2, 15, 10)
    # Persisted: overrides file, and the assignments file now carries the lock.
    saved = json.loads((store.overrides_dir / f"{KEY}.json").read_text())
    assert saved["frames"]["3"]["anchor"] == "P06"
    on_disk = json.loads((store.assignments_dir / f"{KEY}.json").read_text())
    assert on_disk["frames"][2]["source"] == "locked" and on_disk["origin"] == ORIGIN
    assert client.put(f"/api/rolls/{KEY}/frames/3/assign", json={"anchor": "nope"}).status_code == 404


def test_not_a_match_and_no_reference_drop_verdict_anchors(client):
    f = frames_by_number(client.put(f"/api/rolls/{KEY}/frames/1/assign", json={"reject": ["P01"]}).json())
    assert f[1]["source"] == "interpolated" and f[1]["override"]["rejected"] == ["P01"]
    assert f[1]["candidates"][0]["rejected"] is True
    f = frames_by_number(client.put(f"/api/rolls/{KEY}/frames/5/assign", json={"no_reference": True}).json())
    assert f[5]["source"] == "interpolated" and f[5]["locked"] and f[5]["override"]["no_reference"]
    roll = client.get(f"/api/rolls/{KEY}").json()
    assert roll["anchored"] == 0
    # Unlock restores verification's anchor.
    f = frames_by_number(client.put(f"/api/rolls/{KEY}/frames/5/assign", json={"unlock": True}).json())
    assert f[5]["source"] == "anchored" and f[5]["override"] is None


def test_typed_time_becomes_a_frame_fact(client, store):
    r = client.put(f"/api/rolls/{KEY}/frames/3/assign", json={"when": "2026-04-05 12:00"})
    assert r.status_code == 200
    f = frames_by_number(r.json())
    assert f[3]["locked"] and f[3]["fact"]["when"] == "2026-04-05 12:00"
    t = datetime.fromisoformat(f[3]["time"])
    lo = datetime(2026, 4, 5, 12, tzinfo=store.get(KEY).facts.zone)
    assert lo <= t < lo + timedelta(minutes=1)          # the minute is half-open; 12:01:00 is outside it
    facts = json.loads((store.facts_dir / f"{KEY}.json").read_text())
    assert facts["frames"]["3"]["when"] == "2026-04-05 12:00"
    # An ISO instant with an offset is rendered into the roll's zone to the minute.
    r = client.put(f"/api/rolls/{KEY}/frames/3/assign", json={"when": "2026-04-05T16:30:00+00:00"})
    when = frames_by_number(r.json())[3]["fact"]["when"]
    assert when == datetime(2026, 4, 5, 16, 30, tzinfo=UTC).astimezone(store.get(KEY).facts.zone).strftime("%Y-%m-%d %H:%M")
    assert client.put(f"/api/rolls/{KEY}/frames/3/assign", json={"when": "yesterday"}).status_code == 422
    assert client.put(f"/api/rolls/{KEY}/frames/3/assign", json={"lat": 1.0}).status_code == 422


def test_pin_skip_and_confirm(client):
    f = frames_by_number(client.put(f"/api/rolls/{KEY}/frames/2/assign", json={"lat": 41.0, "lon": -71.0, "place_name": "home"}).json())
    assert f[2]["fact"]["place_name"] == "home" and f[2]["locked"]
    assert (f[2]["location"], f[2]["location_source"], f[2]["lat"], f[2]["lon"], f[2]["clusters"]) == ("ok", "user", 41.0, -71.0, [])
    # A pin is an anchor for its neighbours' interpolation: frame 3, between the pin and the
    # day-9 anchor 140 km away, stays honest rather than being interpolated across the map.
    assert f[3]["location"] in ("ambiguous", "none")
    f = frames_by_number(client.put(f"/api/rolls/{KEY}/frames/4/assign", json={"skip": True}).json())
    assert f[4]["source"] == "skipped"
    # "same day as" a dated frame binds this frame to that day.
    client.put(f"/api/rolls/{KEY}/frames/4/assign", json={"unlock": True})
    client.put(f"/api/rolls/{KEY}/frames/2/assign", json={"unlock": True})
    client.put(f"/api/rolls/{KEY}/frames/3/assign", json={"when": "2026-04-02"})
    f = frames_by_number(client.put(f"/api/rolls/{KEY}/frames/4/assign", json={"same_day_as": 3}).json())
    assert f[4]["locked"] is False or f[4]["fact"]["same_day_as"] == 3
    assert f[4]["t_hi"] <= datetime(2026, 4, 3, tzinfo=timezone.utc).isoformat() or f[4]["t_hi"].startswith("2026-04-03T00:00")
    # ...and so does "same day as" a frame that is anchored by a verdict, not by a fact.
    client.put(f"/api/rolls/{KEY}/frames/3/assign", json={"unlock": True})
    client.put(f"/api/rolls/{KEY}/frames/4/assign", json={"unlock": True})
    wide = frames_by_number(client.get(f"/api/rolls/{KEY}/frames").json())[2]
    assert wide["t_hi"] > "2026-04-03"
    f = frames_by_number(client.put(f"/api/rolls/{KEY}/frames/3/assign", json={"same_day_as": 1}).json())
    assert f[3]["t_lo"] >= "2026-04-01T20:00" and f[3]["t_hi"] <= "2026-04-03T04:01"   # day 2 in the photos' -04:00
    f = frames_by_number(client.put(f"/api/rolls/{KEY}/frames/1/assign", json={"confirmed": True}).json())
    assert f[1]["status"] == "confirmed" and f[1]["source"] == "anchored"
    assert client.get(f"/api/rolls/{KEY}").json()["confirmed"] == 1
    assert client.get("/api/rolls").json()[0]["confirmed"] == 1
    # A confirmation is of an assignment: changing the assignment drops it.
    f = frames_by_number(client.put(f"/api/rolls/{KEY}/frames/1/assign", json={"reject": ["P01"]}).json())
    assert f[1]["status"] == "proposed" and f[1]["source"] == "interpolated"
    # "unknown" on a picked frame drops the pick rather than locking the skip to it.
    client.put(f"/api/rolls/{KEY}/frames/3/assign", json={"anchor": "P06"})
    f = frames_by_number(client.put(f"/api/rolls/{KEY}/frames/3/assign", json={"skip": True}).json())
    assert f[3]["source"] == "skipped" and f[3]["override"] is None


def test_a_contradicting_lock_is_refused_and_nothing_persists(client, store):
    assert client.put(f"/api/rolls/{KEY}/frames/4/assign", json={"anchor": "P10"}).status_code == 200
    # Frame 2 locked to a photo *after* frame 4's lock contradicts scan order.
    r = client.put(f"/api/rolls/{KEY}/frames/2/assign", json={"anchor": "P11"})
    assert r.status_code == 409
    saved = json.loads((store.overrides_dir / f"{KEY}.json").read_text())
    assert "2" not in saved["frames"] and saved["frames"]["4"]["anchor"] == "P10"
    f = frames_by_number(client.get(f"/api/rolls/{KEY}/frames").json())
    assert f[2]["override"] is None and f[4]["source"] == "locked"
    # A fact that contradicts scan order is caught by validation before the solver.
    client.put(f"/api/rolls/{KEY}/frames/3/assign", json={"when": "2026-04-05"})
    assert client.put(f"/api/rolls/{KEY}/frames/2/assign", json={"when": "2026-04-06"}).status_code == 422


def test_batch_confirm_by_confidence_range_and_roll(client, store):
    before = frames_by_number(client.get(f"/api/rolls/{KEY}/frames").json())
    high = sorted(n for n, f in before.items() if f["confidence"] >= 0.8)
    assert high == [1, 5]                                             # the two anchored frames
    f = frames_by_number(client.post(f"/api/rolls/{KEY}/confirm", json={"min_confidence": 0.8}).json())
    assert sorted(n for n, x in f.items() if x["status"] == "confirmed") == high
    f = frames_by_number(client.post(f"/api/rolls/{KEY}/confirm", json={"frames": [2, 3]}).json())
    assert sorted(n for n, x in f.items() if x["status"] == "confirmed") == [1, 2, 3, 5]
    f = frames_by_number(client.post(f"/api/rolls/{KEY}/confirm", json={"confirmed": False}).json())
    assert not any(x["status"] == "confirmed" for x in f.values())
    client.put(f"/api/rolls/{KEY}/frames/4/assign", json={"skip": True})
    f = frames_by_number(client.post(f"/api/rolls/{KEY}/confirm", json={}).json())
    assert sorted(n for n, x in f.items() if x["status"] == "confirmed") == [1, 2, 3, 5]   # never a skipped frame
    assert client.get("/api/rolls").json()[0]["confirmed"] == 4
    on_disk = json.loads((store.assignments_dir / f"{KEY}.json").read_text())
    assert [x["status"] for x in on_disk["frames"]] == ["confirmed", "confirmed", "confirmed", "proposed", "confirmed"]
    assert client.post(f"/api/rolls/{KEY}/confirm", json={"frames": [9]}).status_code == 404
    # A range confirmation is of those assignments: changing one drops its confirmation only.
    f = frames_by_number(client.put(f"/api/rolls/{KEY}/frames/2/assign", json={"anchor": "P05"}).json())
    assert f[2]["status"] == "proposed" and f[3]["status"] == "confirmed"


# -- facts and realign ------------------------------------------------------------------------


def test_facts_roundtrip_and_window_change_rebuilds_the_pool(client, store):
    got = client.get(f"/api/rolls/{KEY}/facts").json()
    assert got["roll"] == KEY and got["window_from"] is None
    client.get(f"/api/rolls/{KEY}")
    n0 = len(store.loader_calls)
    body = {"camera": "Mamiya 7II", "film": "Kodak Portra 400", "frames": {"2": {"note": "the sofa"}}}
    r = client.put(f"/api/rolls/{KEY}/facts", json=body)
    assert r.status_code == 200 and r.json()["solved"] and r.json()["facts"]["camera"] == "Mamiya 7II"
    assert len(store.loader_calls) == n0            # same window: re-solved in place, no reload
    assert json.loads((store.facts_dir / f"{KEY}.json").read_text())["film"] == "Kodak Portra 400"
    r = client.put(f"/api/rolls/{KEY}/facts", json=body | {"window_from": "2026-04-01", "window_to": "2026-04-05"})
    assert r.status_code == 200
    assert len(store.loader_calls) == n0 + 1        # window moved: pool rebuilt
    roll = client.get(f"/api/rolls/{KEY}").json()
    assert roll["window"]["source"] == "facts" and len(roll["events"]) == 2 and roll["pool"] == 8
    f = frames_by_number(client.get(f"/api/rolls/{KEY}/frames").json())
    assert f[5]["source"] == "interpolated"          # day 9's anchor photo is outside the window now
    assert client.put(f"/api/rolls/{KEY}/facts", json={"window_from": "2026-05", "window_to": "2026-04"}).status_code == 422


def test_realign_rereads_disk_and_widen_persists_into_facts(client, store):
    client.get(f"/api/rolls/{KEY}")
    n0 = len(store.loader_calls)
    r = client.post(f"/api/rolls/{KEY}/realign", json={})
    assert r.status_code == 200 and len(r.json()["frames"]) == 5 and len(store.loader_calls) == n0 + 1
    client.put(f"/api/rolls/{KEY}/facts", json={"window_from": "2026-04-01", "window_to": "2026-04-05"})
    r = client.post(f"/api/rolls/{KEY}/realign", json={"widen": True})
    assert r.status_code == 200
    facts = json.loads((store.facts_dir / f"{KEY}.json").read_text())
    assert facts["window_from"] < "2026-04-01" and facts["window_to"] > "2026-04-05"
    assert r.json()["pool"] == 12                     # day 9 is back inside
    assert store.loader_calls[-1][2].contains(at(9, 12, 20))


def test_facts_for_an_unloaded_roll_are_saved_even_if_it_cannot_solve(tmp_path, world):
    def broken(origin, **kw):
        raise ValueError("no window for it")

    s = Store(data_dir=tmp_path / "d", loader=broken, assets_loader=lambda: [], origins={"new": "/scans/new"})
    c = TestClient(create_app(s))
    r = c.put("/api/rolls/new/facts", json={"camera": "Leica M7"})
    assert r.status_code == 200 and not r.json()["solved"] and "no window" in r.json()["error"]
    assert json.loads((s.facts_dir / "new.json").read_text())["camera"] == "Leica M7"
    assert c.get("/api/rolls/new").status_code == 409
    assert c.get("/api/rolls").json()[0]["facts"]["camera"] == "Leica M7"


# -- the override model on its own --------------------------------------------------------------


def test_overrides_apply_and_roundtrip(tmp_path, world):
    from filmgeo.align.pipeline import anchors_from_verdicts

    pool = world["pool"]
    ids, _ = ev.segment(pool)
    anchors = anchors_from_verdicts(world["verdicts"], pool, ids, world["sims"])
    assert [(a.frame, a.uuid) for a in anchors] == [(0, "P01"), (4, "P09")]
    o = RollOverrides("r")
    o.frame(1).rejected = ["P01"]
    o.frame(3).anchor = "P06"
    o.frame(5).no_reference = True
    o.frame(2).confirmed = True                       # confirmed alone changes nothing
    out = o.apply(anchors, pool, ids, world["sims"])
    assert [(a.frame, a.uuid, a.locked, a.confidence) for a in out] == [(2, "P06", True, 1.0)]
    o.save(tmp_path)
    back = RollOverrides.load("r", tmp_path)
    assert back.frames[3].anchor == "P06" and back.frames[2].confirmed and back.get(4) is None
    o.frame(3).anchor = "not-in-pool"
    assert o.apply(anchors, pool, ids) == []


# -- writing ----------------------------------------------------------------------------------


@pytest.fixture
def writable(tmp_path, world):
    """The same synthetic roll, aligned from a real scratch folder holding the frame files in scan order."""
    import shutil

    scans = tmp_path / "scans"
    scans.mkdir()
    for f in world["frames"]:
        shutil.copy(f.path, scans / f"000070440{f.number:03d}.jpg")
    loader = FakeLoader(world)
    s = Store(data_dir=tmp_path / "data", loader=loader, assets_loader=lambda: list(world["pool"]), origins={KEY: str(scans)})
    return TestClient(create_app(s)), s, scans


def test_write_is_refused_for_a_roll_inside_photos(client):
    client.get(f"/api/rolls/{KEY}")
    r = client.get(f"/api/rolls/{KEY}/write")
    assert r.status_code == 409 and "Photos library" in r.json()["detail"]
    assert client.get(f"/api/rolls/{KEY}").json()["writable"] is False


@pytest.mark.skipif(__import__("shutil").which("exiftool") is None, reason="exiftool not installed")
def test_write_plan_write_verify_and_restore(writable):
    from datetime import datetime as dt

    c, store, scans = writable
    roll = c.get(f"/api/rolls/{KEY}").json()
    assert roll["writable"] is True and roll["written"] is None
    plan = c.get(f"/api/rolls/{KEY}/write").json()
    assert plan["frames"] == [] and [s["why"] for s in plan["skipped"]] == ["not confirmed"] * 5
    c.post(f"/api/rolls/{KEY}/confirm", json={"min_confidence": 0.8})
    plan = c.get(f"/api/rolls/{KEY}/write").json()
    assert [f["number"] for f in plan["frames"]] == [1, 5] and plan["frames"][0]["current"] is None
    assert plan["frames"][0]["local"] == "2026:04:02 05:20:00" and plan["frames"][0]["offset"] == "-04:00"
    assert plan["frames"][0]["provenance"] == [] and plan["frames"][0]["source"] == "anchored"    # opt-in; off by default

    res = c.post(f"/api/rolls/{KEY}/write").json()
    assert res["ok"] and [ (k["number"], k["ok"]) for k in res["checks"]] == [(1, True), (5, True)] and res["backed_up"] == 2
    assert (scans / "filmgeo.json").exists() and (scans / ".filmgeo_backup" / "000070440001.jpg").exists()
    f = frames_by_number(res["frames"])
    assert f[1]["written"]["local"] == "2026:04:02 05:20:00" and f[1]["written"]["changed"] is False and f[2]["written"] is None
    roll = c.get(f"/api/rolls/{KEY}").json()
    assert roll["written"]["frames"] == 2
    # Nothing to write now; the plan says so.
    plan = c.get(f"/api/rolls/{KEY}/write").json()
    assert plan["frames"] == [] and {s["why"] for s in plan["skipped"]} == {"unchanged", "not confirmed"}
    # Move frame 1 to another photo: it is "changed since written", and the next plan writes only it.
    f = frames_by_number(c.put(f"/api/rolls/{KEY}/frames/1/assign", json={"anchor": "P02", "confirmed": True}).json())
    assert f[1]["written"]["changed"] is True and f[5]["written"]["changed"] is False
    plan = c.get(f"/api/rolls/{KEY}/write").json()
    assert [x["number"] for x in plan["frames"]] == [1] and plan["frames"][0]["current"] == "2026:04:02 05:20:00"
    assert len(c.get(f"/api/rolls/{KEY}/write?force=true").json()["frames"]) == 2
    res = c.post(f"/api/rolls/{KEY}/write").json()
    assert res["ok"] and [k["number"] for k in res["checks"]] == [1]
    assert frames_by_number(res["frames"])[1]["written"]["local"] == "2026:04:02 05:40:00"
    # Restore puts every scan back; the sidecar stays as a record.
    before = (scans / ".filmgeo_backup" / "000070440001.jpg").read_bytes()
    r = c.post(f"/api/rolls/{KEY}/restore").json()
    assert {x["how"] for x in r["restored"]} == {"backup", "nothing to restore"}
    assert (scans / "000070440001.jpg").read_bytes() == before
    from filmgeo.write.exiftool import current_tags
    assert current_tags({1: scans / "000070440001.jpg"})[1].date is None
    # The sidecar forgets what was restored: no "written" badge lies about the file.
    assert all(f["written"] is None for f in r["frames"])
    plan = c.get(f"/api/rolls/{KEY}/write").json()
    assert [x["number"] for x in plan["frames"]] == [1, 5]


# -- places ------------------------------------------------------------------------------------


@pytest.fixture
def with_places(tmp_path, world):
    """The synthetic roll with a check-in and a visit on day 5, between the anchors on day 2 and day 9."""
    class Loader(FakeLoader):
        def __call__(self, *a, **k):
            r = super().__call__(*a, **k)
            extra = [TrailPoint(at(5, 15, 52), 41.5209, -71.1921, "visit", -14400, label="Venue: Little Compton", ref="v1"),
                     TrailPoint(at(5, 15, 55), 41.5209, -71.1921, "visit", -14400, label="Venue: Little Compton", ref="v1"),
                     TrailPoint(at(5, 16, 14), 41.5208, -71.1920, "swarm", -14400, label="Young Family Farm", ref="c1"),
                     TrailPoint(at(12, 9), 41.0, -71.0, "swarm", -14400, label="After The Roll", ref="c2")]
            r.trail = sorted(r.trail + extra, key=lambda p: p.time)
            return pipeline.resolve(r)
    s = Store(data_dir=tmp_path / "data", loader=Loader(world), assets_loader=lambda: list(world["pool"]), origins={KEY: ORIGIN})
    return TestClient(create_app(s))


def test_use_a_checkin_dates_and_places_the_frame(with_places):
    c = with_places
    d = c.get(f"/api/rolls/{KEY}/frames/3/places").json()
    assert [p["name"] for p in d["places"]] == ["Young Family Farm"]              # day 12 is after frame 5's anchor
    p = d["places"][0]
    assert p["kind"] == "check-in" and p["when"] == at(5, 16, 14).isoformat() and p["start"] == at(5, 15, 52).isoformat()
    assert d["from"] == at(2, 9, 20).isoformat() and d["to"] == at(9, 12, 20).isoformat() and d["sources"]["swarm"] == 2
    # "same day as" first, then the check-in: the date replaces the same-day link instead of clashing with it.
    c.put(f"/api/rolls/{KEY}/frames/3/assign", json={"same_day_as": 1})
    f = frames_by_number(c.put(f"/api/rolls/{KEY}/frames/3/assign", json={
        "when": p["when"], "lat": p["lat"], "lon": p["lon"], "radius_m": 300, "place_name": p["name"]}).json())
    assert f[3]["fact"]["same_day_as"] is None and f[3]["fact"]["place_name"] == "Young Family Farm" and f[3]["locked"]
    assert datetime.fromisoformat(f[3]["time"]).astimezone(UTC).strftime("%d %H:%M") == "05 16:14"
    assert (f[3]["lat"], f[3]["lon"], f[3]["location_source"]) == (41.5208, -71.1920, "user")
    # Frame 4 can only be from that stop onward; the stop itself is still possible for it.
    d4 = c.get(f"/api/rolls/{KEY}/frames/4/places").json()
    assert d4["from"] == f[3]["time"] and [q["name"] for q in d4["places"]] == ["Young Family Farm"]
    assert c.get(f"/api/rolls/{KEY}/frames/2/places").json()["to"] == f[3]["time"]
    # ...and "same day as" replaces a date.
    f = frames_by_number(c.put(f"/api/rolls/{KEY}/frames/3/assign", json={"same_day_as": 1}).json())
    assert f[3]["fact"]["when"] is None and f[3]["fact"]["same_day_as"] == 1


def test_search_places_by_name(with_places):
    c = with_places
    assert [p["name"] for p in c.get(f"/api/rolls/{KEY}/places?q=farm").json()] == ["Young Family Farm"]
    assert [p["name"] for p in c.get(f"/api/rolls/{KEY}/places?q=roll").json()] == ["After The Roll"]
    assert c.get(f"/api/rolls/{KEY}/places?q=x").status_code == 422


def test_frame_photos_chronological_by_day_and_event_with_bursts_folded(client):
    d = client.get(f"/api/rolls/{KEY}/frames/3/photos").json()
    assert d["from"] == at(2, 9, 20).isoformat() and d["to"] == at(9, 12, 20).isoformat()
    # Between the anchors inclusive: P01-P03 of day 2's morning, the afternoon event, P08-P09 on day 9.
    assert [x["day"] for x in d["days"]] == ["2026-04-02", "2026-04-09"] and d["total"] == 9
    day2 = d["days"][0]
    assert [e["index"] for e in day2["events"]] == [0, 1] and [e["count"] for e in day2["events"]] == [3, 4]
    assert d["days"][1]["events"][0]["count"] == 2 and d["days"][1]["events"][0]["photos"][0]["uuid"] == "P08"
    # Photos twenty minutes apart are not a burst: every one shows.
    assert [len(p["more"]) for p in day2["events"][1]["photos"]] == [0, 0, 0, 0]
    assert not any(p["image_missing"] for e in day2["events"] for p in e["photos"])
    assert client.get(f"/api/rolls/{KEY}/frames/3/photos?all_days=true").json()["total"] == 12
    assert client.get(f"/api/rolls/{KEY}/frames/9/photos").status_code == 404


def test_same_moment_as_a_neighbour_locks_the_frame_beside_it(client):
    # Frame 2 was shot moments after frame 1 (anchored at 09:20): it lands inside a minute of it.
    f = frames_by_number(client.put(f"/api/rolls/{KEY}/frames/2/assign", json={"same_time_as": 1}).json())
    assert f[2]["fact"]["same_time_as"] == 1 and f[2]["locked"]
    t2 = datetime.fromisoformat(f[2]["time"])
    assert at(2, 9, 20) <= t2 < at(2, 9, 21) and datetime.fromisoformat(f[2]["t_hi"]) <= at(2, 9, 21)
    # A moment link replaces a day link and a date, and a date replaces it.
    f = frames_by_number(client.put(f"/api/rolls/{KEY}/frames/3/assign", json={"same_day_as": 1}).json())
    f = frames_by_number(client.put(f"/api/rolls/{KEY}/frames/3/assign", json={"same_time_as": 2}).json())
    assert f[3]["fact"]["same_day_as"] is None and f[3]["fact"]["same_time_as"] == 2
    f = frames_by_number(client.put(f"/api/rolls/{KEY}/frames/3/assign", json={"when": at(2, 14, 5).isoformat()}).json())
    assert f[3]["fact"]["same_time_as"] is None and f[3]["fact"]["when"]
    assert client.put(f"/api/rolls/{KEY}/frames/4/assign", json={"same_time_as": 4}).status_code == 422


def test_geocode_proxies_and_caches(store):
    from filmgeo.api.geocode import Geocoder

    calls = []

    def fake(q):
        calls.append(q)
        return [{"display_name": "Young Family Farm, Little Compton", "lat": "41.5209", "lon": "-71.1921", "type": "farm"}]

    c = TestClient(create_app(store, geocoder=Geocoder(fake, min_interval=0)))
    r = c.get("/api/geocode?q=Young Family Farm").json()
    assert r == [{"name": "Young Family Farm, Little Compton", "lat": 41.5209, "lon": -71.1921, "kind": "farm"}]
    c.get("/api/geocode?q=young  family farm")
    assert calls == ["Young Family Farm"]                 # the second, same up to case and spacing, came from the cache
    assert c.get("/api/geocode?q=Y").status_code == 422


def test_an_empty_place_name_clears_it(client):
    f = frames_by_number(client.put(f"/api/rolls/{KEY}/frames/3/assign", json={"lat": 41.5, "lon": -71.2, "place_name": "Sakonnet Point"}).json())
    assert f[3]["fact"]["place_name"] == "Sakonnet Point"
    f = frames_by_number(client.put(f"/api/rolls/{KEY}/frames/3/assign", json={"lat": 41.6, "lon": -71.1, "place_name": ""}).json())
    assert f[3]["fact"]["place_name"] is None and f[3]["lat"] == 41.6
