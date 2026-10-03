"""Where a frame was, from what it looks like (COO-177).

`geo.place` reads a location off the trail once the solver has said *when*. That is the right
order only when the time is known. With place evidence the order is turned round, in two steps.

**Decide the place** (`decide`). Each frame's nearest photos vote for places. A place holding
most of a believable vote is decided outright. The rest are decided against those: the roll is
solved with the sure frames held to their places, and each remaining frame takes the heaviest
place of its vote that this leaves *possible*, with confidence = its share of the possible
vote times how much the vote is worth at all (q). How many time slots a place has does not enter:
a house with sixty stays in the window and a tenth of the vote must not outweigh a farm stand
read off the frame with one recorded visit, and a restaurant visited once with 7% of the vote
must not draw in frames of a house with 92% and forty visits. Both happened while the place
was read off the posterior mass. Order is a constraint and removes what cannot be; it is not
a weight (docs/m5-findings.md: nudges lose, constraints win).

**Then the time, within the place** (`solve.solve` prunes each decided frame to the states
that hold it at its place and solves again), so the proposal's time and place agree and the
occasion confidence answers "which visit, given the place".

`apply` writes the result onto the assignments after `geo.place` has done the trail's part.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from filmgeo.align.evidence import READING_PREFIX
from filmgeo.align.model import RollModel
from filmgeo.events import haversine_m

PLACE_MIN = 0.6          # confidence a place needs before it is written as the location
Q_MIN = 0.6              # and the frame's vote must be worth this much: best photo about 0.79 similar, or a name read off it
FEASIBLE_MIN = 0.02      # posterior mass on a place's states for the place to count as possible
SAME_PLACE_M = 300.0


@dataclass
class PlaceDecision:
    lat: float
    lon: float
    confidence: float
    photo: int               # index into evidence.photos: the most similar voter there
    decided: bool            # confident enough to be written as a pin and to prune the solve


SURE_SHARE = 0.8         # a place with this much of the frame's vote is decided on the vote alone


def decide(model: RollModel, post: np.ndarray | None, undecided_only: dict[int, PlaceDecision] | None = None,
           place_min: float = PLACE_MIN, q_min: float = Q_MIN) -> dict[int, PlaceDecision]:
    """Per frame, the heaviest place of its vote.

    With `post=None` only the overwhelming votes are decided (`SURE_SHARE` of the vote on one
    place and a vote worth believing): they need no check. With a posterior — from a solve
    already narrowed to those — every other frame takes the heaviest place *the decided ones
    leave possible*: a look-alike on another continent is out because its neighbours are
    placed, not because of how the evidence happens to weigh.
    """
    ev = model.evidence
    if ev is None:
        return {}
    by_frame: dict[int, list[tuple[int, tuple]]] = {}
    for (i, j), c in model.choices.items():
        by_frame.setdefault(i, []).append((j, c))
    out: dict[int, PlaceDecision] = dict(undecided_only or {})
    for i, offers in by_frame.items():
        if i in out and out[i].decided:
            continue
        fe = ev.frames[i]
        offered = [h for h in fe.places if h.mass >= ev.params.min_mass
                   and any(abs(c[0] - h.lat) < 1e-9 and abs(c[1] - h.lon) < 1e-9 and np.isfinite(model.emissions[i, j]) for j, c in offers)]
        if not offered:
            continue
        if post is None:
            top = offered[0]
            if top.mass >= SURE_SHARE and fe.q >= q_min:
                out[i] = PlaceDecision(top.lat, top.lon, fe.q * top.mass, top.best, fe.q * top.mass >= place_min)
            continue
        possible = [h for h in offered
                    if sum(float(post[i, j]) for j, c in offers if abs(c[0] - h.lat) < 1e-9 and abs(c[1] - h.lon) < 1e-9) >= FEASIBLE_MIN]
        if not possible:
            continue
        top = max(possible, key=lambda h: h.mass)
        conf = fe.q * top.mass / sum(h.mass for h in possible)
        out[i] = PlaceDecision(top.lat, top.lon, conf, top.best, conf >= place_min and fe.q >= q_min)
    return out


def hosts(model: RollModel, i: int, d: PlaceDecision) -> list[int]:
    """States that would hold frame i at its decided place."""
    return [j for (k, j), c in model.choices.items() if k == i and abs(c[0] - d.lat) < 1e-9 and abs(c[1] - d.lon) < 1e-9]


def apply(model: RollModel, solution, pinned: set[int] | None = None) -> None:
    """Write the decided places onto the assignments: lat/lon, source, confidence, and the photo or name."""
    ev = model.evidence
    if ev is None:
        return
    pinned = pinned or set()
    for i, a in enumerate(solution.assignments):
        d = solution.places.get(i)
        if d is None or i in pinned or a.source in ("anchored", "locked", "skipped"):
            continue
        a.place_confidence = d.confidence
        if not d.decided:
            continue
        # The photo shown is the one the proposal itself rests on: the chosen visit's own.
        c = model.choices.get((i, a.state))
        photo = ev.photos[c[2] if c is not None and abs(c[0] - d.lat) < 1e-9 and abs(c[1] - d.lon) < 1e-9 else d.photo]
        if photo.lat is None:
            photo = ev.photos[d.photo]
        a.lat, a.lon = (photo.lat, photo.lon) if photo.lat is not None else (d.lat, d.lon)
        a.location, a.clusters = "ok", []
        if photo.uuid.startswith(READING_PREFIX):       # a name read off the frame, not a photo
            a.location_source, a.place_name = "reading", photo.filename
        else:
            a.location_source, a.place_uuid = "visual", photo.uuid
