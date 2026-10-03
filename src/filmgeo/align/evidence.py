"""What a frame looks like it is a picture *of*: the camera roll as a visual index of places (COO-177).

The first engine asked of every frame "which phone photo shows the same occasion?", and left a
frame alone when none did. Measured on the rolls the user reviewed (docs/v2-findings.md), that
is the wrong first question. A frame shot at a house the phone photographed 669 times in a
week has no single occasion to match — the right afternoon ranks 8th to 38th and a careful
verifier says "no match" — but its most similar photo is at that house all the same. And a
frame shot somewhere the phone stayed in the pocket this month is usually somewhere it came
out in another year.

So the question here is *where*, asked of the nearest photos in two sets: the window's pool
(the diary: what was photographed, when) and the atlas (every other place in the library the
user could have reached). Each frame gets its K nearest photos with soft-max weights; the
weight that falls within a few hundred metres of a spot is the evidence the frame was taken
there, and the weight that falls inside one event of the window is the evidence for that
occasion. How much the whole vote is worth depends on how similar the best photo is at all:
below about 0.78 cosine the nearest photo is a look-alike, not the place.

Nothing here knows about order or time. `align.model` turns this into emissions; the monotone
solve then decides which visit to a place a frame belongs to.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

import numpy as np

from filmgeo.photos.library import Asset

EARTH_M = 6371000.0


def haversine_many(lat: float, lon: float, lats: np.ndarray, lons: np.ndarray) -> np.ndarray:
    p1, p2 = math.radians(lat), np.radians(lats)
    dp, dl = p2 - p1, np.radians(lons - lon)
    h = np.sin(dp / 2) ** 2 + math.cos(p1) * np.cos(p2) * np.sin(dl / 2) ** 2
    return 2 * EARTH_M * np.arcsin(np.sqrt(h))


@dataclass
class EvidenceParams:
    k: int = 30                    # nearest photos that vote
    tau: float = 0.03              # soft-max temperature on cosine similarity
    radius_m: float = 300.0        # photos this close to a spot vote for it
    # P(the vote's place is right) from the best photo's similarity: on the reviewed rolls the
    # winning place was within 300 m for 54/54 frames whose best photo scored >= 0.85, 11/13 in
    # 0.80-0.85 and 5/14 below 0.80.
    q_centre: float = 0.775
    q_slope: float = 35.0
    # Which visit to a place. Every state at the place is supported by the place's vote: the
    # visit whose own photo looks most like the frame in full, the others by `other_visit` of
    # it, graded between by a soft-max at the vote's own temperature. Two cleverer forms were
    # built and measured against this one (docs/v2-findings.md) — dealing the vote out among
    # the visits, and scaling the matching visit by the number it competes with times its
    # measured reliability — and neither beat it on place, day or occasion; both let a run of
    # look-alike days out-vote verified anchors. What this form cannot give is a posterior
    # that means "which visit": forty other visits at 0.15 outweigh the one that matches. So
    # the occasion confidence of an unverified frame is not read off the posterior but
    # computed from what was measured (`occasion_confidence`): the best-looking visit is the
    # true one for about 9 frames in 10 when its photo is above 0.84 cosine, 1 in 2 below, and
    # for frames at a much-visited house none of six — hence the low end at 0.3.
    other_visit: float = 0.15
    alpha_lo: float = 0.3
    alpha_hi: float = 0.9
    alpha_centre: float = 0.84
    alpha_slope: float = 50.0
    tau_occasion: float = 0.015    # soft-max over a place's visits for the confidence: at this temperature the share matches how often the best visit is right
    stay_discount: float = 0.6     # a silent stay at the place against a visit that was photographed or recorded
    offtrail: float = 0.5          # what an atlas photo's vote keeps for a place with no visit in the window (a name read off the frame keeps all)
    min_mass: float = 0.02         # hypotheses with less of the vote than this are ignored
    speed_kmh: float = 60.0        # how far from the trail a gap can reach
    reach_slack_m: float = 2000.0
    atlas_reach_m: float = 25_000.0    # atlas photos farther than this from every window photo are not candidates


@dataclass
class Hypothesis:
    """One place a frame may have been taken, as its nearest photos see it."""

    lat: float
    lon: float
    mass: float                    # share of the frame's vote within `radius_m`
    best: int                      # index into `Evidence.photos` of the most similar member
    in_window: bool                # some member is a photo of the window (the place is on the trail)
    photo_mass: float = 0.0        # the part of `mass` cast by photos of the window: these can tell visits apart
    read_mass: float = 0.0         # the part cast by a name read off the frame: no look-alike risk, and no visit preferred


@dataclass
class FrameEvidence:
    idx: np.ndarray                # (K,) into Evidence.photos, most similar first
    w: np.ndarray                  # (K,) weights, sum 1
    sims: np.ndarray               # (K,)
    lat: np.ndarray                # (K,) nan where the photo has no GPS
    lon: np.ndarray
    event: np.ndarray              # (K,) event id in the window, -1 for atlas photos
    q: float                       # how much the vote is worth at all
    places: list[Hypothesis] = field(default_factory=list)
    event_best: np.ndarray | None = None       # (n_events,) the best similarity to a photo of each event
    event_best_idx: np.ndarray | None = None   # (n_events,) and which photo that is

    def near(self, lat: float, lon: float, radius_m: float) -> tuple[float, int | None]:
        """Vote share within `radius_m` of a spot, and the most similar photo there."""
        ok = ~np.isnan(self.lat)
        d = np.full(len(self.idx), np.inf)
        d[ok] = haversine_many(lat, lon, self.lat[ok], self.lon[ok])
        m = d <= radius_m
        if not m.any():
            return 0.0, None
        return float(self.w[m].sum()), int(self.idx[np.argmax(m)])      # idx is similarity-sorted: first hit is best


@dataclass
class Evidence:
    photos: list[Asset]            # the window's pool first, then the atlas
    n_pool: int
    frames: list[FrameEvidence]
    params: EvidenceParams

    @property
    def n_atlas(self) -> int:
        return len(self.photos) - self.n_pool


def alpha_of(best_sim: float, p: EvidenceParams) -> float:
    """P(the visit whose photo looks most like the frame is the true visit), from how similar that photo is."""
    return p.alpha_lo + (p.alpha_hi - p.alpha_lo) / (1.0 + math.exp(-p.alpha_slope * (best_sim - p.alpha_centre)))


def quality(top_sim: float, p: EvidenceParams) -> float:
    return 1.0 / (1.0 + math.exp(-p.q_slope * (top_sim - p.q_centre)))


def _hypotheses(fe: FrameEvidence, n_pool: int, radius_m: float, n_real: int | None = None) -> list[Hypothesis]:
    """Group the voters by place, heaviest first. `n_real` is where the photos end and the readings begin."""
    out: list[Hypothesis] = []
    taken = np.isnan(fe.lat)
    for k in range(len(fe.idx)):
        if taken[k]:
            continue
        d = np.full(len(fe.idx), np.inf)
        free = ~taken
        d[free] = haversine_many(fe.lat[k], fe.lon[k], fe.lat[free], fe.lon[free])
        m = d <= radius_m
        inwin = m & (fe.event >= 0)
        read = m & np.array([i >= n_real for i in fe.idx]) if n_real is not None else np.zeros(len(m), dtype=bool)
        out.append(Hypothesis(float(fe.lat[k]), float(fe.lon[k]), float(fe.w[m].sum()), int(fe.idx[k]), bool(inwin.any()),
                              float(fe.w[inwin].sum()), float(fe.w[read].sum())))
        taken |= m
    out.sort(key=lambda h: -h.mass)
    return out


def reachable_atlas(pool: list[Asset], atlas: list[Asset], reach_m: float) -> list[Asset]:
    """Atlas photos within `reach_m` of some located photo of the window: a coarse grid test."""
    cell = reach_m
    def key(lat: float, lon: float) -> tuple[int, int]:
        return (int(lat // (cell / 111320.0)), int(lon // (cell / (111320.0 * max(0.2, math.cos(math.radians(lat)))))))
    near: set[tuple[int, int]] = set()
    for a in pool:
        if a.lat is not None:
            i, j = key(a.lat, a.lon)
            near.update((i + di, j + dj) for di in (-1, 0, 1) for dj in (-1, 0, 1))
    return [a for a in atlas if a.lat is not None and key(a.lat, a.lon) in near]


READING_PREFIX = "reading:"


def add_readings(ev: Evidence, readings: list) -> Evidence:
    """A copy of the evidence with places read off the frames (`align.readings`) folded into the vote.

    A reading enters its frame's list as one more voter with the reading's confidence as its
    weight — the photos keep the rest — standing at the named place, belonging to no event.
    The frame's vote is then worth at least that confidence: a sign is evidence even when not
    one photo resembles the frame. The input is left untouched, so a re-solve under different
    verdicts starts from the photos alone again.
    """
    import copy
    from datetime import datetime, timezone

    if not readings:
        return ev
    out = Evidence(list(ev.photos), ev.n_pool, list(ev.frames), ev.params)
    n_real = len(ev.photos)
    for r in readings:
        if not (0 <= r.frame < len(out.frames)):
            continue
        fe = copy.copy(out.frames[r.frame])
        out.photos.append(Asset(f"{READING_PREFIX}{r.kind}:{r.name}", r.name, datetime(1970, 1, 1, tzinfo=timezone.utc), None, r.lat, r.lon))
        c = r.confidence
        fe.idx = np.append(fe.idx, len(out.photos) - 1)
        fe.w = np.append(fe.w * (1 - c), c)
        fe.sims = np.append(fe.sims, 0.0)
        fe.lat, fe.lon = np.append(fe.lat, r.lat), np.append(fe.lon, r.lon)
        fe.event = np.append(fe.event, -1)
        fe.q = 1 - (1 - fe.q) * (1 - c)
        fe.places = _hypotheses(fe, out.n_pool, out.params.radius_m, n_real)
        out.frames[r.frame] = fe
    return out


def atlas_candidates(assets: list[Asset], pool: list[Asset], cached: set[str] | dict, reach_m: float) -> list[Asset]:
    """Every embedded, located phone photo outside the window that the window's trail could reach."""
    inpool = {a.uuid for a in pool}
    cand = [a for a in assets if a.uuid in cached and a.uuid not in inpool and a.lat is not None and not a.is_scan
            and "/scopes/syndication/" not in (a.derivative or "")]
    return reachable_atlas(pool, cand, reach_m)


def build(frame_vecs: np.ndarray, pool: list[Asset], pool_vecs: np.ndarray, event_ids: list[int],
          atlas: list[Asset] | None = None, atlas_vecs: np.ndarray | None = None,
          params: EvidenceParams | None = None) -> Evidence:
    """Nearest photos, weights and place hypotheses for every frame."""
    p = params or EvidenceParams()
    photos = list(pool) + list(atlas or [])
    vecs = pool_vecs if not atlas else np.concatenate([pool_vecs, atlas_vecs])
    lat = np.array([a.lat if a.lat is not None else np.nan for a in photos], dtype=float)
    lon = np.array([a.lon if a.lon is not None else np.nan for a in photos], dtype=float)
    ev = np.concatenate([np.asarray(event_ids, dtype=int), np.full(len(photos) - len(pool), -1, dtype=int)])
    sims = frame_vecs @ vecs.T
    ev_pool = np.asarray(event_ids, dtype=int)
    n_events = int(ev_pool.max()) + 1 if len(ev_pool) else 0
    frames = []
    for i in range(sims.shape[0]):
        k = min(p.k, sims.shape[1])
        top = np.argpartition(-sims[i], k - 1)[:k]
        top = top[np.argsort(-sims[i][top])]
        s = sims[i][top]
        w = np.exp((s - s[0]) / p.tau)
        w /= w.sum()
        fe = FrameEvidence(top, w, s, lat[top], lon[top], ev[top], quality(float(s[0]), p))
        fe.places = _hypotheses(fe, len(pool), p.radius_m)
        fe.event_best = np.full(n_events, -1.0)
        np.maximum.at(fe.event_best, ev_pool, sims[i, : len(pool)])
        order = np.argsort(sims[i, : len(pool)])                     # ascending: the last write per event is its best
        fe.event_best_idx = np.zeros(n_events, dtype=int)
        fe.event_best_idx[ev_pool[order]] = order
        frames.append(fe)
    return Evidence(photos, len(pool), frames, p)


def place_support(fe: FrameEvidence, p: EvidenceParams, lat: np.ndarray, lon: np.ndarray, radius: np.ndarray,
                  event: np.ndarray, is_event: np.ndarray, is_stay: np.ndarray, is_visit: np.ndarray, is_gap: np.ndarray,
                  reachable) -> tuple[np.ndarray, dict[int, tuple]]:
    """How much each state is supported as the place and time of one frame, and by which photo.

    The states are given as arrays (`lat`/`lon` nan where a state has no place, `event` -1 where
    it is not an event). For each place the frame's nearest photos point to:

    * if the window's timeline was ever there — events, silent stays, recorded visits within
      reach of the spot — each of them is supported by the place's vote, the events according
      to how much their own photos resemble the frame (see `EvidenceParams`), and so, at a
      discount, is any other gap from which the place can be reached;
    * if it was never there, the vote goes to every gap from which the place can be reached
      (`reachable(state index, lat, lon)`).

    A state keeps the best support any hypothesis gives it. Returns (support per state,
    state -> (lat, lon, photo index, vote share, photo is of the state's own event)).
    """
    S = len(lat)
    support = np.zeros(S)
    choice: dict[int, tuple] = {}
    located = ~np.isnan(lat)

    def offer(j: int, value: float, c: tuple) -> None:
        if value > support[j]:
            support[j] = value
            choice[j] = c

    for h in fe.places:
        if h.mass < p.min_mass:
            break
        d = np.full(S, np.inf)
        d[located] = haversine_many(h.lat, h.lon, lat[located], lon[located])
        hosts = d <= radius
        if hosts.any():
            ev_ids = np.unique(event[hosts & is_event])
            ratio: dict[int, float] = {}
            if len(ev_ids):
                best = fe.event_best[ev_ids]
                ratio = dict(zip(ev_ids.tolist(), np.exp((best - best.max()) / p.tau).tolist()))
            # What the window's own photos cast can tell the visits apart; what a sign or an
            # atlas photo casts cannot — it says the place, and every visit to it is as good.
            other = h.mass - h.photo_mass
            near_h = np.zeros(len(fe.idx), dtype=bool)
            ok = ~np.isnan(fe.lat)
            near_h[ok] = haversine_many(h.lat, h.lon, fe.lat[ok], fe.lon[ok]) <= p.radius_m
            for j in np.where(hosts)[0]:
                if is_event[j]:
                    e = int(event[j])
                    # The photo that speaks for this state: the visit's own most similar photo
                    # *at this place* — an event that wanders is hosted by its nearest end.
                    mine = near_h & (fe.event == e)
                    photo, own = (int(fe.idx[int(np.argmax(mine))]), True) if mine.any() else (h.best, False)
                    term = p.other_visit + (1 - p.other_visit) * ratio[e]
                    offer(j, h.photo_mass * term + other, (h.lat, h.lon, photo, h.mass, own))
                else:
                    value = h.photo_mass * p.other_visit + other
                    offer(j, value if is_visit[j] else value * p.stay_discount, (h.lat, h.lon, h.best, h.mass, False))
            # And the silent time from which the place can be reached: the phone comes out at
            # the drive-in forty minutes after the frame of its sign; the stop on the way home
            # was photographed another day. Worth what a silent stay there is worth.
            value = (h.photo_mass * p.other_visit + other) * p.stay_discount
            for j in np.where(is_gap & ~hosts & ~is_visit)[0]:
                if value > support[j] and reachable(j, h.lat, h.lon):
                    offer(j, value, (h.lat, h.lon, h.best, h.mass, False))
        else:
            # Nowhere on the window's timeline: any gap from which the place can be reached. A
            # name read off the frame keeps its whole vote; a photo from another year is a
            # look-alike risk and keeps `offtrail` of it.
            value = h.read_mass + (h.mass - h.read_mass) * p.offtrail
            for j in np.where(is_gap & ~is_visit)[0]:
                if value > support[j] and reachable(j, h.lat, h.lon):
                    offer(j, value, (h.lat, h.lon, h.best, h.mass, False))
    # Events whose photos carry no GPS can only be recognised by those photos.
    for j in np.where(is_event & ~located)[0]:
        e = int(event[j])
        w = math.exp((fe.event_best[e] - fe.event_best.max()) / p.tau)
        offer(j, p.other_visit + (1 - p.other_visit) * w, (float("nan"), float("nan"), int(fe.event_best_idx[e]), 0.0, True))
    return support, choice


def occasion_confidence(fe: FrameEvidence, p: EvidenceParams, event: int, rivals: list[int]) -> float:
    """How sure it is that an unverified frame sitting in `event` belongs to that visit, among `rivals`
    (the events at the same place, itself included): the measured reliability of the best-looking
    visit at this similarity, times this visit's share of the look."""
    ids = np.array(sorted(set(rivals) | {event}), dtype=int)
    best = fe.event_best[ids]
    w = np.exp((best - best.max()) / p.tau_occasion)
    share = float(w[list(ids).index(event)] / w.sum())
    return alpha_of(float(best.max()), p) * share
