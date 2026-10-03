"""Where a frame was, from what it looks like (COO-177).

`geo.place` reads a location off the trail once the solver has said *when*. That is the right
order only when the time is known. With place evidence the order can be turned round: every
state the frame might occupy offers a place (the event's, the stay's, or a reachable place its
nearest photos point to), the posterior weighs the states, and states that agree on the place
add up — a frame the solver cannot date between six afternoons at one house is still at that
house with all six afternoons' mass. When one place holds enough of it, that is the frame's
location, whatever the trail inside its interval says, and how sure it is is reported on its
own (`place_confidence`), apart from how sure the time is (`confidence`).
"""

from __future__ import annotations

from filmgeo.align.evidence import READING_PREFIX
from filmgeo.align.model import RollModel
from filmgeo.align.solve import Solution
from filmgeo.events import haversine_m

PLACE_MIN = 0.6          # (vote quality x posterior mass) a place needs before it is written as the location
SAME_PLACE_M = 300.0


def locate(model: RollModel, solution: Solution, pinned: set[int] | None = None, place_min: float = PLACE_MIN) -> Solution:
    """Set lat/lon, `location_source = "visual"`, `place_confidence` and `place_uuid` where the photos decide."""
    ev = model.evidence
    if ev is None:
        return solution
    pinned = pinned or set()
    by_frame: dict[int, list[tuple[int, tuple]]] = {}
    for (i, j), c in model.choices.items():
        by_frame.setdefault(i, []).append((j, c))
    for i, a in enumerate(solution.assignments):
        if i in pinned or a.source in ("anchored", "locked", "skipped"):
            continue
        # Group the states' offers by the photo's own position, heaviest first.
        offers = []
        for j, c in by_frame.get(i, []):
            mass = float(solution.posterior[i, j])
            photo = ev.photos[c[2]]
            if mass > 1e-6 and photo.lat is not None:
                offers.append((mass, photo, j))
        offers.sort(key=lambda o: -o[0])
        places: list[list] = []            # [mass, best photo, its mass, state]
        for mass, photo, j in offers:
            for pl in places:
                if haversine_m((pl[1].lat, pl[1].lon), (photo.lat, photo.lon)) <= SAME_PLACE_M:
                    pl[0] += mass
                    break
            else:
                places.append([mass, photo, mass, j])
        if not places:
            continue
        places.sort(key=lambda pl: -pl[0])
        mass, photo = places[0][0], places[0][1]
        # The photo shown is the one the proposal itself rests on when it is at this place.
        c = model.choices.get((i, a.state))
        if c is not None:
            mine = ev.photos[c[2]]
            if mine.lat is not None and haversine_m((mine.lat, mine.lon), (photo.lat, photo.lon)) <= SAME_PLACE_M:
                photo = mine
        # The posterior says how the states' offers divide; whether the offers mean anything is
        # the frame's own q — a frame whose nearest photo is a look-alike has places to offer
        # and no reason to believe them. Both must hold before a pin is written.
        mass *= ev.frames[i].q
        a.place_confidence = mass
        if mass >= place_min:
            a.lat, a.lon = photo.lat, photo.lon
            a.location, a.clusters = "ok", []
            if photo.uuid.startswith(READING_PREFIX):       # a name read off the frame, not a photo
                a.location_source, a.place_name = "reading", photo.filename
            else:
                a.location_source, a.place_uuid = "visual", photo.uuid
    return solution
