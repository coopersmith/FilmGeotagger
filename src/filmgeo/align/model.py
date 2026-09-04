"""HMM states and emissions for one roll (COO-114).

A roll is an ordered, undated sequence of frames; the phone timeline is a dated sequence of
events. The alignment asks, for every frame, which piece of the timeline it sits in. Those
pieces are the hidden states:

* `anchor`  — A(i,c): frame i is at verified candidate c's instant. Exists only for a
              verified match (Claude confidence >= `min_anchor_confidence`) or a user pick, and
              only frame i may occupy it. The state's *time* is c's second — that is what gets
              written — but its *occasion* (`occ_lo..occ_hi`, the event c belongs to, at least
              an hour wide) is what the verdict vouches for: Claude answers "same occasion",
              and when right it picks a same-session photo minutes off (COO-120). The reported
              interval is the occasion, not the instant.
* `event`   — E(e): the frame is inside phone-photo event e (interval = event span, location =
              the event centroid).
* `gap`     — G(k): the frame is between two events. Interval known, location unknown unless a
              trail point says otherwise (that is geo.py's job, COO-116).
* `outside` — X: the frame is not in the window at all. Two states, `before` and `after`,
              so a path can start outside and enter, or leave and stay out, but never leave
              and re-enter earlier in time (that would let the monotone chain be bypassed).
              Their posterior mass is the wrong-window signal (COO-118).

States are sorted by time and transitions only move to an equal or higher rank, so a frame
never sits earlier than the frame before it. Emissions are log-probabilities. There is no fitted calibration yet
(M1 left it open; COO-140 refits from confirmations), so `AlignParams` holds a hand-set
logistic on SigLIP similarity and hand-set floors. The numbers are documented at each field;
the *structure* — anchors beat events beat gaps beat outside, and a fact zeros out everything
it excludes — is what the tests pin down.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Literal

import numpy as np

from filmgeo.events import Event, haversine_m
from filmgeo.signals.base import Constraint, Window, frame_bounds

Kind = Literal["anchor", "event", "gap", "outside"]
NEG = -np.inf

# An anchored frame's occasion is at least this wide: Claude's question is "same occasion,
# within an hour or so", and on real anchors its picks sit 2-6 minutes from the truth.
OCCASION_MIN_SPAN = timedelta(hours=1)

# Buckets a `time_of_day` clue maps to, in local hours. Overlap with an event's local hours is
# consistency; none is a penalty. Deliberately generous: a clue is read off film, not a clock.
TIME_OF_DAY_HOURS = {
    "dawn": range(4, 8),
    "morning": range(6, 12),
    "midday": range(10, 15),
    "afternoon": range(12, 19),
    "dusk": range(17, 22),
    "night": [*range(19, 24), *range(0, 6)],
}


@dataclass
class AlignParams:
    # Logistic from cosine similarity to P(same moment). Measured on the 113 hand-anchored
    # frames (docs/m2-findings.md): similarity to the true photo has median 0.948, to the best
    # photo of any *other* event 0.877, and the pool median is 0.70 — so the whole informative
    # range is 0.85-0.99, and a centre anywhere lower saturates every event to 1. A grid-fit
    # logistic of true-vs-best-other gives centre 0.88, slope 10. Refit from confirmations in
    # COO-140; this is the crude version.
    sim_centre: float = 0.88
    sim_slope: float = 10.0
    # Probability floor for sitting in an event that holds no similar photo. The user shoots
    # "often, not always" next to the phone, so an event with nothing similar is still likely.
    event_floor: float = 0.05
    # Flat likelihoods for the evidence-free states.
    gap_prob: float = 0.02
    outside_prob: float = 0.005
    # Added to log(confidence) for an anchor so the exact instant is preferred over "somewhere
    # in the same event" — a small nudge, not evidence. See build_emissions for how a verdict
    # reshapes the whole row.
    anchor_bonus: float = 0.5
    min_anchor_confidence: float = 0.5
    # A clue (night / midday...) contradicting an event's local hours.
    clue_penalty: float = 1.5
    # Transitions. Time jumps are penalised sublinearly (log1p of hours) so a roll can sit in a
    # camera for weeks while consecutive frames still prefer to stay close: 1 h costs 0.24,
    # a day 1.1, a week 1.8, a month 2.3. For scale, a verdict at 0.9 confidence is worth
    # log(0.9/0.1) = 2.2 over the alternatives, so one anchor outweighs a week's jump but not
    # by much — which is why the reverse-roll test counts anchors rather than score.
    jump_weight: float = 0.35
    state_change: float = 0.2
    event_change: float = 0.3
    outside_switch: float = 2.0
    # Same-outing transition bonus (COO-119). Measured off: on the two verified rolls the bonus
    # changed no anchor and no interval, and moved the 22-day roll's interpolated frames from a
    # median 1.7 h off the truth to 14.8 h — the groups on a newborn-at-home roll are "who is
    # holding the baby", which says nothing about which day. The pass is kept for its
    # descriptions and out-of-sequence flags; using groups as joint day constraints is COO-147.
    outing_bonus: float = 0.0
    # Frame place facts: how far a state's location may be from the stated place.
    place_radius_m: float = 2000.0

    def calibrate(self, sim: float) -> float:
        return 1.0 / (1.0 + math.exp(-self.sim_slope * (sim - self.sim_centre)))


@dataclass
class Anchor:
    """A verified (or user-picked) match of one frame to one phone photo."""

    frame: int                 # 0-based frame index
    uuid: str
    time: datetime
    event: int
    confidence: float          # Claude's, or 1.0 for a user pick
    similarity: float = 0.0
    lat: float | None = None
    lon: float | None = None
    tzoffset: int | None = None
    locked: bool = False       # user-picked: prune every other state for this frame


@dataclass
class FrameClues:
    """The subset of `verify.claude.Clues` the emissions use. All optional."""

    time_of_day: str | None = None
    indoor: bool | None = None


@dataclass
class State:
    kind: Kind
    t_lo: datetime
    t_hi: datetime
    lat: float | None = None
    lon: float | None = None
    event: int | None = None
    frame: int | None = None       # anchor: the frame it belongs to
    uuid: str | None = None        # anchor: the photo
    tzoffset: int | None = None
    locked: bool = False
    side: str | None = None        # outside: "before" | "after"
    occ_lo: datetime | None = None # anchor: the occasion's span — what the verdict actually vouches for
    occ_hi: datetime | None = None

    @property
    def has_location(self) -> bool:
        return self.lat is not None and self.lon is not None

    def overlaps(self, lo: datetime, hi: datetime) -> bool:
        """Does this state intersect the half-open range [lo, hi)?"""
        if hi <= lo:
            return False
        if self.t_lo == self.t_hi:
            return lo <= self.t_lo < hi
        return self.t_lo < hi and lo <= self.t_hi

    def label(self) -> str:
        if self.kind == "anchor":
            return f"A(frame {self.frame + 1} @ {self.t_lo:%Y-%m-%d %H:%M})"
        if self.kind == "event":
            return f"E{self.event} {self.t_lo:%m-%d %H:%M}..{self.t_hi:%H:%M}"
        if self.kind == "gap":
            return f"G {self.t_lo:%m-%d %H:%M}..{self.t_hi:%m-%d %H:%M}"
        return f"X-{self.side}"


@dataclass
class RollModel:
    n_frames: int
    states: list[State]
    emissions: np.ndarray                 # (n_frames, S) log domain, -inf = excluded
    transitions: np.ndarray               # (S, S) log domain, frame-independent
    params: AlignParams
    window: Window
    same_outing: set[tuple[int, int]] = field(default_factory=set)   # consecutive frame pairs
    skipped: set[int] = field(default_factory=set)
    bounds: list[tuple[datetime, datetime]] = field(default_factory=list)   # per-frame, from facts

    @property
    def outside(self) -> list[int]:
        return [i for i, s in enumerate(self.states) if s.kind == "outside"]

    def anchors_for(self, frame: int) -> list[int]:
        return [i for i, s in enumerate(self.states) if s.kind == "anchor" and s.frame == frame]


# ---------------------------------------------------------------------------------------
# States


def build_states(window: Window, events: list[Event], anchors: list[Anchor]) -> list[State]:
    states: list[State] = []
    prev_end = window.start
    for e in events:
        lo, hi = max(e.start, window.start), min(e.end, window.end)
        if hi < lo:
            continue
        if lo > prev_end:
            states.append(State("gap", prev_end, lo))
        states.append(State("event", lo, hi, e.lat, e.lon, event=e.index))
        prev_end = hi
    if window.end > prev_end:
        states.append(State("gap", prev_end, window.end))
    spans = {e.index: (e.start, e.end) for e in events}
    for a in anchors:
        if window.contains(a.time):
            occ_lo, occ_hi = spans.get(a.event, (a.time, a.time))
            half = OCCASION_MIN_SPAN / 2
            occ_lo, occ_hi = min(occ_lo, a.time - half), max(occ_hi, a.time + half)
            occ_lo, occ_hi = max(occ_lo, window.start), min(occ_hi, window.end)
            states.append(State("anchor", a.time, a.time, a.lat, a.lon, event=a.event,
                                frame=a.frame, uuid=a.uuid, tzoffset=a.tzoffset, locked=a.locked,
                                occ_lo=occ_lo, occ_hi=occ_hi))
    # Sort by time; anchors before the event that contains them is fine either way because
    # transitions test intervals, not ranks. Outside is last.
    states.sort(key=lambda s: (s.t_lo, s.t_hi))
    states.append(State("outside", window.start, window.start, side="before"))
    states.append(State("outside", window.end, window.end, side="after"))
    return states


# ---------------------------------------------------------------------------------------
# Emissions


def _event_hours(events: list[Event]) -> dict[int, set[int]]:
    """Local hours an event spans. Event times are tz-aware in the photos' own zone."""
    hours: dict[int, set[int]] = {}
    for e in events:
        if e.end - e.start >= timedelta(hours=23):
            hours[e.index] = set(range(24))
            continue
        h, span = set(), e.start
        while span <= e.end:
            h.add(span.hour)
            span += timedelta(hours=1)
        h.add(e.end.hour)
        hours[e.index] = h
    return hours


def _clue_consistent(clues: FrameClues | None, hours: set[int] | None) -> bool:
    if clues is None or clues.time_of_day is None or not hours:
        return True
    wanted = TIME_OF_DAY_HOURS.get(clues.time_of_day.lower())
    return wanted is None or bool(set(wanted) & hours)


def build_emissions(
    states: list[State],
    n_frames: int,
    params: AlignParams,
    anchors: list[Anchor],
    events: list[Event],
    sims: np.ndarray | None = None,          # (n_frames, len(pool)) cosine similarities
    event_ids: list[int] | None = None,      # event id per pool asset
    clues: list[FrameClues | None] | None = None,
    constraints: list[Constraint] | None = None,
    window: Window | None = None,
) -> tuple[np.ndarray, set[int]]:
    S = len(states)
    em = np.full((n_frames, S), NEG)
    kinds = [s.kind for s in states]

    # Best calibrated similarity per (frame, event).
    best: dict[tuple[int, int], float] = {}
    if sims is not None and event_ids is not None:
        ev_arr = np.asarray(event_ids)
        for e in np.unique(ev_arr):
            cols = np.where(ev_arr == e)[0]
            top = sims[:, cols].max(axis=1)
            for i in range(n_frames):
                best[(i, int(e))] = params.calibrate(float(top[i]))
    hours = _event_hours(events)

    log_gap, log_out = math.log(params.gap_prob), math.log(params.outside_prob)
    for j, s in enumerate(states):
        if s.kind == "gap":
            em[:, j] = log_gap
        elif s.kind == "outside":
            em[:, j] = log_out
        elif s.kind == "event":
            for i in range(n_frames):
                p = max(params.event_floor, best.get((i, s.event), 0.0))
                v = math.log(p)
                if clues and not _clue_consistent(clues[i], hours.get(s.event)):
                    v -= params.clue_penalty
                em[i, j] = v

    # A verdict "candidate c, confidence q" says: with probability q the frame is on c's
    # occasion; with 1-q it is anywhere else. So c's event takes at least q, the anchor state
    # (c's exact instant) takes q plus a small bonus for being exact, and every other state
    # for that frame is scaled by 1-q. Similarity is not added to the anchor: Claude saw the
    # image, and similarity is already in the event floor it competes with.
    by_frame: dict[int, list[Anchor]] = {}
    for a in anchors:
        by_frame.setdefault(a.frame, []).append(a)
    for i, frame_anchors in by_frame.items():
        q_max = max(min(a.confidence, 1 - 1e-6) for a in frame_anchors)
        keep_events = {a.event for a in frame_anchors}
        scale = math.log(1 - q_max)
        for j, s in enumerate(states):
            if s.kind == "anchor":
                continue
            if s.kind == "event" and s.event in keep_events:
                q = max(min(a.confidence, 1 - 1e-6) for a in frame_anchors if a.event == s.event)
                em[i, j] = max(em[i, j], math.log(q))
            elif np.isfinite(em[i, j]):
                em[i, j] += scale
    for j, s in enumerate(states):
        if s.kind == "anchor":
            a = next(x for x in anchors if x.frame == s.frame and x.uuid == s.uuid and x.time == s.t_lo)
            em[s.frame, j] = math.log(max(a.confidence, 1e-6)) + params.anchor_bonus

    # Locked anchors prune every other state for their frame.
    for j, s in enumerate(states):
        if s.kind == "anchor" and s.locked:
            keep = em[s.frame, j]
            em[s.frame, :] = NEG
            em[s.frame, j] = keep

    # Constraints zero out what they exclude.
    skipped: set[int] = set()
    if constraints and window is not None:
        bounds = frame_bounds(constraints, n_frames, window)
        has_fact = {c.frame - 1 for c in constraints if c.scope == "frame" and c.frame and c.has_time}
        for i, (lo, hi) in enumerate(bounds):
            for j, s in enumerate(states):
                if s.kind == "outside":
                    if i in has_fact:
                        em[i, j] = NEG          # a frame with a date cannot be outside the window
                    continue
                if not s.overlaps(lo, hi):
                    em[i, j] = NEG
        for c in constraints:
            if c.scope != "frame" or not c.frame or not (1 <= c.frame <= n_frames):
                continue
            i = c.frame - 1
            if c.skip:
                skipped.add(i)
            if c.has_place:
                radius = c.radius_m or params.place_radius_m
                for j, s in enumerate(states):
                    if s.has_location and haversine_m((c.lat, c.lon), (s.lat, s.lon)) > radius:
                        em[i, j] = NEG
    # A skipped frame carries no evidence: uniform over whatever is still allowed.
    for i in skipped:
        allowed = np.isfinite(em[i])
        em[i, allowed] = 0.0
    dead = [i + 1 for i in range(n_frames) if not np.isfinite(em[i]).any()]
    if dead:
        raise ValueError(f"constraints leave no possible state for frame(s) {dead}")
    return em, skipped


# ---------------------------------------------------------------------------------------
# Transitions


def build_transitions(states: list[State], params: AlignParams) -> np.ndarray:
    S = len(states)
    tr = np.full((S, S), NEG)
    for a, s in enumerate(states):
        for b, t in enumerate(states):
            if s.kind == "outside" or t.kind == "outside":
                # before* -> in-window* -> after*: never out and back in.
                if s.kind == "outside" and t.kind == "outside":
                    if s.side == t.side:
                        tr[a, b] = 0.0
                    elif s.side == "before":
                        tr[a, b] = -params.outside_switch
                elif s.kind == "outside" and s.side == "before":
                    tr[a, b] = -params.outside_switch
                elif t.kind == "outside" and t.side == "after":
                    tr[a, b] = -params.outside_switch
                continue
            # Monotone order on state *rank*, not on intervals. Two touching intervals are
            # pairwise compatible at their shared instant, but a chain of such pairs can walk
            # backwards through a whole week (event -> gap -> earlier event), and a first-order
            # transition cannot carry the time variable that would forbid it. Rank can. The one
            # exception: a frame after an anchored one may sit later in the anchor's own event,
            # whose state ranks below the anchor because it starts earlier.
            if b < a and not (s.kind == "anchor" and t.kind == "event" and t.event == s.event):
                continue
            v = 0.0
            if a != b:
                v -= params.state_change
                jump_h = max(0.0, (t.t_lo - s.t_hi).total_seconds()) / 3600.0
                v -= params.jump_weight * math.log1p(jump_h)
                if s.event is not None and t.event is not None and s.event != t.event:
                    v -= params.event_change
            tr[a, b] = v
    return tr


# ---------------------------------------------------------------------------------------


def build_model(
    window: Window,
    events: list[Event],
    n_frames: int,
    anchors: list[Anchor] | None = None,
    sims: np.ndarray | None = None,
    event_ids: list[int] | None = None,
    clues: list[FrameClues | None] | None = None,
    constraints: list[Constraint] | None = None,
    same_outing: set[tuple[int, int]] | None = None,
    params: AlignParams | None = None,
) -> RollModel:
    params = params or AlignParams()
    anchors = [a for a in (anchors or []) if a.locked or a.confidence >= params.min_anchor_confidence]
    states = build_states(window, events, anchors)
    em, skipped = build_emissions(states, n_frames, params, anchors, events, sims, event_ids, clues, constraints, window)
    tr = build_transitions(states, params)
    bounds = frame_bounds(constraints or [], n_frames, window)
    return RollModel(n_frames, states, em, tr, params, window, set(same_outing or ()), skipped, bounds)
