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
        assert x.t_lo >= at(2, 8, 30) and x.t_hi <= at(2, 17)     # between the two anchors' occasions
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


def test_two_anchors_in_one_event_keep_scan_order():
    # Verification anchored frames 1 and 3 to two photos of the same event, in the wrong
    # order. The old anchor -> own-event exception let the path walk back through the event
    # and the written times came out non-monotone; now one anchor has to go.
    anchors = [anchor(1, 2, 10, 0, conf=0.9), anchor(3, 2, 9, 0, conf=0.9)]
    model = build_model(WINDOW, EVENTS, 5, anchors)
    sol = solve(model)
    times = [x.time for x in sol.assignments]
    assert times == sorted(times) and all(b - a >= timedelta(seconds=2) for a, b in zip(times, times[1:]))
    assert sol.anchored == 1
    # The same pair locked is a contradiction the user has to resolve.
    locked = [anchor(1, 2, 10, 0, conf=1.0, locked=True), anchor(3, 2, 9, 0, conf=1.0, locked=True)]
    with pytest.raises(ValueError):
        solve(build_model(WINDOW, EVENTS, 5, locked))


def test_frames_after_an_anchor_stay_on_its_occasion_through_the_tail():
    # Anchor at 10:00 inside event 0 (09-11). The event state ranks below the anchor, so the
    # next frames stay on the occasion only through the anchor's tail, which spans the
    # occasion (the event, at least an hour around the photo) but ranks after the anchor.
    model = build_model(WINDOW, EVENTS, 4, [anchor(0, 2, 10, 0, conf=0.95)])
    sol = solve(model)
    a = sol.assignments
    assert a[0].source == "anchored"
    for x in a[1:]:
        assert x.time >= at(2, 10)
    tails = [s for s in model.states if s.after_frame is not None]
    assert len(tails) == 1 and (tails[0].t_lo, tails[0].t_hi) == (at(2, 9), at(2, 11))
    j = model.states.index(tails[0])
    assert a[1].state == j and a[1].event == 0
    assert model.states.index(next(s for s in model.states if s.kind == "anchor")) == j - 1
    # The tail is closed to the anchored frame itself and to frames before it.
    assert model.emissions[0, j] == -np.inf and np.isfinite(model.emissions[1, j])
    # Mid-event, the event state serves the frames before the anchor, so there is no head...
    assert not [s for s in model.states if s.before_frame is not None]
    # ...but on the event's first photo the event ranks above the anchor, and a head is needed.
    model2 = build_model(WINDOW, EVENTS, 4, [anchor(2, 2, 9, 0, conf=0.95)])
    heads = [s for s in model2.states if s.before_frame is not None]
    assert len(heads) == 1
    h = model2.states.index(heads[0])
    assert model2.states[h + 1].kind == "anchor"
    assert np.isfinite(model2.emissions[1, h]) and model2.emissions[2, h] == -np.inf
    assert solve(model2).assignments[1].event == 0


def test_frames_sharing_one_photo_keep_all_their_anchors():
    # Frames 2 and 3 both matched to the photo at 09:00 (a burst on one occasion), frame 1 to
    # a weaker photo later the same event. The two strong anchors must survive together, in
    # frame order, and the weak reversed one go.
    anchors = [anchor(0, 2, 10, 0, conf=0.6), anchor(1, 2, 9, 0, conf=0.97), anchor(2, 2, 9, 0, conf=0.96)]
    sol = solve(build_model(WINDOW, EVENTS, 4, anchors))
    a = sol.assignments
    assert a[1].source == "anchored" and a[2].source == "anchored" and a[1].time == a[2].time == at(2, 9)
    assert a[0].source == "interpolated" and a[0].time <= at(2, 9)
    # And a frame between two anchors on one photo sits on that occasion, at that instant.
    anchors = [anchor(0, 2, 9, 0, conf=0.95), anchor(2, 2, 9, 0, conf=0.95)]
    a = solve(build_model(WINDOW, EVENTS, 3, anchors)).assignments
    assert a[0].source == a[2].source == "anchored" and a[1].source == "interpolated"
    assert a[1].t_lo <= at(2, 9) <= a[1].t_hi and a[1].event == 0
    assert a[0].time == a[1].time == a[2].time == at(2, 9)     # order wins over spacing


def test_same_day_as_an_anchored_frame_binds_to_its_day():
    # Frame 1 is anchored to day 2 by a verdict, nothing else is known; "frame 3 is the same
    # day as frame 1" must keep frame 3 (and frame 2, by order) on day 2.
    from filmgeo.align.model import anchored_days

    anchors = [anchor(0, 2, 10, 0, conf=0.9)]
    cs = [Constraint("frame", "user", frame=3, same_day_as=1)]
    derived = anchored_days(anchors, cs)
    assert len(derived) == 1 and derived[0].frame == 1 and derived[0].t_lo == at(2) and derived[0].t_hi == at(3)
    model = build_model(WINDOW, EVENTS, 5, anchors, constraints=cs)
    sol = solve(model)
    a = sol.assignments
    assert at(2) <= a[2].time < at(3) and a[2].t_hi <= at(3)
    assert a[1].t_hi <= at(3)                        # frame 2 sits between them, so the same day
    assert a[4].t_lo >= at(2)                        # frame 5 is only bound by order
    # A pair with no anchored side derives nothing.
    assert anchored_days(anchors, [Constraint("frame", "user", frame=4, same_day_as=5)]) == []


def test_outing_is_a_joint_day_constraint():
    # Frames 0-2 are one outing. Frame 0 is anchored to day 2 at 0.9; frame 2 has a weaker
    # verdict on day 9. Left to itself the solver keeps both (a week's jump is cheap next to a
    # 0.6 anchor); as one outing they cannot be a week apart, so the weaker anchor goes and the
    # whole group sits on day 2.
    anchors = [anchor(0, 2, 10, 0, conf=0.9), anchor(2, 9, 13, 2, conf=0.6)]
    loose = solve(build_model(WINDOW, EVENTS, 3, anchors, params=AlignParams(outing_day_penalty=0.0)))
    assert loose.anchored == 2
    joint = solve(build_model(WINDOW, EVENTS, 3, anchors, same_outing={(0, 1), (1, 2)}))
    a = joint.assignments
    assert joint.anchored == 1 and a[0].source == "anchored"
    assert all(at(2) <= x.time < at(3) for x in a), [x.time for x in a]
    # With a strong anchor right after the group on day 9, the evidence balance moves the whole
    # group to day 9 (a week's jump and a lost 0.6 anchor cost more than frame 0's 0.9). Either
    # way the group shares one day — that is the constraint — and the frame after is free.
    anchors2 = anchors + [anchor(3, 9, 14, 2, conf=0.9)]
    after = solve(build_model(WINDOW, EVENTS, 4, anchors2, same_outing={(0, 1), (1, 2)}))
    assert after.assignments[3].source == "anchored" and after.assignments[3].time == at(9, 14)
    assert len({x.time.date() for x in after.assignments[:3]}) == 1


def test_outing_penalty_yields_to_a_lock():
    # Two locked frames in one outing on different days contradict the group; the solve still
    # goes through (a finite penalty, not -inf) and both locks hold.
    anchors = [anchor(0, 2, 10, 0, conf=1.0, locked=True), anchor(1, 9, 13, 2, conf=1.0, locked=True)]
    sol = solve(build_model(WINDOW, EVENTS, 2, anchors, same_outing={(0, 1)}))
    assert sol.anchored == 2


def test_same_day_matrix_reads_day_ranges():
    model = build_model(WINDOW, EVENTS, 2, [anchor(0, 2, 10, 0)])
    sd = model.same_day()
    idx = {s.label(): j for j, s in enumerate(model.states)}
    e0 = next(j for j, s in enumerate(model.states) if s.kind == "event" and s.event == 0 and s.after_frame is None)
    e2 = next(j for j, s in enumerate(model.states) if s.kind == "event" and s.event == 2 and s.after_frame is None)
    gap = next(j for j, s in enumerate(model.states) if s.kind == "gap" and s.t_lo == at(2, 17))   # day 2 evening, up to midnight
    assert model.states[gap].t_hi == at(3)                                                            # gaps are cut at midnight
    gap8 = next(j for j, s in enumerate(model.states) if s.kind == "gap" and s.t_lo == at(9))       # day 9 morning, up to the event
    assert sd[e0, e0] and not sd[e0, e2] and sd[e0, gap] and not sd[gap, e2] and sd[gap8, e2]
    gaps = [s for s in model.states if s.kind == "gap"]
    assert all(s.t_lo.date() == (s.t_hi - timedelta(seconds=1)).date() for s in gaps)
    assert all(sd[j, k] for j in model.outside for k in range(len(model.states)))
    assert idx  # labels are unique enough to build
