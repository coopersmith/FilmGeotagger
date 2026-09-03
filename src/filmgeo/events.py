"""Event segmentation over the phone timeline.

An event is a stretch of photos taken close together in time and place — one visit, one meal,
one walk. Events matter twice: they cap retrieval diversity so a single heavily photographed
scene cannot fill a frame's whole candidate list, and in M2 they become the alignment states a
frame can occupy when no anchor pins it exactly.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import datetime

from filmgeo.config import EVENT_GAP_SECONDS, EVENT_MOVE_METRES
from filmgeo.photos.library import Asset


def haversine_m(a: tuple[float, float], b: tuple[float, float]) -> float:
    r = 6371000.0
    p1, p2 = math.radians(a[0]), math.radians(b[0])
    dp, dl = p2 - p1, math.radians(b[1] - a[1])
    h = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(math.sqrt(h))


@dataclass
class Event:
    index: int
    start: datetime
    end: datetime
    lat: float | None
    lon: float | None
    spread_m: float
    count: int


def segment(assets: list[Asset]) -> tuple[list[int], list[Event]]:
    """Assign each asset an event id. Assets must be sorted by date.

    A new event begins on a long enough gap, or on a move far enough to be somewhere else.
    """
    ids: list[int] = []
    current = 0
    last_time: datetime | None = None
    last_loc: tuple[float, float] | None = None

    for a in assets:
        if last_time is not None:
            gap = (a.date - last_time).total_seconds()
            moved = (
                last_loc is not None
                and a.has_location
                and haversine_m(last_loc, (a.lat, a.lon)) > EVENT_MOVE_METRES
            )
            if gap > EVENT_GAP_SECONDS or moved:
                current += 1
        ids.append(current)
        last_time = a.date
        if a.has_location:
            last_loc = (a.lat, a.lon)

    events = []
    for e in range(current + 1):
        members = [a for a, i in zip(assets, ids) if i == e]
        located = [(a.lat, a.lon) for a in members if a.has_location]
        if located:
            clat = sum(p[0] for p in located) / len(located)
            clon = sum(p[1] for p in located) / len(located)
            spread = max(haversine_m((clat, clon), p) for p in located)
        else:
            clat = clon = None
            spread = 0.0
        events.append(
            Event(e, members[0].date, members[-1].date, clat, clon, spread, len(members))
        )
    return ids, events
