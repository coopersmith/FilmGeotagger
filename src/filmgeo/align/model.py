"""HMM states and emissions for one roll (COO-114).

A roll is an ordered, undated sequence of frames; the phone timeline is a dated sequence of
events. The alignment asks, for every frame, which piece of the timeline it sits in. Those
pieces are the hidden states:

* `anchor`  — A(i,c): frame i is exactly at verified candidate c's instant. Exists only for a
              verified match (Claude confidence >= `min_anchor_confidence`) or a user pick, and
              only frame i may occupy it.
* `event`   — E(e): the frame is inside phone-photo event e (interval = event span, location =
              the event centroid).
* `gap`     — G(k): the frame is between two events. Interval known, location unknown unless a
              trail point says otherwise (that is geo.py's job, COO-116).
* `outside` — X: the frame is not in the window at all. One state, reachable from anywhere,
              carrying a large penalty; its posterior mass is the wrong-window signal (COO-118).

States are sorted by time; the solver's transitions then only allow a frame to sit no earlier
than the frame before it. Emissions are log-probabilities. There is no fitted calibration yet
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
    # Logistic from cosine similarity to P(same moment). SigLIP similarities for true matches
    # in M1 clustered around 0.6-0.8 and non-matches below ~0.5; centre and slope are set so
    # 0.55 -> 0.5 and 0.7 -> 0.9. Replace with a fitted curve when COO-140 lands.
    sim_centre: float = 0.55
    sim_slope: float = 15.0
    # Probability floor for sitting in an event that holds no similar photo. The user shoots
    # "often, not always" next to the phone, so an event with nothing similar is still likely.
    event_floor: float = 0.05
    # Flat likelihoods for the evidence-free states.
    gap_prob: float = 0.02
    outside_prob: float = 0.005
    # Added to log(confidence) for an anchor so a verified match beats the same event's
    # similarity-only emission. exp(1) ~ 2.7x.
    anchor_bonus: float = 1.0
    min_anchor_confidence: float = 0.5
    # A clue (night / midday...) contradicting an event's local hours.
    clue_penalty: float = 1.5
    # Transitions. Time jumps are penalised sublinearly (log1p of hours) so a roll can sit in a
    # camera for weeks while consecutive frames still prefer to stay close: 1 h costs 0.35,
    # a day 1.6, a month 3.3.
    jump_weight: float = 0.5
    state_change: float = 0.2
    event_change: float = 0.3
    outside_switch: float = 2.0
    outing_bonus: float = 1.0
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
        return "X"


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
    def outside(self) -> int:
        return next(i for i, s in enumerate(self.states) if s.kind == "outside")

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
    for a in anchors:
        if window.contains(a.time):
            states.append(State("anchor", a.time, a.time, a.lat, a.lon, event=a.event,
                                frame=a.frame, uuid=a.uuid, tzoffset=a.tzoffset, locked=a.locked))
    # Sort by time; anchors before the event that contains them is fine either way because
    # transitions test intervals, not ranks. Outside is last.
    states.sort(key=lambda s: (s.t_lo, s.t_hi))
    states.append(State("outside", window.start, window.end))
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
        elif s.kind == "anchor":
            a = next(x for x in anchors if x.frame == s.frame and x.uuid == s.uuid and x.time == s.t_lo)
            conf = max(a.confidence, 1e-6)
            em[s.frame, j] = math.log(conf) + params.anchor_bonus + math.log(
                max(params.event_floor, params.calibrate(a.similarity)) if a.similarity else 1.0
            )

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
            if s.kind == "outside" and t.kind == "outside":
                tr[a, b] = 0.0
                continue
            if s.kind == "outside" or t.kind == "outside":
                tr[a, b] = -params.outside_switch
                continue
            if t.t_hi < s.t_lo:
                continue                                   # would move backwards in time
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
