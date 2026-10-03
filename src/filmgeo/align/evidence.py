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
    # Support of a state = the vote for its place, scaled between `other_visit` (the place,
    # but none of this visit's photos look like the frame) and 1 (the visit holds the most
    # similar photo). The scale has to be wide: a matching visit that scored only twice another
    # day at the same place lost to the cost of one jump in time, and frames whose best photo
    # sat on the true minute were dated days away.
    other_visit: float = 0.15
    occasion_mode: str = "best"    # see FrameEvidence.in_event
    stay_discount: float = 0.6     # at the place between two bursts of photos there: nothing was photographed
    offtrail_discount: float = 0.6 # at a place the phone did not record this time, reachable inside a gap
    visit_weight: float = 0.5      # at a recorded stop there (a check-in, a timeline visit) with no photos of it
    # What a state with nothing to offer is worth to a frame that does resemble its photos:
    # emissions are (1 - q) * floor + q * support, never below this.
    epsilon: float = 0.003
    speed_kmh: float = 60.0        # how far from the trail a gap can reach
    reach_slack_m: float = 2000.0
    atlas_reach_m: float = 150_000.0   # atlas photos farther than this from every window photo are not candidates


@dataclass
class Hypothesis:
    """One place a frame may have been taken, as its nearest photos see it."""

    lat: float
    lon: float
    mass: float                    # share of the frame's vote within `radius_m`
    best: int                      # index into `Evidence.photos` of the most similar member
    in_window: bool                # some member is a photo of the window (the place is on the trail)


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

    def near(self, lat: float, lon: float, radius_m: float) -> tuple[float, int | None]:
        """Vote share within `radius_m` of a spot, and the most similar photo there."""
        ok = ~np.isnan(self.lat)
        d = np.full(len(self.idx), np.inf)
        d[ok] = haversine_many(lat, lon, self.lat[ok], self.lon[ok])
        m = d <= radius_m
        if not m.any():
            return 0.0, None
        return float(self.w[m].sum()), int(self.idx[np.argmax(m)])      # idx is similarity-sorted: first hit is best

    def in_event(self, e: int, mode: str = "best") -> tuple[float, int | None]:
        """How much the frame looks like the photos of one event, and the most similar of them.

        "best": the event's most similar photo against the frame's most similar photo overall
        (1.0 for the event that holds it) — a burst of ten middling photos must not outvote one
        that matches. "share": the event's share of the vote.
        """
        m = self.event == e
        if not m.any():
            return 0.0, None
        k = int(np.argmax(m))
        if mode != "best":
            return float(self.w[m].sum()), int(self.idx[k])
        # Against the best photo *of the window*: an atlas photo of the same spot from another
        # year may be the nearest of all, and says nothing about which visit this was.
        inwin = self.event >= 0
        ref = self.w[int(np.argmax(inwin))]
        return float(self.w[k] / ref), int(self.idx[k])


@dataclass
class Evidence:
    photos: list[Asset]            # the window's pool first, then the atlas
    n_pool: int
    frames: list[FrameEvidence]
    params: EvidenceParams

    @property
    def n_atlas(self) -> int:
        return len(self.photos) - self.n_pool


def quality(top_sim: float, p: EvidenceParams) -> float:
    return 1.0 / (1.0 + math.exp(-p.q_slope * (top_sim - p.q_centre)))


def _hypotheses(fe: FrameEvidence, n_pool: int, radius_m: float) -> list[Hypothesis]:
    out: list[Hypothesis] = []
    taken = np.isnan(fe.lat)
    for k in range(len(fe.idx)):
        if taken[k]:
            continue
        d = np.full(len(fe.idx), np.inf)
        free = ~taken
        d[free] = haversine_many(fe.lat[k], fe.lon[k], fe.lat[free], fe.lon[free])
        m = d <= radius_m
        out.append(Hypothesis(float(fe.lat[k]), float(fe.lon[k]), float(fe.w[m].sum()), int(fe.idx[k]), bool((fe.idx[m] < n_pool).any())))
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
    """Fold places read off the frames (`align.readings`) into the vote.

    A reading enters its frame's list as one more voter with the reading's confidence as its
    weight — the photos keep the rest — standing at the named place, belonging to no event.
    The frame's vote is then worth at least that confidence: a sign is evidence even when not
    one photo resembles the frame.
    """
    from datetime import datetime, timezone

    for r in readings:
        if not (0 <= r.frame < len(ev.frames)):
            continue
        fe = ev.frames[r.frame]
        ev.photos.append(Asset(f"{READING_PREFIX}{r.kind}:{r.name}", r.name, datetime(1970, 1, 1, tzinfo=timezone.utc), None, r.lat, r.lon))
        c = r.confidence
        fe.idx = np.append(fe.idx, len(ev.photos) - 1)
        fe.w = np.append(fe.w * (1 - c), c)
        fe.sims = np.append(fe.sims, 0.0)
        fe.lat, fe.lon = np.append(fe.lat, r.lat), np.append(fe.lon, r.lon)
        fe.event = np.append(fe.event, -1)
        fe.q = 1 - (1 - fe.q) * (1 - c)
        fe.places = _hypotheses(fe, ev.n_pool, ev.params.radius_m)
    return ev


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
        frames.append(fe)
    return Evidence(photos, len(pool), frames, p)
