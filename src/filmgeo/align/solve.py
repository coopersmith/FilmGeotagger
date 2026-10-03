"""Viterbi proposal and forward-backward posteriors over a `RollModel` (COO-115).

Both run in log space over an (n_frames × S × S) lattice. S is a few hundred for a month-wide
window (events + gaps + anchors), so numpy broadcasting over the S×S transition matrix per
frame is fast enough: 38 × 500 × 500 is under ten million operations.

Per-frame outputs:

* the Viterbi state — the proposal shown in the UI;
* the posterior over states — its mass on the chosen *occasion* (the event, with every anchor
  head, tail and this frame's own anchors over it) is the confidence, and the smallest set of
  states carrying 90% of the mass gives the time interval `t_lo..t_hi`;
* the posterior mass on `outside`, the wrong-window signal COO-118 consumes.

Assigned times: anchored frames take the anchor's instant exactly, while their interval is the
anchor's occasion (COO-145). Unanchored frames get the midpoint of their state's interval, then
every time is forced strictly increasing with >= 2 s spacing so scan order survives in Photos
and Lightroom (PLAN.md). Uncertainty lives in `t_lo/t_hi`, never in the written time.
"""

from __future__ import annotations

import dataclasses
from dataclasses import dataclass, field
from datetime import datetime, timedelta

import numpy as np

from filmgeo.align.model import NEG, RollModel, State

MIN_SPACING = timedelta(seconds=2)
INTERVAL_MASS = 0.9


@dataclass
class Assignment:
    frame: int                       # 0-based
    state: int
    source: str                      # anchored | locked | interpolated | skipped
    time: datetime
    t_lo: datetime
    t_hi: datetime
    confidence: float
    outside_mass: float
    anchor_uuid: str | None = None
    tzoffset: int | None = None
    lat: float | None = None
    lon: float | None = None
    event: int | None = None
    # Filled by geo.place(): where the frame was, and how sure that is.
    location: str = "none"                  # ok | ambiguous | none
    location_source: str | None = None      # anchor | trail | interpolated
    clusters: list = field(default_factory=list)   # geo.Cluster, for the UI when ambiguous
    place_confidence: float | None = None   # posterior mass on this place, when the nearest photos chose it (align/locate.py)
    place_uuid: str | None = None           # the photo that shows the place
    place_name: str | None = None           # the name read off the frame, when that is what placed it
    offset_disputed: bool = False           # trail points in the interval, or the neighbouring anchors, disagree on offset
    offsets: list[int] = field(default_factory=list)   # the distinct offsets in play when disputed, for the UI to offer


@dataclass
class Solution:
    path: list[int]
    posterior: np.ndarray            # (n_frames, S)
    log_score: float                 # Viterbi log score of the proposal
    assignments: list[Assignment]
    places: dict = field(default_factory=dict)      # frame -> locate.PlaceDecision, when there is place evidence

    @property
    def anchored(self) -> int:
        return sum(a.source in ("anchored", "locked") for a in self.assignments)


def _pair_transitions(model: RollModel, i: int) -> np.ndarray:
    """Transition matrix for frames i -> i+1 inside one outing: the joint-day penalty, and the (off) bonus.

    Two consecutive frames Claude grouped into one outing may not change calendar day: every
    transition between states whose day ranges do not meet costs `outing_day_penalty`. That is
    the joint constraint COO-147 asked for — an outing maps onto a day, and the frames inside
    it are then placed by similarity and anchors as before — expressed pairwise, which is
    exact for the chain a first-order model can see.
    """
    tr = model.transitions
    if (i, i + 1) not in model.same_outing:
        return tr
    out = tr
    if model.params.outing_day_penalty:
        out = np.where(model.same_day() | ~np.isfinite(tr), out, out - model.params.outing_day_penalty)
    if model.params.outing_bonus:
        ev = np.array([s.event if s.event is not None else -1 for s in model.states])
        same = (ev[:, None] == ev[None, :]) & (ev[:, None] >= 0)
        out = np.where(same & np.isfinite(tr), out + model.params.outing_bonus, out)
    return out


def viterbi(model: RollModel, kinds: set[str] | None = None) -> tuple[list[int], float]:
    """Best path. `kinds` restricts the states allowed (e.g. {"gap", "outside"} for the null path)."""
    em = model.emissions
    if kinds is not None:
        mask = np.array([s.kind in kinds for s in model.states])
        em = np.where(mask[None, :], em, NEG)
    n, S = em.shape
    score = em[0].copy()
    back = np.zeros((n, S), dtype=np.int64)
    for i in range(1, n):
        cand = score[:, None] + _pair_transitions(model, i - 1)      # (from, to)
        back[i] = np.argmax(cand, axis=0)
        score = cand[back[i], np.arange(S)] + em[i]
    if not np.isfinite(score.max()):
        raise ValueError("no feasible path: the constraints exclude every monotone assignment")
    path = [int(np.argmax(score))]
    for i in range(n - 1, 0, -1):
        path.append(int(back[i, path[-1]]))
    path.reverse()
    return path, float(score.max())


def _logsumexp(x: np.ndarray, axis: int) -> np.ndarray:
    m = np.max(x, axis=axis, keepdims=True)
    m = np.where(np.isfinite(m), m, 0.0)
    with np.errstate(divide="ignore"):
        return np.squeeze(m, axis=axis) + np.log(np.sum(np.exp(x - m), axis=axis))


def forward_backward(model: RollModel) -> np.ndarray:
    em = model.emissions
    n, S = em.shape
    fwd = np.empty((n, S))
    bwd = np.empty((n, S))
    fwd[0] = em[0]
    for i in range(1, n):
        fwd[i] = _logsumexp(fwd[i - 1][:, None] + _pair_transitions(model, i - 1), axis=0) + em[i]
    bwd[n - 1] = 0.0
    for i in range(n - 2, -1, -1):
        bwd[i] = _logsumexp(_pair_transitions(model, i) + (em[i + 1] + bwd[i + 1])[None, :], axis=1)
    post = fwd + bwd
    z = _logsumexp(post, axis=1)
    with np.errstate(invalid="ignore"):
        post = np.exp(post - z[:, None])
    return np.nan_to_num(post)


def _interval(model: RollModel, post: np.ndarray, mass: float = INTERVAL_MASS) -> tuple[datetime, datetime]:
    """Union of the fewest states carrying `mass` of the posterior. Clipped later by the caller."""
    order = np.argsort(-post)
    lo, hi, total = None, None, 0.0
    for j in order:
        s = model.states[j]
        if s.kind != "outside":
            lo = s.t_lo if lo is None else min(lo, s.t_lo)
            hi = s.t_hi if hi is None else max(hi, s.t_hi)
        total += post[j]
        if total >= mass:
            break
    if lo is None:                      # all mass outside the window
        return model.window.start, model.window.end
    return lo, hi


def _intervals(model: RollModel, path: list[int], post: np.ndarray) -> list[tuple[datetime, datetime]]:
    """Per-frame 90% intervals, clipped to the frame's facts and to the anchors around it.

    A state's span can extend well past what the monotone order leaves possible for one frame:
    the gap before an anchored frame's photo, or the whole week a fact has already ruled out.
    An anchored frame reports its *occasion* (the anchor's event span, at least an hour wide,
    COO-145), not the photo's second: that is what the verdict vouches for. Frames before it
    therefore end no later than the occasion's end, and frames after it start no earlier than
    its start.
    """
    n = len(path)
    raw = []
    for i, j in enumerate(path):
        s = model.states[j]
        lo, hi = (s.occ_lo, s.occ_hi) if s.kind == "anchor" else _interval(model, post[i])
        if model.bounds:
            b_lo, b_hi = model.bounds[i]
            lo, hi = max(lo, b_lo), min(hi, b_hi)
        raw.append([lo, hi])
    occ = [(model.states[j].occ_lo, model.states[j].occ_hi) if model.states[j].kind == "anchor" else None for j in path]
    last = None
    for i in range(n):
        if last is not None:
            raw[i][0] = max(raw[i][0], last)
        if occ[i] is not None:
            last = occ[i][0]
    nxt = None
    for i in range(n - 1, -1, -1):
        if nxt is not None:
            raw[i][1] = min(raw[i][1], nxt)
        if occ[i] is not None:
            nxt = occ[i][1]
    return [(lo, max(lo, hi)) for lo, hi in raw]


def _assign_times(model: RollModel, path: list[int], intervals: list[tuple[datetime, datetime]]) -> list[datetime]:
    times: list[datetime] = []
    fixed: list[bool] = []
    for j, (lo, hi) in zip(path, intervals):
        s = model.states[j]
        if s.kind == "anchor":
            times.append(s.t_lo)
            fixed.append(True)
        else:
            mid = s.t_lo + (s.t_hi - s.t_lo) / 2
            # In an event, beside the photo of it that looks most like the frame: a better
            # guess at the minute than the middle of the visit.
            c = model.choices.get((len(times), j))
            if c is not None and c[4] and model.evidence is not None:
                mid = model.evidence.photos[c[2]].date
            # Intervals are half-open on the right: a fact "14:05" is [14:05, 14:06), and the
            # written time must not land on the excluded end.
            times.append(min(max(mid, lo), max(lo, hi - timedelta(seconds=1))))
            fixed.append(False)
    # Force strict ordering with >= 2 s spacing by moving only the unanchored frames: forward
    # so nothing precedes what came before it, then backward so nothing runs into the next
    # anchor. Anchored frames keep their photo's instant exactly (PLAN.md); two anchors on the
    # same photo therefore stay equal, which is the truth and what the user would write.
    for i in range(1, len(times)):
        if not fixed[i] and times[i] < times[i - 1] + MIN_SPACING:
            times[i] = times[i - 1] + MIN_SPACING
    for i in range(len(times) - 2, -1, -1):
        if not fixed[i] and times[i] > times[i + 1] - MIN_SPACING:
            times[i] = times[i + 1] - MIN_SPACING
            # Squeezed between two anchors on one instant there is no room for spacing;
            # order wins over spacing, and the frame sits on that instant too.
            if i > 0 and fixed[i - 1] and times[i] < times[i - 1]:
                times[i] = times[i - 1]
    return times


def posterior_path(model: RollModel, post: np.ndarray) -> list[int]:
    """The monotone path that collects the most posterior mass, frame by frame.

    Viterbi returns the single likeliest joint assignment, and a single assignment favours
    whatever is *peaked*: one visit whose photo matches at even odds outbids the forty other
    visits that share the other half between them, and six frames each chasing their own
    look-alike day can out-vote three verified anchors. The posterior adds the forty up. This
    path maximises the sum of the per-frame marginals over the transitions the model allows,
    so each frame sits where the weight of *all* consistent explanations puts it, and the
    order of the roll still holds.
    """
    n, S = post.shape
    allowed = np.isfinite(model.transitions)
    # A verified anchor stands for its occasion: the instant and "somewhere on the same
    # occasion" answer the question the same way, and split between them neither would win.
    post = post.copy()
    for j, s in enumerate(model.states):
        if s.kind == "anchor":
            same = [k for k, t in enumerate(model.states) if t.kind == "event" and t.event == s.event]
            post[s.frame, j] += post[s.frame, same].sum()
    score = np.where(np.isfinite(model.emissions[0]), post[0], -np.inf)
    back = np.zeros((n, S), dtype=np.int64)
    for i in range(1, n):
        cand = np.where(allowed, score[:, None], -np.inf)
        back[i] = np.argmax(cand, axis=0)
        best = cand[back[i], np.arange(S)]
        score = np.where(np.isfinite(model.emissions[i]), best + post[i], -np.inf)
    if not np.isfinite(score.max()):
        return viterbi(model)[0]
    path = [int(np.argmax(score))]
    for i in range(n - 1, 0, -1):
        path.append(int(back[i, path[-1]]))
    path.reverse()
    return path


def solve(model: RollModel) -> Solution:
    path, score = viterbi(model)
    post = forward_backward(model)
    places: dict = {}
    if model.evidence is not None:
        # Place first (align/locate.py), then the time within it. The overwhelming votes are
        # decided and held; the roll is solved again; the remaining frames take the heaviest
        # place that leaves possible, and are held too; the last solve gives the times.
        from filmgeo.align import locate

        def narrowed(m: RollModel, decided: dict) -> RollModel | None:
            em = m.emissions.copy()
            changed = False
            for i, d in decided.items():
                if not d.decided or i in m.skipped or any(s.kind == "anchor" and s.frame == i for s in m.states):
                    continue                              # a verified or picked photo already says where
                keep = locate.hosts(m, i, d)
                row = np.full(em.shape[1], NEG)
                row[keep] = em[i, keep]
                if np.isfinite(row).any() and not np.array_equal(np.isfinite(row), np.isfinite(em[i])):
                    em[i], changed = row, True
            if not changed:
                return m
            out = dataclasses.replace(m, emissions=em)
            try:
                viterbi(out)
            except ValueError:
                return None                               # these places contradict the order
            return out

        base = model
        sure = locate.decide(base, None)
        held = narrowed(base, sure)
        if held is None:
            held, sure = base, {}
        places = locate.decide(held, forward_backward(held), sure)
        final = narrowed(base, places)
        if final is None:
            final, places = held, {i: d for i, d in places.items() if i in sure}
        model = final
        path, _ = viterbi(model)
        post = forward_backward(model)
        if model.params.decode == "posterior":
            path = posterior_path(model, post)
    intervals = _intervals(model, path, post)
    times = _assign_times(model, path, intervals)
    out_js = model.outside
    assignments = []
    for i, j in enumerate(path):
        s: State = model.states[j]
        lo, hi = intervals[i]
        if s.kind == "anchor":
            source = "locked" if s.locked else "anchored"
        elif i in model.skipped:
            source = "skipped"
        else:
            source = "interpolated"
        # Confidence is the mass on the frame's *occasion*: the event state, every anchor's
        # head and tail over it, and this frame's own anchors in it. They all answer "which
        # occasion" the same way and differ only in whether the instant is pinned — and the
        # occasion is what a verdict vouches for (COO-145). Reporting the anchor state alone
        # would fall as soon as a neighbour's tail offers "same occasion, not that second" as
        # a cheap alternative. Gaps and outside are single states and keep their own mass.
        if s.event is not None:
            same = [k for k, t in enumerate(model.states) if t.event == s.event
                    and (t.kind == "event" or (t.kind == "anchor" and t.frame == i))]
        else:
            same = [j]
        confidence = float(post[i, same].sum())
        c = model.choices.get((i, j))
        if model.evidence is not None and s.kind == "event" and c is not None and c[4] and s.event is not None:
            # An unverified frame beside a photo of its visit: how often that is the right
            # visit was measured, and the posterior — forty other visits to the same house at
            # a seventh of the support each — does not say it (align/evidence.py).
            from filmgeo.align.evidence import occasion_confidence

            rivals = [t.event for (k, jj), cc in model.choices.items() if k == i and (t := model.states[jj]).kind == "event"
                      and t.event is not None and abs(cc[0] - c[0]) < 1e-9 and abs(cc[1] - c[1]) < 1e-9]
            confidence = max(confidence, occasion_confidence(model.evidence.frames[i], model.evidence.params, s.event, rivals))
        assignments.append(
            Assignment(
                frame=i, state=j, source=source, time=times[i], t_lo=lo, t_hi=hi,
                confidence=confidence, outside_mass=float(post[i, out_js].sum()),
                anchor_uuid=s.uuid, tzoffset=s.tzoffset, lat=s.lat, lon=s.lon, event=s.event,
            )
        )
    return Solution(path, post, score, assignments, places)


def null_score(model: RollModel) -> float:
    """Log score of the best path that uses no event or anchor — the wrong-window baseline."""
    try:
        return viterbi(model, kinds={"gap", "outside"})[1]
    except ValueError:
        return float("-inf")
