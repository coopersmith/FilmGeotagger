"""Viterbi proposal and forward-backward posteriors over a `RollModel` (COO-115).

Both run in log space over an (n_frames × S × S) lattice. S is a few hundred for a month-wide
window (events + gaps + anchors), so numpy broadcasting over the S×S transition matrix per
frame is fast enough: 38 × 500 × 500 is under ten million operations.

Per-frame outputs:

* the Viterbi state — the proposal shown in the UI;
* the posterior over states — its mass on the chosen state is the confidence, and the
  smallest set of states carrying 90% of the mass gives the time interval `t_lo..t_hi`;
* the posterior mass on `outside`, the wrong-window signal COO-118 consumes.

Assigned times: anchored frames take the anchor's instant exactly. Unanchored frames get the
midpoint of their state's interval, then every time is forced strictly increasing with >= 2 s
spacing so scan order survives in Photos and Lightroom (PLAN.md). Uncertainty lives in
`t_lo/t_hi`, never in the written time.
"""

from __future__ import annotations

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
    offset_disputed: bool = False           # trail points in the interval disagree on offset


@dataclass
class Solution:
    path: list[int]
    posterior: np.ndarray            # (n_frames, S)
    log_score: float                 # Viterbi log score of the proposal
    assignments: list[Assignment]

    @property
    def anchored(self) -> int:
        return sum(a.source in ("anchored", "locked") for a in self.assignments)


def _pair_transitions(model: RollModel, i: int) -> np.ndarray:
    """Transition matrix for frames i -> i+1, with the same-outing bonus applied if earned."""
    tr = model.transitions
    if (i, i + 1) not in model.same_outing:
        return tr
    ev = np.array([s.event if s.event is not None else -1 for s in model.states])
    same = (ev[:, None] == ev[None, :]) & (ev[:, None] >= 0)
    return np.where(same & np.isfinite(tr), tr + model.params.outing_bonus, tr)


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
    Anchored frames are fixed points, so nothing between two of them may report an interval
    reaching outside them.
    """
    n = len(path)
    raw = []
    for i, j in enumerate(path):
        s = model.states[j]
        lo, hi = (s.t_lo, s.t_hi) if s.kind == "anchor" else _interval(model, post[i])
        if model.bounds:
            b_lo, b_hi = model.bounds[i]
            lo, hi = max(lo, b_lo), min(hi, b_hi)
        raw.append([lo, hi])
    fixed = [model.states[j].t_lo if model.states[j].kind == "anchor" else None for j in path]
    last = None
    for i in range(n):
        if last is not None:
            raw[i][0] = max(raw[i][0], last)
        if fixed[i] is not None:
            last = fixed[i]
    nxt = None
    for i in range(n - 1, -1, -1):
        if nxt is not None:
            raw[i][1] = min(raw[i][1], nxt)
        if fixed[i] is not None:
            nxt = fixed[i]
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
            times.append(min(max(mid, lo), hi))
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
    return times


def solve(model: RollModel) -> Solution:
    path, score = viterbi(model)
    post = forward_backward(model)
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
        assignments.append(
            Assignment(
                frame=i, state=j, source=source, time=times[i], t_lo=lo, t_hi=hi,
                confidence=float(post[i, j]), outside_mass=float(post[i, out_js].sum()),
                anchor_uuid=s.uuid, tzoffset=s.tzoffset, lat=s.lat, lon=s.lon, event=s.event,
            )
        )
    return Solution(path, post, score, assignments)


def null_score(model: RollModel) -> float:
    """Log score of the best path that uses no event or anchor — the wrong-window baseline."""
    try:
        return viterbi(model, kinds={"gap", "outside"})[1]
    except ValueError:
        return float("-inf")
