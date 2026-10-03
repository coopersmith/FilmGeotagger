"""The place-first engine (COO-177): evidence, readings, visits, the two-step solve.

Everything runs on synthetic vectors — a few "places", each a direction in a small space, with
photos scattered round it — so the tests say what the engine does with evidence of a known
shape, not what SigLIP happens to return.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import numpy as np
import pytest

from filmgeo import events as ev
from filmgeo.align import evidence as evidence_mod, locate, readings as readings_mod, visits as visits_mod
from filmgeo.align.checks import RollInputs
from filmgeo.align.model import build_states
from filmgeo.align.solve import solve
from filmgeo.gazetteer import Gazetteer, Hit, name_match
from filmgeo.geo import clusters
from filmgeo.photos import library
from filmgeo.photos.library import Asset
from filmgeo.signals.base import TrailPoint, Window

UTC = timezone.utc
T0 = datetime(2026, 8, 1, tzinfo=UTC)
WINDOW = Window(T0, T0 + timedelta(days=14))

HOUSE = (43.6740, 11.2985)
CHURCH = (43.7727, 11.2555)        # 12 km away
FARM = (43.6800, 11.3300)          # 2.6 km from the house, never photographed


def at(day, hour=0, minute=0):
    return T0 + timedelta(days=day - 1, hours=hour, minutes=minute)


def unit(v):
    v = np.asarray(v, dtype=float)
    return v / np.linalg.norm(v)


def proto(k, dim=16):
    v = np.zeros(dim)
    v[k] = 1.0
    return v


def photo_vec(place_k, visit_k=None, strength=0.25, dim=16):
    """A photo of place k; photos of the same visit also share a smaller direction of their own."""
    v = proto(place_k, dim) + 0.3 * proto(15, dim)          # every photo shares a little: cosine between places is not 0
    if visit_k is not None:
        v = v + strength * proto(visit_k, dim)
    return unit(v)


@pytest.fixture
def world():
    """A house photographed on five days, a church on one, nothing at the farm."""
    pool, vecs = [], []
    n = 0
    for d, day in enumerate((2, 4, 6, 8, 10)):
        for m in (0, 10, 20):
            pool.append(Asset(f"H{n}", f"H{n}.heic", at(day, 18, m), 7200, HOUSE[0], HOUSE[1] + 0.0001 * m / 10))
            vecs.append(photo_vec(0, 4 + d))
            n += 1
    for m in (0, 10):
        pool.append(Asset(f"C{m}", f"C{m}.heic", at(5, 11, m), 7200, *CHURCH))
        vecs.append(photo_vec(1, 10))
    order = sorted(range(len(pool)), key=lambda i: pool[i].date)
    pool, vecs = [pool[i] for i in order], np.stack([vecs[i] for i in order])
    event_ids, evs = ev.segment(pool)
    return {"pool": pool, "vecs": vecs, "event_ids": event_ids, "events": evs}


def run(world, frame_vecs, readings=(), visits=(), anchors=()):
    e = evidence_mod.build(np.stack(frame_vecs), world["pool"], world["vecs"], world["event_ids"])
    e = evidence_mod.add_readings(e, list(readings))
    inputs = RollInputs(WINDOW, world["events"], len(frame_vecs), list(anchors), None, world["event_ids"], evidence=e, visits=list(visits))
    model = inputs.build()
    sol = solve(model)
    locate.apply(model, sol)
    return model, sol, e


# -- evidence ---------------------------------------------------------------------------------


def test_nearest_photos_vote_for_the_place_and_a_look_alike_is_worth_little(world):
    house = photo_vec(0, 6)                                  # looks like the house, and like day 6's visit
    nothing = unit(proto(12) + 0.3 * proto(15))             # resembles no photo more than any other
    e = evidence_mod.build(np.stack([house, nothing]), world["pool"], world["vecs"], world["event_ids"])
    h = e.frames[0].places[0]
    assert abs(h.lat - HOUSE[0]) < 1e-3 and h.mass > 0.95 and h.in_window and h.photo_mass == pytest.approx(h.mass)
    assert e.frames[0].q > 0.95 > 0.05 > e.frames[1].q
    # The best photo of each event is known, whichever made the top K.
    day6 = [x for x, a in zip(world["event_ids"], world["pool"]) if a.date.day == 6][0]
    assert int(np.argmax(e.frames[0].event_best)) == day6


def test_a_reading_joins_the_vote_without_touching_the_input(world):
    e = evidence_mod.build(np.stack([unit(proto(12) + 0.3 * proto(15))]), world["pool"], world["vecs"], world["event_ids"])
    r = readings_mod.Reading(0, "sign", "YOUNG FAMILY FARM", "Young Family Farm", FARM[0], FARM[1], 0.9)
    e2 = evidence_mod.add_readings(e, [r])
    assert len(e.photos) == len(world["pool"]) and len(e2.photos) == len(e.photos) + 1 and e.frames[0].q < 0.05
    fe = e2.frames[0]
    assert fe.q == pytest.approx(1 - (1 - e.frames[0].q) * 0.1) and fe.w.sum() == pytest.approx(1.0)
    top = fe.places[0]
    assert (top.lat, top.lon) == FARM and top.mass == pytest.approx(0.9) and top.read_mass == pytest.approx(0.9) and not top.in_window
    assert e2.photos[top.best].uuid.startswith(evidence_mod.READING_PREFIX)


# -- the solve --------------------------------------------------------------------------------


def test_a_frame_at_a_much_visited_place_is_placed_even_when_no_visit_stands_out(world):
    # Looks like the house on no day in particular: the place is certain, the day is not.
    model, sol, _ = run(world, [photo_vec(0)])
    a = sol.assignments[0]
    assert a.location == "ok" and a.location_source == "visual" and a.place_confidence > 0.9
    assert abs(a.lat - HOUSE[0]) < 1e-3 and a.place_uuid.startswith("H")
    assert a.confidence < 0.5                                # which visit: not known, and not claimed


def test_the_visit_whose_photos_match_dates_the_frame_beside_its_photo(world):
    model, sol, _ = run(world, [photo_vec(0, 6, strength=0.6)])          # the day-6 visit, clearly
    a = sol.assignments[0]
    assert a.time.date() == at(6).date() and a.time in {p.date for p in world["pool"]}      # beside a photo of that visit
    assert a.location_source == "visual" and a.confidence > 0.6


def test_order_decides_between_visits_and_a_minor_place_does_not_draw_frames_in(world):
    # Frame 1 is the church (day 5). Frames 2 and 3 look like the house and, by a hair, like a
    # visit *before* the church — which order forbids: they belong to a later house visit, not
    # to the church's single time slot, however little the house's vote is worth per visit.
    church = photo_vec(1, 10, strength=0.6)
    house_early = unit(photo_vec(0) + 0.03 * proto(4))
    model, sol, _ = run(world, [church, house_early, house_early])
    a = sol.assignments
    assert abs(a[0].lat - CHURCH[0]) < 1e-3 and a[0].time.date() == at(5).date()
    for x in a[1:]:
        assert abs(x.lat - HOUSE[0]) < 1e-3 and x.location_source == "visual"
        assert x.time > a[0].time and x.time.date() >= at(5).date()
    assert a[1].time <= a[2].time


def test_a_frame_that_resembles_nothing_gets_no_pin_from_its_photos(world):
    model, sol, _ = run(world, [unit(proto(12) + 0.3 * proto(15))])
    a = sol.assignments[0]
    assert a.location_source != "visual" and a.place_uuid is None
    assert sol.places.get(0) is None or not sol.places[0].decided


def test_a_name_read_off_the_frame_places_it_off_the_trail(world):
    r = readings_mod.Reading(1, "sign", "YOUNG FAMILY FARM", "Young Family Farm", FARM[0], FARM[1], 0.9)
    model, sol, _ = run(world, [photo_vec(0, 4, 0.6), unit(proto(12) + 0.3 * proto(15)), photo_vec(0, 8, 0.6)], readings=[r])
    a = sol.assignments[1]
    assert a.location == "ok" and a.location_source == "reading" and a.place_name == "Young Family Farm"
    assert (a.lat, a.lon) == FARM and a.place_uuid is None
    assert sol.assignments[0].time <= a.time <= sol.assignments[2].time
    assert model.states[a.state].kind == "gap"               # no photo there: somewhere in the silent time between


def test_a_recorded_visit_gives_the_reading_its_day(world):
    r = readings_mod.Reading(0, "sign", "YOUNG FAMILY FARM", "Young Family Farm", FARM[0], FARM[1], 0.9)
    trail = [TrailPoint(at(7, 16, 0), FARM[0], FARM[1] + 0.0002, "swarm", 7200, label="Young Family Farm")]
    vs = visits_mod.from_trail(trail)
    assert len(vs) == 1 and vs[0].label == "Young Family Farm" and vs[0].t_lo < at(7, 16) < vs[0].t_hi
    model, sol, _ = run(world, [unit(proto(12) + 0.3 * proto(15))], readings=[r], visits=vs)
    a = sol.assignments[0]
    assert a.location_source == "reading" and vs[0].t_lo <= a.time <= vs[0].t_hi
    assert model.states[a.state].visit and model.states[a.state].venue == "Young Family Farm"


def test_reach_keeps_a_far_place_out_of_a_short_gap(world):
    # A reading 400 km away cannot be reached in the hours between two bursts at the house.
    far = readings_mod.Reading(0, "sign", "SOMEWHERE ELSE", "Somewhere Else", 47.0, 8.0, 0.9)
    from filmgeo.align.model import State, _reachable

    states = build_states(WINDOW, world["events"], [])
    long = next(s for s in states if s.kind == "gap" and s.reach_hours > 20)
    short = State("gap", at(2, 18, 20), at(2, 19, 10), reach_from=HOUSE, reach_to=HOUSE, reach_hours=50 / 60)

    p = evidence_mod.EvidenceParams()
    assert not _reachable(short, far.lat, far.lon, p) and _reachable(long, far.lat, far.lon, p)
    assert _reachable(short, *FARM, p)


# -- states -----------------------------------------------------------------------------------


def test_gaps_between_bursts_at_one_place_are_stays_and_visits_split_the_timeline(world):
    states = build_states(WINDOW, world["events"], [])
    gaps = [s for s in states if s.kind == "gap"]
    # Between the house on day 2 and day 4: a stay there. Round the church trip: not.
    assert any(s.stay and abs(s.lat - HOUSE[0]) < 1e-3 and s.t_lo.day in (2, 3) for s in gaps)
    assert all(not s.stay for s in gaps if at(5, 0) <= s.t_lo < at(5, 11))
    # A check-in at the farm in the middle of day 7's silence becomes a state of its own.
    v = visits_mod.Visit(at(7, 15, 45), at(7, 16, 30), *FARM, "Young Family Farm")
    with_visit = build_states(WINDOW, world["events"], [], visits=[v])
    vs = [s for s in with_visit if s.visit]
    assert len(vs) == 1 and (vs[0].t_lo, vs[0].t_hi) == (v.t_lo, v.t_hi) and vs[0].has_location
    assert len(with_visit) == len(states) + 2                  # the visit, and the gap it cut in two
    # A visit inside an event somewhere else splits the event round it; one at the event's own place does not.
    inside = visits_mod.Visit(at(2, 18, 3), at(2, 18, 6), *FARM, "stop")
    split = build_states(WINDOW, world["events"], [], visits=[inside])
    day2 = [s for s in split if s.kind == "event" and s.t_lo.day == 2]
    assert len(day2) == 2 and day2[0].event == day2[1].event and any(s.visit for s in split)
    same = visits_mod.Visit(at(2, 18, 3), at(2, 18, 6), *HOUSE, "home")
    assert not any(s.visit for s in build_states(WINDOW, world["events"], [], visits=[same]))


def test_visits_ignore_photos_and_passing_route_points():
    trail = [TrailPoint(at(3, 9), 43.0, 11.0, "photos", 7200),
             TrailPoint(at(3, 10), 43.1, 11.1, "health", 7200), TrailPoint(at(3, 10, 1), 43.1001, 11.1, "health", 7200),
             TrailPoint(at(3, 12), 43.2, 11.2, "visit", 7200, label="Cafe", ref="v1"), TrailPoint(at(3, 12, 40), 43.2, 11.2, "visit", 7200, label="Cafe", ref="v1")]
    vs = visits_mod.from_trail(trail)
    assert [(v.label, v.t_lo, v.t_hi) for v in vs] == [("Cafe", at(3, 12), at(3, 12, 40))]


# -- gazetteer and readings -------------------------------------------------------------------


def test_name_match_wants_the_name_both_ways():
    assert name_match("YOUNG FAMILY FARM", "Young Family Farm") == 1.0
    assert name_match("TEATRO BOITO", "Cinema Teatro Boito") == pytest.approx(2 / 3)
    assert name_match("Cattedrale di Santa Maria del Fiore", "Santa Maria del Fiore") >= 0.75
    assert name_match("Tuscan vineyard", "Apartment among Tuscan vineyard hill") < 0.6       # the query's words, not the hit's name
    assert name_match("IL CAFFÈ ESPRESSO", "Accademia del Caffè Espresso") < 0.7
    assert name_match("bedroom", "Bedroom Furniture by Woodforms") == 0.0                    # one shared word is not a name


class FakeVerdict:
    def __init__(self, **clues):
        self.clues = clues


def test_readings_prefer_what_sign_and_guess_agree_on(tmp_path, world):
    table = {
        "young family farm": [Hit("Young Family Farm", *FARM)],
        "fresh cut flowers": [Hit("Fresh Cut Flowers", HOUSE[0] + 0.001, HOUSE[1])],        # a shop of that name, nearer the trail
        "young family farm little compton": [Hit("Young Family Farm", *FARM)],
        "bedroom": [Hit("Bedroom Furniture by Woodforms", HOUSE[0], HOUSE[1])],
        "santa croce church greve chianti": [Hit("Greve in Chianti", 43.58, 11.31)],        # a town, not the church
    }
    asked = []

    def backend(query, near):
        from filmgeo.gazetteer import norm_tokens

        asked.append(query)
        return table.get(" ".join(norm_tokens(query)), [])

    gz = Gazetteer(tmp_path / "gz.json", backend=backend, min_interval=0)
    verdicts = {
        1: FakeVerdict(signage_text=["YOUNG FAMILY FARM", "EST. 1997", "VEGETABLES", "Fresh Cut Flowers"], place_guess="Young Family Farm, Little Compton"),
        2: FakeVerdict(signage_text=[], place_guess="bedroom"),
        3: FakeVerdict(signage_text=[], place_guess="Santa Croce church, Greve in Chianti"),
        4: FakeVerdict(signage_text=[], place_guess=None),
    }
    out = readings_mod.from_verdicts(verdicts, world["pool"], gz)
    assert [(r.frame, r.kind, r.name, r.confidence) for r in out] == [(0, "sign", "Young Family Farm", readings_mod.SIGN_CONFIDENCE)]
    assert "bedroom" not in [a.lower() for a in asked]                                      # a one-word guess is never asked
    # Cached: a second pass asks nothing, and offline finds the same.
    n = len(asked)
    assert readings_mod.from_verdicts(verdicts, world["pool"], Gazetteer(tmp_path / "gz.json", backend=backend, min_interval=0), offline=True)[0].name == "Young Family Farm"
    assert len(asked) == n


def test_gazetteer_does_not_cache_a_backend_that_could_not_answer(tmp_path):
    gz = Gazetteer(tmp_path / "gz.json", backend=lambda q, near: None, min_interval=0)
    assert gz.lookup("Young Family Farm", HOUSE) == [] and gz.cache == {}
    assert gz.find("Young Family Farm", HOUSE, 1000.0, offline=True) is None


# -- small things that came with it ------------------------------------------------------------


def test_clusters_match_the_quadratic_version():
    rng = np.random.default_rng(0)
    pts = [TrailPoint(at(1, 0, i), 43.0 + (i % 3) * 0.01 + rng.normal(0, 1e-4), 11.0 + rng.normal(0, 1e-4), "photos") for i in range(60)]
    out = clusters(pts)
    assert sorted(c.count for c in out) == [20, 20, 20] and all(c.spread_m < 100 for c in out)


def test_atlas_takes_a_spread_of_each_place_and_leaves_out_scans_and_messages():
    assets = [Asset(f"A{i}", f"A{i}.heic", at(1) + timedelta(days=30 * i), 0, 43.0, 11.0, derivative=f"/d/A{i}.jpeg") for i in range(40)]
    assets += [Asset("B0", "B0.heic", at(1), 0, 44.0, 12.0, derivative="/d/B0.jpeg"),
               Asset("S0", "874466_0001.jpg", at(1), 0, 44.0, 12.0, derivative="/d/S0.jpeg"),
               Asset("M0", "M0.heic", at(1), 0, 45.0, 13.0, derivative="/x/scopes/syndication/M0.jpeg"),
               Asset("N0", "N0.heic", at(1), 0, None, None, derivative="/d/N0.jpeg")]
    out = library.atlas(assets, cap=4)
    ids = [a.uuid for a in out]
    assert ids == ["A0", "B0", "A13", "A26", "A39"]                     # first and last of the big cell, evenly between; in time order


def test_embed_cache_saves_in_chunks_and_survives_an_unreadable_chunk(tmp_path, monkeypatch):
    from filmgeo.embed import cache as cache_mod

    monkeypatch.setattr(cache_mod, "VECTORS", tmp_path)

    class Fake:
        def encode(self, paths, batch_size=16):
            if all(p.startswith("bad") for p in paths):
                return np.zeros((len(paths), 1), dtype=np.float32)        # what the embedder returns when nothing could be read
            return np.stack([np.full(4, float(len(p)), dtype=np.float32) for p in paths])

    seen = []
    keys = [f"k{i}" for i in range(5)]
    paths = ["a", "bb", "bad1", "bad2", "ccc"]
    v = cache_mod.embed_cached(Fake(), keys, paths, "x", chunk=2, progress=lambda d, t: seen.append((d, t)))
    assert v.shape == (5, 4) and seen == [(2, 5), (4, 5), (5, 5)]
    assert (v[2] == 0).all() and (v[3] == 0).all() and v[4, 0] == 3.0
    again = cache_mod.VectorCache("x")
    assert again.keys == keys and not list(tmp_path.glob("x/*.tmp.npy"))


# -- through the pipeline ----------------------------------------------------------------------


def test_solve_run_carries_place_fields_and_freezes_what_is_confirmed(world):
    from filmgeo.align import pipeline
    from filmgeo.align.overrides import RollOverrides
    from filmgeo.align.pipeline import FrameRef
    from filmgeo.signals.user_facts import RollFacts

    frame_vecs = np.stack([photo_vec(1, 10, 0.6), photo_vec(0), photo_vec(0, 8, 0.6)])
    frames = [FrameRef(i + 1, f"f{i + 1}", None) for i in range(3)]
    sims = frame_vecs @ world["vecs"].T
    e = evidence_mod.build(frame_vecs, world["pool"], world["vecs"], world["event_ids"])
    facts, ov = RollFacts("r"), RollOverrides("r")

    def solve_(overrides, evidence=e):
        return pipeline.solve_run("r", "r", frames, facts, WINDOW, "test", world["pool"], world["events"], world["event_ids"], sims,
                                  {}, {}, [], None, overrides, evidence)

    r = solve_(ov)
    j = pipeline.to_json(r)
    assert j["engine"] == "v2" and j["atlas"] == 0 and j["readings"] == [] and j["visits"] == 0
    f2 = j["frames"][1]
    assert f2["location_source"] == "visual" and f2["place_confidence"] > 0.9 and f2["place_uuid"].startswith("H") and f2["place_name"] is None
    assert pipeline.resolve(r).evidence is e                                 # a re-solve keeps the evidence it was built with

    # Confirm frame 2 as it stands; then re-solve with the first engine: it does not move.
    ov.frame(2).confirmed = True
    r2 = solve_(ov)
    snap = ov.frames[2].snapshot
    assert snap["time"] == r.solution.assignments[1].time.isoformat() and snap["location_source"] == "visual"
    r3 = solve_(ov, evidence=None)
    a = r3.solution.assignments[1]
    assert a.time.isoformat() == snap["time"] and (a.lat, a.lon) == (snap["lat"], snap["lon"]) and a.location_source == "visual"
    assert pipeline.to_json(r3)["engine"] == "v1" and pipeline.to_json(r3)["frames"][1]["status"] == "confirmed"
    # The unconfirmed neighbour is the first engine's again: no photo-placed pin without verification.
    assert r3.solution.assignments[2].location_source != "visual"
