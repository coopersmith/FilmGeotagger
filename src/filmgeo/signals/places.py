"""Named places from the trail: what the user can point at and say "that frame was there" (COO-174).

A phone photo can date a frame because the user can see it. A check-in can too, because the
user can *read* it: "Young Family Farm, Sunday 16:14". On the second end-to-end roll two of ten
frames had no matching photo and both were dated from Swarm by hand; this module is what turns
that into a list with a button.

A place is a check-in, an NFC tap, or a visit (arrival to departure). A check-in within
`MERGE_M` and `MERGE_GAP` of a visit is the same stop, so the two merge: the check-in gives the
name, the visit gives the span. `Home` and `Work` visits are kept but flagged, because a roll
shot at home has hundreds of them and the list is for picking by eye.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta

from filmgeo.events import haversine_m
from filmgeo.signals.base import TrailPoint

NAMED_SOURCES = ("swarm", "nfc")
VISIT_SOURCE = "visit"
MERGE_M = 300.0
MERGE_GAP = timedelta(minutes=30)
ROUTINE = ("home", "work")


@dataclass
class Place:
    name: str
    start: datetime
    end: datetime
    lat: float
    lon: float
    tzoffset: int | None
    kind: str                 # "check-in" | "visit" | "tap"
    ref: str
    checkin: datetime | None = None     # the instant the user tapped, when there was one
    routine: bool = False     # Home / Work

    @property
    def when(self) -> datetime:
        """The instant a frame takes from this place: the check-in if there was one, else the arrival."""
        return self.checkin or self.start


def _is_routine(label: str | None) -> bool:
    return bool(label) and label.strip().lower().split(":")[0] in ROUTINE


def from_trail(trail: list[TrailPoint]) -> list[Place]:
    """Every named stop in the trail, in time order, check-ins merged into the visit they belong to."""
    visits: dict[str, list[TrailPoint]] = {}
    named: list[TrailPoint] = []
    for p in trail:
        if not p.has_location:
            continue
        if p.source == VISIT_SOURCE and p.ref:
            visits.setdefault(p.ref, []).append(p)
        elif p.source in NAMED_SOURCES and (p.label or p.source == "nfc"):
            named.append(p)
    places: list[Place] = []
    for ref, pts in visits.items():
        pts.sort(key=lambda p: p.time)
        first = pts[0]
        places.append(Place(first.label or "visit", first.time, pts[-1].time, first.lat, first.lon, first.tzoffset,
                            "visit", ref, routine=_is_routine(first.label)))
    for c in named:
        kind = "tap" if c.source == "nfc" else "check-in"
        host = next((v for v in places if v.kind == "visit" and v.checkin is None
                     and v.start - MERGE_GAP <= c.time <= v.end + MERGE_GAP
                     and haversine_m((v.lat, v.lon), (c.lat, c.lon)) <= MERGE_M), None)
        if host is not None and kind == "check-in":
            host.name, host.kind, host.checkin, host.routine = c.label, "check-in", c.time, False
            host.start, host.end = min(host.start, c.time), max(host.end, c.time)
            host.lat, host.lon = c.lat, c.lon
            if c.tzoffset is not None:
                host.tzoffset = c.tzoffset
            host.ref = c.ref or host.ref
        else:
            places.append(Place(c.label or "NFC tap", c.time, c.time, c.lat, c.lon, c.tzoffset, kind, c.ref or "", checkin=c.time))
    places.sort(key=lambda p: p.start)
    return places


def between(places: list[Place], lo: datetime, hi: datetime) -> list[Place]:
    return [p for p in places if p.end >= lo and p.start <= hi]


def search(places: list[Place], q: str) -> list[Place]:
    words = [w for w in q.lower().split() if w]
    return [p for p in places if words and all(w in p.name.lower() for w in words)]
