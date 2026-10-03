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
              the event centroid). Every anchor also gets a *head* and a *tail*: event states
              over the anchor's occasion, ranked directly before and after the anchor and open
              only to the frames before, respectively after, it (`before_frame`/`after_frame`).
              The tail is always there; the head only when the photo is the event's first, since
              otherwise the event state itself serves the frames before. That is how the
              neighbours of an anchored frame stay on its occasion without the chain ever
              stepping down in rank. (An earlier version let an anchor fall back
              into its own event state instead; two anchors in one event in the wrong order
              could then be walked through it, and the written times came out non-monotone on
              the 22-day roll. Two frames on the *same* photo — Lightroom spaces a tagged group
              a second apart — are ordered by frame within the instant, and a frame before the
              event's first photo needs the head to stay on the occasion at all.)
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
from datetime import datetime, timedelta, timezone
from typing import Literal

import numpy as np

from filmgeo.align.evidence import Evidence
from filmgeo.events import Event, haversine_m
from filmgeo.signals.base import SAME_MOMENT, Constraint, Window, frame_bounds

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
    # A weather clue (clear / overcast / rain / snow) contradicting the archive's weather at the
    # event's place and hour (COO-136). Measured OFF: on the verified rolls Claude's clue
    # disagrees with the 10 km reanalysis on 8 of 25 outdoor frames at the frame's *true* time
    # and place (a whole "clear" walk the archive calls overcast), and any penalty moves the
    # 22-day roll's right-day count from 29 to 25 and its interpolated error from 1.5 h to
    # 8.3 h. The machinery stays for measurement (FILMGEO_WEATHER=1, raise this); the evidence
    # says a film frame's sky and an hourly grid cell are not the same thing.
    weather_penalty: float = 0.0
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
    # Same-outing pairs as a *joint day* constraint (COO-147): two consecutive frames Claude put
    # in one outing may not sit on different calendar days. A strong finite penalty rather than
    # -inf, so a user lock that contradicts a group still solves (the group loses, visibly)
    # instead of leaving no path. log-odds of about 400:1 against splitting an outing across days.
    outing_day_penalty: float = 6.0
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
    weather: str | None = None     # clear | overcast | rain | snow | fog, as Claude read it


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
    stay: bool = False             # gap between two bursts of photos at one place: the place is known, the hour is not
    visit: bool = False            # a recorded stop with no photos (a check-in, a timeline visit): place and hour known
    venue: str | None = None       # the visit's venue name, when the source has one
    # gap: where the trail was before and after, and for how long it is silent — how far a
    # place off the trail can be and still be reached inside it. (lat, lon) or None per end.
    reach_from: tuple[float, float] | None = None
    reach_to: tuple[float, float] | None = None
    reach_hours: float = 0.0
    before_frame: int | None = None      # event head: open only to frames before this anchored frame
    after_frame: int | None = None       # event tail: open only to frames after this anchored frame
    anchor_time: datetime | None = None  # head/tail: the anchor's instant, which fixes their rank

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
    evidence: "Evidence | None" = None
    # (frame, state) -> where the frame would be if it sat in that state, by its nearest photos:
    # (state lat, state lon, index into evidence.photos of the photo that says so, vote share,
    # whether that photo belongs to the state's own event). Absent = no visual say.
    choices: dict[tuple[int, int], tuple] = field(default_factory=dict)

    @property
    def outside(self) -> list[int]:
        return [i for i, s in enumerate(self.states) if s.kind == "outside"]

    def same_day(self) -> np.ndarray:
        """(S, S) bool: can a frame in state a and the next frame in state b be on one calendar day?

        A state covers the days from its start to its end (a week-long gap covers seven); two
        states are compatible if their day ranges intersect. Outside states are always
        compatible — they carry their own penalty. Days are read in each state's own zone.
        """
        if getattr(self, "_same_day", None) is None:
            lo = np.array([s.t_lo.date().toordinal() if s.kind != "outside" else -10**9 for s in self.states])
            hi = np.array([s.t_hi.date().toordinal() if s.kind != "outside" else 10**9 for s in self.states])
            self._same_day = (lo[:, None] <= hi[None, :]) & (lo[None, :] <= hi[:, None])
        return self._same_day

    def anchors_for(self, frame: int) -> list[int]:
        return [i for i, s in enumerate(self.states) if s.kind == "anchor" and s.frame == frame]


# ---------------------------------------------------------------------------------------
# States


def _day_pieces(lo: datetime, hi: datetime) -> list[tuple[datetime, datetime]]:
    """[lo, hi) cut at local midnights, in `lo`'s own zone, so each piece lies within one calendar day."""
    out = []
    cur = lo
    while cur < hi:
        nxt = min(hi, (cur + timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0))
        if nxt <= cur:            # a piece that starts exactly at midnight
            nxt = min(hi, cur + timedelta(days=1))
        out.append((cur, nxt))
        cur = nxt
    return out or [(lo, hi)]


STAY_RADIUS_M = 300.0


def build_states(window: Window, events: list[Event], anchors: list[Anchor], stay_radius_m: float = STAY_RADIUS_M,
                 visits: list | None = None) -> list[State]:
    """One state per event, one gap state per calendar day between events, anchors with heads and tails.

    Gaps are cut at midnight so a state never spans two days: that keeps the joint-day
    constraint (COO-147) exact — a week-long gap would otherwise be "the same day" as both
    ends of the week — and gives the posterior, and the timeline's best-days summary, a day's
    resolution inside long silences.
    """
    states: list[State] = []
    prev_end = window.start
    prev_loc: tuple[float, float] | None = None

    def gaps(lo: datetime, hi: datetime, a: tuple[float, float] | None, b: tuple[float, float] | None) -> list[State]:
        # Silent between two bursts of photos at one place: a *stay* — the frame is there, the
        # hour unknown. Either way the gap remembers where the trail was on both sides.
        stay = a is not None and b is not None and haversine_m(a, b) <= stay_radius_m
        lat, lon = ((a[0] + b[0]) / 2, (a[1] + b[1]) / 2) if stay else (None, None)
        hours = (hi - lo).total_seconds() / 3600.0
        return [State("gap", x, y, lat, lon, stay=stay, reach_from=a, reach_to=b, reach_hours=hours) for x, y in _day_pieces(lo, hi)]

    # The timeline's pieces in order: photo events, and recorded visits (align/visits.py) where
    # the photos are silent or say somewhere else. A visit inside an event at another place
    # splits the event round it — a five-minute stop at the beach inside a morning at home.
    pieces: list[tuple[datetime, datetime, Event | None, object | None]] = []
    for e in events:
        lo, hi = max(e.start, window.start), min(e.end, window.end)
        if hi >= lo:
            pieces.append((lo, hi, e, None))
    for v in sorted(visits or [], key=lambda v: v.t_lo):
        lo, hi = max(v.t_lo, window.start), min(v.t_hi, window.end)
        if hi <= lo:
            continue
        clash = [k for k, (a, b, e, w) in enumerate(pieces) if a < hi and lo < b]
        if not clash:
            pieces.append((lo, hi, None, v))
            continue
        if len(clash) > 1 or pieces[clash[0]][3] is not None:
            continue                                  # spans several pieces, or another visit: the photos have it
        a, b, e, _ = pieces[clash[0]]
        if e.lat is not None and haversine_m((e.lat, e.lon), (v.lat, v.lon)) <= stay_radius_m:
            continue                                  # the photos already say this place
        if not (a < lo and hi < b):
            continue
        pieces[clash[0] : clash[0] + 1] = [(a, lo, e, None), (lo, hi, None, v), (hi, b, e, None)]
    pieces.sort(key=lambda x: x[0])

    for lo, hi, e, v in pieces:
        loc = (e.lat, e.lon) if (e is not None and e.lat is not None) else ((v.lat, v.lon) if v is not None else None)
        if lo > prev_end:
            states.extend(gaps(prev_end, lo, prev_loc, loc or prev_loc))
        if e is not None:
            states.append(State("event", lo, hi, e.lat, e.lon, event=e.index))
        else:
            states.append(State("gap", lo, hi, v.lat, v.lon, stay=True, visit=True, venue=v.label,
                                reach_from=loc, reach_to=loc, reach_hours=(hi - lo).total_seconds() / 3600.0))
        prev_end = max(prev_end, hi)
        prev_loc = loc or prev_loc
    if window.end > prev_end:
        states.extend(gaps(prev_end, window.end, prev_loc, None))
    spans = {e.index: (e.start, e.end) for e in events}
    by_index = {e.index: e for e in events}
    headed: set[tuple[int, datetime]] = set()
    for a in sorted(anchors, key=lambda a: (a.time, a.frame)):
        if window.contains(a.time):
            occ_lo, occ_hi = spans.get(a.event, (a.time, a.time))
            half = OCCASION_MIN_SPAN / 2
            occ_lo, occ_hi = min(occ_lo, a.time - half), max(occ_hi, a.time + half)
            occ_lo, occ_hi = max(occ_lo, window.start), min(occ_hi, window.end)
            states.append(State("anchor", a.time, a.time, a.lat, a.lon, event=a.event,
                                frame=a.frame, uuid=a.uuid, tzoffset=a.tzoffset, locked=a.locked,
                                occ_lo=occ_lo, occ_hi=occ_hi))
            e = by_index.get(a.event)
            lat, lon = (e.lat, e.lon) if e is not None else (a.lat, a.lon)
            states.append(State("event", occ_lo, occ_hi, lat, lon, event=a.event, after_frame=a.frame, anchor_time=a.time))
            # A head only where the event state itself cannot serve the frames before the
            # anchor: when the photo is the event's first, the event ranks *above* the anchor.
            # Otherwise the event state already covers them, and a second state over the same
            # occasion would double-count "on the occasion, not at the photo" in the posterior.
            first_photo = e is None or a.time <= e.start
            if first_photo and (a.event, a.time) not in headed:
                headed.add((a.event, a.time))
                states.append(State("event", occ_lo, occ_hi, lat, lon, event=a.event, before_frame=a.frame, anchor_time=a.time))
    # Rank: by time, then anchors on one instant by frame, each followed by its own tail, then
    # the event or gap that starts there. Outside is last.
    states.sort(key=_rank)
    states.append(State("outside", window.start, window.start, side="before"))
    states.append(State("outside", window.end, window.end, side="after"))
    return states


def _rank(s: State) -> tuple:
    if s.kind == "anchor":
        return (s.t_lo, 0, s.frame, 0)
    if s.before_frame is not None:
        return (s.anchor_time, 0, s.before_frame, -1)
    if s.after_frame is not None:
        return (s.anchor_time, 0, s.after_frame, 1)
    return (s.t_lo, 1, 0, 0)


# ---------------------------------------------------------------------------------------
# Emissions


def _event_support(s: State, fe, p, spread_m: float) -> tuple[float, tuple | None]:
    """How well a frame fits an event: its nearest photos' vote for the event's place, and for
    the event's own photos. A visit that holds the similar photos gets the whole vote; another
    visit to the same place gets the place share only."""
    occ, occ_best = fe.in_event(s.event, p.occasion_mode)
    if s.has_location:
        place, place_best = fe.near(s.lat, s.lon, max(p.radius_m, spread_m + 100.0))
    else:
        place, place_best = occ, occ_best                 # an event with no GPS: only its own photos speak for it
    support = place * (p.other_visit + (1 - p.other_visit) * occ)
    best = occ_best if occ_best is not None else place_best
    if best is None:
        return 0.0, None
    return support, (s.lat, s.lon, best, place, occ_best is not None)


def _reachable(s: State, lat: float, lon: float, p) -> bool:
    """Can a place off the trail be visited inside this gap, given where the trail was on either side?"""
    budget = p.speed_kmh * 1000.0 * s.reach_hours + p.reach_slack_m
    a, b = s.reach_from, s.reach_to
    if a is None and b is None:
        return True
    if a is None or b is None:
        return haversine_m(a or b, (lat, lon)) <= budget
    return haversine_m(a, (lat, lon)) + haversine_m((lat, lon), b) <= budget + haversine_m(a, b)


def _gap_support(s: State, fe, p) -> tuple[float, tuple | None]:
    """A gap holds no photos, so nothing speaks for the occasion; what speaks is the place.
    Between two bursts at one place that place is offered (a stay); any gap also offers the
    frame's own best place if it can be reached from where the trail was."""
    best_support, best_choice = 0.0, None
    if s.stay:
        place, photo = fe.near(s.lat, s.lon, p.radius_m)
        if photo is not None:
            # A recorded stop is worth more than a silent stay: the phone *was* there then.
            best_support = place * (p.visit_weight if s.visit else p.other_visit * p.stay_discount)
            best_choice = (s.lat, s.lon, photo, place, False)
    for h in fe.places:
        support = h.mass * p.other_visit * p.offtrail_discount
        if support <= best_support:
            break                                           # hypotheses are sorted by mass
        if _reachable(s, h.lat, h.lon, p):
            best_support, best_choice = support, (h.lat, h.lon, h.best, h.mass, False)
            break
    return best_support, best_choice



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
    event_weather: dict[int, str] | None = None,     # observed weather class per event (signals/weather.py)
    evidence: Evidence | None = None,                # nearest photos and place votes per frame (align/evidence.py)
    choices: dict | None = None,                     # filled: (frame, state) -> place the state offers the frame
) -> tuple[np.ndarray, set[int]]:
    from filmgeo.signals.weather import contradicts, normalise_clue

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
    spread = {e.index: e.spread_m for e in events}
    for j, s in enumerate(states):
        if s.kind == "gap":
            em[:, j] = log_gap
            if evidence is not None:
                for i in range(n_frames):
                    fe = evidence.frames[i]
                    support, choice = _gap_support(s, fe, evidence.params)
                    if choice is not None and choices is not None:
                        choices[(i, j)] = choice
                    em[i, j] = math.log(max(evidence.params.epsilon, (1 - fe.q) * params.gap_prob + fe.q * support))
        elif s.kind == "outside":
            em[:, j] = log_out
        elif s.kind == "event":
            for i in range(n_frames):
                if evidence is not None:
                    fe = evidence.frames[i]
                    support, choice = _event_support(s, fe, evidence.params, spread.get(s.event, 0.0))
                    if choice is not None and choices is not None:
                        choices[(i, j)] = choice
                    # A frame that resembles nothing (q -> 0) is as likely in any event as the
                    # first engine's floor said; one that resembles its photos is where they are.
                    p = max(evidence.params.epsilon, (1 - fe.q) * params.event_floor + fe.q * support)
                else:
                    p = max(params.event_floor, best.get((i, s.event), 0.0))
                v = math.log(p)
                if clues and not _clue_consistent(clues[i], hours.get(s.event)):
                    v -= params.clue_penalty
                if clues and event_weather and clues[i] is not None and s.event in event_weather \
                        and contradicts(normalise_clue(clues[i].weather), event_weather[s.event]):
                    v -= params.weather_penalty
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
    # An anchor's head is for the frames before it only, its tail for the frames after.
    for j, s in enumerate(states):
        if s.before_frame is not None:
            em[s.before_frame:, j] = NEG
        if s.after_frame is not None:
            em[: s.after_frame + 1, j] = NEG

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
                far = [j for j, s in enumerate(states)
                       if s.has_location and haversine_m((c.lat, c.lon), (s.lat, s.lon)) > radius]
                # The user's time and place both stand. A five-minute stop at the beach inside a
                # morning of phone photos at home falls in an event whose centroid is home; if the
                # place would leave the frame no state at all, the place yields here (geo.place
                # still puts the frame at the user's pin) rather than the solve failing.
                keep = em[i].copy()
                em[i, far] = NEG
                if not np.isfinite(em[i]).any():
                    em[i] = keep
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
            # transition cannot carry the time variable that would forbid it. Rank can, with no
            # exceptions: a frame after an anchored one that stays in the anchor's event uses
            # the anchor's tail state, which ranks just above it.
            if b < a:
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


def anchored_days(anchors: list[Anchor], constraints: list[Constraint]) -> list[Constraint]:
    """"Same day as frame N" where N is anchored: N's day, as a constraint, for `frame_bounds` to spread.

    `frame_bounds` propagates a shared day only from a *dated* frame, and an anchored frame is
    dated by a verdict rather than a fact. Saying "frame 4 is the same day as frame 3" when
    frame 3 shows 4 April is the most natural fact of all (review feedback), so every anchored
    frame that takes part in a same-day pair contributes its local calendar day here. The day
    is read in the photo's own offset, so a UTC-dated camera still yields the right day.
    """
    partners: set[int] = set()
    for c in constraints:
        if c.scope == "frame" and c.frame and c.same_day_as:
            partners.update((c.frame, c.same_day_as))
    out: list[Constraint] = []
    for a in anchors:
        n = a.frame + 1
        if n not in partners:
            continue
        t = a.time if a.tzoffset is None else a.time.astimezone(timezone(timedelta(seconds=a.tzoffset)))
        day = t.replace(hour=0, minute=0, second=0, microsecond=0)
        out.append(Constraint("frame", "anchor", frame=n, t_lo=day, t_hi=day + timedelta(days=1), note="anchored day"))
    return out


def anchored_moments(anchors: list[Anchor], constraints: list[Constraint]) -> list[Constraint]:
    """"Same moment as frame N" where N is anchored: N's instant, stretched by SAME_MOMENT on the side
    scan order allows, as a constraint on the *other* frame.

    The anchored frame itself is left alone — it reports its occasion (COO-145) and a one-second
    bound on it would collapse that. The partner gets [t, t + SAME_MOMENT) when it comes later
    in the roll and (t - SAME_MOMENT, t] when earlier; `frame_bounds` carries it on down a chain.
    """
    by_frame = {a.frame + 1: a for a in anchors}
    out: list[Constraint] = []
    for c in constraints:
        if c.scope != "frame" or not c.frame or not c.same_time_as:
            continue
        for me, partner in ((c.frame, c.same_time_as), (c.same_time_as, c.frame)):
            a = by_frame.get(partner)
            if a is None or me in by_frame:
                continue
            lo, hi = (a.time, a.time + SAME_MOMENT) if me > partner else (a.time - SAME_MOMENT, a.time + timedelta(seconds=1))
            out.append(Constraint("frame", "anchor", frame=me, t_lo=lo, t_hi=hi, note=f"moments {'after' if me > partner else 'before'} frame {partner}"))
    return out


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
    event_weather: dict[int, str] | None = None,
    evidence: Evidence | None = None,
    visits: list | None = None,
) -> RollModel:
    params = params or AlignParams()
    anchors = [a for a in (anchors or []) if a.locked or a.confidence >= params.min_anchor_confidence]
    constraints = list(constraints or []) + anchored_days(anchors, constraints or []) + anchored_moments(anchors, constraints or [])
    states = build_states(window, events, anchors, visits=visits)
    choices: dict = {}
    em, skipped = build_emissions(states, n_frames, params, anchors, events, sims, event_ids, clues, constraints, window, event_weather,
                                  evidence, choices)
    tr = build_transitions(states, params)
    bounds = frame_bounds(constraints or [], n_frames, window)
    return RollModel(n_frames, states, em, tr, params, window, set(same_outing or ()), skipped, bounds, evidence, choices)
