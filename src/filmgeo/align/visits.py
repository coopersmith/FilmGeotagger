"""Recorded visits: when the trail's *other* sources say the phone was at a place (COO-177).

The camera roll is the diary the engine reads first. A check-in, a timeline stop or a tapped
NFC tag is one more entry in the same diary — "at this place, at this time" — without a
photograph. It becomes a state of its own on the timeline, so a frame whose own evidence (a
sign read off it, an atlas photo of the place from another year) points there has somewhere
on the right day to land. Nothing here is required: with no extra sources there are no visits
and the engine runs on the camera roll alone.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta

from filmgeo.events import haversine_m
from filmgeo.signals.base import TrailPoint

PHOTO_SOURCES = {"photos", "user"}       # photos are events already; the user's facts are constraints
MOVING_SOURCES = {"health", "timeline"}  # route samples: a visit only where they dwell
BEFORE = timedelta(minutes=15)           # a check-in is a moment inside a stop: this much before it...
AFTER = timedelta(minutes=30)            # ...and after
JOIN_M = 300.0
JOIN_GAP = timedelta(minutes=45)
DWELL = timedelta(minutes=5)


@dataclass
class Visit:
    t_lo: datetime
    t_hi: datetime
    lat: float
    lon: float
    label: str | None = None
    source: str = "visit"


def from_trail(trail: list[TrailPoint]) -> list[Visit]:
    """Runs of non-photo trail points at one place, as visits in time order."""
    pts = sorted((p for p in trail if p.source not in PHOTO_SOURCES and p.has_location), key=lambda p: p.time)
    runs: list[list[TrailPoint]] = []
    for p in pts:
        last = runs[-1][-1] if runs else None
        if last is not None and p.time - last.time <= JOIN_GAP and haversine_m((last.lat, last.lon), (p.lat, p.lon)) <= JOIN_M:
            runs[-1].append(p)
        else:
            runs.append([p])
    out = []
    for run in runs:
        if all(p.source in MOVING_SOURCES for p in run) and run[-1].time - run[0].time < DWELL:
            continue                                   # passing through
        lat = sum(p.lat for p in run) / len(run)
        lon = sum(p.lon for p in run) / len(run)
        label = next((p.label for p in run if p.label and p.source == "swarm"), None) or next((p.label for p in run if p.label), None)
        single = run[0].time == run[-1].time
        out.append(Visit(run[0].time - (BEFORE if single else timedelta(0)), run[-1].time + (AFTER if single else timedelta(0)),
                         lat, lon, label, run[0].source))
    return out
