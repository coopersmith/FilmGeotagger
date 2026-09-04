from datetime import datetime, timedelta, timezone

from filmgeo.align.checks import RollInputs, best_days, new_candidates, reverse_test, widen, window_check
from filmgeo.align.model import Anchor
from filmgeo.align.solve import solve
from filmgeo.events import Event
from filmgeo.signals.base import Constraint, Window

UTC = timezone.utc
T0 = datetime(2026, 4, 1, tzinfo=UTC)


def at(day, hour=0):
    return T0 + timedelta(days=day - 1, hours=hour)


EVENTS = [Event(0, at(2, 9), at(2, 11), 41.0, -71.0, 50.0, 5), Event(1, at(5, 14), at(5, 17), 41.2, -71.0, 50.0, 5),
          Event(2, at(9, 12), at(9, 15), 42.0, -70.0, 50.0, 5)]
WINDOW = Window(at(1), at(31))


def anchors(order):
    """Anchors on frames 0, 3, 6 in the given day order."""
    days = {0: (2, 10, 0), 1: (5, 15, 1), 2: (9, 13, 2)}
    return [Anchor(f, f"u{f}", at(*days[o][:2]), days[o][2], 0.9, similarity=0.9) for f, o in zip((0, 3, 6), order)]


def test_reverse_test_flags_a_reversed_roll_and_not_a_forward_one():
    fwd = RollInputs(WINDOW, EVENTS, 7, anchors((0, 1, 2)))
    assert not reverse_test(fwd).suspect
    rev = RollInputs(WINDOW, EVENTS, 7, anchors((2, 1, 0)))
    r = reverse_test(rev)
    assert r.suspect and r.reverse_anchored == 3 and r.forward_anchored <= 1 and r.margin > 0


def test_reverse_needs_three_anchors():
    two = RollInputs(WINDOW, EVENTS, 7, anchors((2, 1, 0))[:2])
    r = reverse_test(two)
    assert r.reverse_anchored == 2 and not r.suspect


def test_reversed_inputs_flip_everything():
    inp = RollInputs(WINDOW, EVENTS, 5, [Anchor(1, "u", at(2, 10), 0, 0.9)],
                     constraints=[Constraint("frame", "user", frame=2, same_day_as=4), Constraint("roll", "user", t_lo=at(1))],
                     same_outing={(0, 1)})
    r = inp.reversed()
    assert r.anchors[0].frame == 3
    fc = [c for c in r.constraints if c.scope == "frame"][0]
    assert (fc.frame, fc.same_day_as) == (4, 2)
    assert r.same_outing == {(3, 4)}
    assert r.reversed().anchors[0].frame == 1


def test_window_check_doubtful_only_with_verification_evidence():
    inp = RollInputs(WINDOW, EVENTS, 10)
    model = inp.build()
    sol = solve(model)
    unverified = window_check(model, sol)
    assert not unverified.doubtful and "no verification" in unverified.reason
    verified_none = window_check(model, sol, n_verified=10)
    assert verified_none.doubtful and verified_none.anchored == 0
    good = RollInputs(WINDOW, EVENTS, 10, anchors((0, 1, 2)))
    gm = good.build()
    gs = solve(gm)
    ok = window_check(gm, gs, n_verified=10)
    assert not ok.doubtful and ok.anchored == 3 and ok.score_per_frame > 0


def test_best_days_follow_the_anchors():
    inp = RollInputs(WINDOW, EVENTS, 7, anchors((0, 1, 2)))
    model = inp.build()
    days = best_days(model, solve(model))
    assert {d for d, _ in days[:3]} >= {at(2).date(), at(9).date()}


def test_widen_and_new_candidates():
    w = widen(WINDOW)
    assert w.start == at(1) - timedelta(days=31) and w.end == at(31) + timedelta(days=31)
    assert new_candidates({0: ["a", "b"]}, {0: ["b", "c"], 1: ["d"]}) == {0: ["c"], 1: ["d"]}


def test_window_check_one_anchor_on_a_short_roll_is_doubtful():
    one = RollInputs(WINDOW, EVENTS, 10, anchors((0, 1, 2))[:1])
    m = one.build()
    assert window_check(m, solve(m), n_verified=10).doubtful


def test_reverse_test_survives_an_infeasible_reversed_order():
    # A lock on frame 3 (day 9) and a date on frame 2 (day 5) are consistent forwards and a
    # contradiction backwards: the reversed model has no state for the locked frame.
    locked = Anchor(2, "u", at(9, 13), 2, 1.0, locked=True)
    dated = Constraint("frame", "user", frame=2, t_lo=at(5), t_hi=at(6))
    inp = RollInputs(WINDOW, EVENTS, 4, [locked], constraints=[dated])
    r = reverse_test(inp)
    assert not r.suspect and r.forward_anchored == 1 and r.reverse_anchored == 0
