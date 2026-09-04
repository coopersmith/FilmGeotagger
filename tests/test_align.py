"""Synthetic fixtures for the alignment HMM: a timeline of events, a roll, some anchors."""

from datetime import datetime, timedelta, timezone

import numpy as np
import pytest

from filmgeo.align.model import AlignParams, Anchor, FrameClues, build_model
from filmgeo.align.solve import forward_backward, null_score, solve, viterbi
from filmgeo.events import Event
from filmgeo.signals.base import Constraint, Window

UTC = timezone.utc
T0 = datetime(2026, 4, 1, tzinfo=UTC)


def at(day, hour=0, minute=0):
    return T0 + timedelta(days=day - 1, hours=hour, minutes=minute)


def event(i, day, h0, h1, lat=41.0, lon=-71.0):
    return Event(i, at(day, h0), at(day, h1), lat, lon, 50.0, 5)


WINDOW = Window(at(1), at(31))
# Three outings: day 2 morning, day 2 afternoon, day 9 (a week later).
EVENTS = [event(0, 2, 9, 11), event(1, 2, 14, 17, lat=41.2), event(2, 9, 12, 15, lat=42.0, lon=-70.0)]


def anchor(frame, day, hour, ev, conf=0.9, **kw):
    return Anchor(frame, f"uuid-{frame}", at(day, hour), ev, conf, similarity=0.7, **kw)


def kinds(model, path):
    return [model.states[j].kind for j in path]


def test_single_day_anchors_are_exact_and_middle_stays_between():
    anchors = [anchor(0, 2, 9, 0), anchor(4, 2, 16, 1)]
    model = build_model(WINDOW, EVENTS, 5, anchors)
    sol = solve(model)
    a = sol.assignments
    assert a[0].source == "anchored" and a[0].time == at(2, 9) and a[0].anchor_uuid == "uuid-0"
    assert a[4].source == "anchored" and a[4].time == at(2, 16)
    for x in a[1:4]:
        assert x.source == "interpolated"
        assert at(2, 9) <= x.time <= at(2, 16)
        assert x.t_lo >= at(2, 9) and x.t_hi <= at(2, 17)         # between the two anchors' events
    assert [x.time for x in a] == sorted(x.time for x in a)
    assert all(a[i + 1].time - a[i].time >= timedelta(seconds=2) for i in range(4))
    assert sol.anchored == 2


def test_multi_day_gap_interval_spans_the_gap():
    anchors = [anchor(0, 2, 15, 1), anchor(3, 9, 13, 2)]
    model = build_model(WINDOW, EVENTS, 4, anchors)
    sol = solve(model)
    mid = sol.assignments[1:3]
    for x in mid:
        assert at(2, 15) <= x.time <= at(9, 13)
        assert x.t_lo >= at(2, 14) and x.t_hi <= at(9, 15)
        assert x.confidence < 0.9                                    # nothing pins them
    # The mass covers the whole week: the 90% interval is not a single event.
    assert (mid[0].t_hi - mid[0].t_lo) >= timedelta(days=1)
    # Posteriors are proper distributions.
    post = forward_backward(model)
    assert np.allclose(post.sum(axis=1), 1.0)


def test_locked_frame_prunes_and_drags_neighbours():
    # Frame 2 is user-locked to day 9; a strong but wrong anchor says frame 1 is on day 9 too,
    # and a weaker one says frame 3 is on day 2. Monotone order must reject the day-2 anchor.
    anchors = [anchor(1, 9, 12, 2, conf=0.95), anchor(2, 9, 13, 2, conf=1.0, locked=True), anchor(3, 2, 10, 0, conf=0.6)]
    model = build_model(WINDOW, EVENTS, 4, anchors)
    sol = solve(model)
    a = sol.assignments
    assert a[2].source == "locked" and a[2].time == at(9, 13) and a[2].confidence == pytest.approx(1.0)
    assert a[3].source != "anchored" and a[3].time >= at(9, 13)
    assert a[0].time <= a[1].time <= a[2].time


def test_no_anchors_is_monotone_and_unsure():
    model = build_model(WINDOW, EVENTS, 6)
    sol = solve(model)
    times = [x.time for x in sol.assignments]
    assert times == sorted(times)
    assert all(x.source == "interpolated" for x in sol.assignments)
    assert all(x.confidence < 0.8 for x in sol.assignments)
    assert all(x.t_hi - x.t_lo > timedelta(days=1) for x in sol.assignments)
    assert null_score(model) <= sol.log_score


def test_conflicting_anchors_keep_the_stronger():
    # Frame 0 strongly on day 9, frame 1 weakly on day 2: a monotone path can hold only one.
    anchors = [anchor(0, 9, 13, 2, conf=0.95), anchor(1, 2, 10, 0, conf=0.55)]
    model = build_model(WINDOW, EVENTS, 3, anchors)
    sol = solve(model)
    assert sol.assignments[0].source == "anchored" and sol.assignments[1].source == "interpolated"
    assert sol.assignments[1].time >= at(9, 13)
    # And the other way round when the strengths swap.
    anchors = [anchor(0, 9, 13, 2, conf=0.55), anchor(1, 2, 10, 0, conf=0.95)]
    sol = solve(build_model(WINDOW, EVENTS, 3, anchors))
    assert sol.assignments[1].source == "anchored" and sol.assignments[0].source == "interpolated"


def test_below_threshold_anchor_is_not_a_state():
    model = build_model(WINDOW, EVENTS, 2, [anchor(0, 2, 9, 0, conf=0.3)])
    assert not model.anchors_for(0)
    assert model.anchors_for(0) == [] and len(build_model(WINDOW, EVENTS, 2, [anchor(0, 2, 9, 0, conf=0.5)]).anchors_for(0)) == 1


def test_frame_date_fact_zeroes_other_days():
    cs = [Constraint("frame", "user", frame=2, t_lo=at(9), t_hi=at(10))]
    model = build_model(WINDOW, EVENTS, 3, constraints=cs)
    sol = solve(model)
    a = sol.assignments
    assert at(9) <= a[1].time < at(10) and a[1].t_lo >= at(9) and a[1].t_hi <= at(10)
    assert a[2].time >= a[1].time
    assert a[1].outside_mass == 0.0                                 # a dated frame is never outside
    # Every state outside day 9 is excluded for frame 2 — including the day-2 events.
    for j, s in enumerate(model.states):
        if s.kind == "event" and s.event in (0, 1):
            assert model.emissions[1, j] == -np.inf


def test_place_fact_excludes_far_events():
    cs = [Constraint("frame", "user", frame=1, lat=42.0, lon=-70.0, radius_m=5000)]
    model = build_model(WINDOW, EVENTS, 2, constraints=cs)
    for j, s in enumerate(model.states):
        if s.kind == "event":
            assert np.isfinite(model.emissions[0, j]) == (s.event == 2)
        if s.kind == "gap":
            assert np.isfinite(model.emissions[0, j])                  # unknown location is allowed


def test_skip_is_uniform_but_still_monotone():
    cs = [Constraint("frame", "user", frame=2, skip=True)]
    anchors = [anchor(0, 2, 9, 0), anchor(2, 9, 13, 2)]
    model = build_model(WINDOW, EVENTS, 3, anchors, constraints=cs)
    sol = solve(model)
    assert sol.assignments[1].source == "skipped"
    assert at(2, 9) <= sol.assignments[1].time <= at(9, 13)
    allowed = np.isfinite(model.emissions[1])
    assert np.all(model.emissions[1][allowed] == 0.0)


def test_similarity_prefers_the_matching_event():
    # No verified anchors, but frame 0 looks like the day-9 photos and frame 1 like nothing.
    pool_events = [0, 0, 1, 1, 2, 2]
    sims = np.array([[0.3, 0.3, 0.3, 0.3, 0.8, 0.75], [0.3] * 6])
    model = build_model(WINDOW, EVENTS, 2, sims=sims, event_ids=pool_events)
    sol = solve(model)
    assert sol.assignments[0].event == 2 and sol.assignments[0].source == "interpolated"
    assert sol.assignments[1].time >= sol.assignments[0].time


def test_clue_penalty_against_event_hours():
    night = [FrameClues(time_of_day="night")]
    noon = [FrameClues(time_of_day="midday")]                 # 10-14 overlaps every event (9-11, 14-17, 12-15)
    m_night = build_model(WINDOW, EVENTS, 1, clues=night)
    m_noon = build_model(WINDOW, EVENTS, 1, clues=noon)
    ev_cols = [j for j, s in enumerate(m_night.states) if s.kind == "event"]
    assert all(m_night.emissions[0, j] < m_noon.emissions[0, j] for j in ev_cols)      # every event is daytime
    assert m_noon.emissions[0, ev_cols[0]] == pytest.approx(np.log(AlignParams().event_floor))


def test_same_outing_bonus_keeps_frames_together():
    sims = np.array([[0.8, 0.3, 0.3], [0.62, 0.62, 0.3]])       # frame 1 torn between event 0 and 1
    kw = dict(sims=sims, event_ids=[0, 1, 2])
    plain = solve(build_model(WINDOW, EVENTS, 2, **kw))
    bonded = solve(build_model(WINDOW, EVENTS, 2, same_outing={(0, 1)}, params=AlignParams(outing_bonus=1.0), **kw))
    assert bonded.assignments[1].event == 0
    assert bonded.posterior[1, bonded.assignments[1].state] > plain.posterior[1, bonded.assignments[1].state]


def test_infeasible_constraints_raise():
    cs = [Constraint("frame", "user", frame=1, t_lo=at(9), t_hi=at(10)),
          Constraint("frame", "user", frame=2, t_lo=at(2), t_hi=at(3))]
    with pytest.raises(ValueError):
        viterbi(build_model(WINDOW, EVENTS, 2, constraints=cs))


def test_reverse_order_is_a_worse_fit():
    anchors = [anchor(0, 2, 9, 0), anchor(3, 9, 13, 2)]
    fwd = solve(build_model(WINDOW, EVENTS, 4, anchors))
    rev = solve(build_model(WINDOW, EVENTS, 4, [Anchor(3 - a.frame, a.uuid, a.time, a.event, a.confidence, a.similarity) for a in anchors]))
    assert fwd.log_score > rev.log_score and rev.anchored < fwd.anchored


def test_anchored_interval_is_the_occasion_not_the_instant():
    # Frame 2 verified against a photo at 10:00 inside the 09:00-11:00 event: the written time
    # is 10:00 exactly, the interval is the event, and neighbours clip to the event's edges.
    model = build_model(WINDOW, EVENTS, 3, [anchor(1, 2, 10, 0)])
    a = solve(model).assignments
    assert a[1].source == "anchored" and a[1].time == at(2, 10)
    assert (a[1].t_lo, a[1].t_hi) == (at(2, 9), at(2, 11))
    assert a[0].t_hi == at(2, 11) and a[2].t_lo == at(2, 9)
    # A lone photo (single-photo event) still gets an hour around it.
    lone = [Event(9, at(20, 15), at(20, 15), 41.0, -71.0, 0.0, 1)]
    m = build_model(WINDOW, EVENTS + lone, 1, [Anchor(0, "u", at(20, 15), 9, 0.9)])
    x = solve(m).assignments[0]
    assert (x.t_lo, x.t_hi) == (at(20, 14, 30), at(20, 15, 30)) and x.time == at(20, 15)
