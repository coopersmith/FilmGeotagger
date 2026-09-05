"""Foursquare / Swarm as trail points (COO-144): named check-ins, and the app's passive visits.

A Swarm data export (Foursquare → privacy → export) unpacks to a folder of `checkins<N>.json`
files plus `visits.json`, `photos<N>.json` and a few tables. Two of them locate the user:

* **Check-ins** — the user tapped a venue: `createdAt` (a `YYYY-MM-DD HH:MM:SS.ffffff` string
  **in UTC**), `lat`/`lng`, `timeZoneOffset` in **minutes** east of UTC, and `venue.name`.
  Measured against the Photos library on this Mac's export (154 check-ins in 2026 with a phone
  photo within 300 m): read as UTC the median gap to the nearest photo is 6 minutes and 119 of
  154 fall within 30 minutes; read as local wall clock the median is 4 hours. UTC it is.
* **Visits** — Swarm's background location: `timeArrived`/`timeDeparted` (same UTC format),
  `latitude`/`longitude`, `city`, `locationType` (`Venue`, `Home`, ...). No zone offset; it is
  borrowed from the check-ins around it, else from the nearest phone photo by instant.

What a check-in adds that no other source can: a *name*. "787 Coffee Co., 14:12" labels the
trail point, which labels the cluster the review UI offers for an ambiguous frame, and is the
kind of fact a person can confirm against a frame at a glance. Visits add intervals — the user
was *here from 15:23 to 21:31* — which the trail turns into a point at arrival, one at departure
and one every half hour between, so a long stay reads as a dense, tight cluster.

Sparse and honest: nobody checks in at their own sofa, so this says nothing about the at-home
weeks; its value is on outings. Drop the export folder under `.filmgeo/signals/swarm/`.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path

from filmgeo.config import DATA_DIR
from filmgeo.signals.base import Constraint, TrailPoint, Window

SOURCE_CHECKIN = "swarm"
SOURCE_VISIT = "visit"
SWARM_DIR = DATA_DIR / "signals" / "swarm"
VISIT_STEP = timedelta(minutes=30)

OffsetAt = Callable[[datetime], int | None]


def parse_utc(text: str) -> datetime:
    """`2026-08-17 20:25:35.000000` → aware UTC datetime."""
    return datetime.strptime(text[:19], "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc)


@dataclass(frozen=True)
class Checkin:
    time: datetime            # UTC
    lat: float
    lon: float
    tzoffset: int             # seconds east of UTC
    venue: str
    id: str


@dataclass(frozen=True)
class Visit:
    arrived: datetime         # UTC
    departed: datetime | None
    lat: float
    lon: float
    city: str | None
    kind: str | None          # locationType
    id: str


def load_checkins(directory: Path) -> list[Checkin]:
    out = []
    for p in sorted(directory.glob("checkins*.json")):
        try:
            items = json.loads(p.read_text()).get("items", [])
        except (json.JSONDecodeError, AttributeError):
            continue
        for c in items:
            if c.get("lat") is None or c.get("lng") is None or not c.get("createdAt"):
                continue
            out.append(Checkin(parse_utc(c["createdAt"]), float(c["lat"]), float(c["lng"]),
                               int(c.get("timeZoneOffset") or 0) * 60, (c.get("venue") or {}).get("name") or "", str(c.get("id"))))
    out.sort(key=lambda c: c.time)
    return out


def load_visits(directory: Path) -> list[Visit]:
    p = directory / "visits.json"
    if not p.exists():
        return []
    try:
        items = json.loads(p.read_text()).get("items", [])
    except (json.JSONDecodeError, AttributeError):
        return []
    out = []
    for v in items:
        if v.get("latitude") is None or not v.get("timeArrived"):
            continue
        out.append(Visit(parse_utc(v["timeArrived"]), parse_utc(v["timeDeparted"]) if v.get("timeDeparted") else None,
                         float(v["latitude"]), float(v["longitude"]), v.get("city"), v.get("locationType"), str(v.get("id"))))
    out.sort(key=lambda v: v.arrived)
    return out


class Swarm:
    """`Signal` adapter over a Swarm export folder. Parsed once per instance."""

    name = "swarm"

    def __init__(self, directory: Path = SWARM_DIR, offset_at: OffsetAt | None = None, visits: bool = True,
                 visit_step: timedelta = VISIT_STEP):
        self.directory = Path(directory)
        self.offset_at = offset_at
        self.use_visits = visits
        self.visit_step = visit_step
        self._checkins: list[Checkin] | None = None
        self._visits: list[Visit] | None = None

    @property
    def checkins(self) -> list[Checkin]:
        if self._checkins is None:
            self._checkins = load_checkins(self.directory) if self.directory.is_dir() else []
        return self._checkins

    @property
    def visits(self) -> list[Visit]:
        if self._visits is None:
            self._visits = load_visits(self.directory) if self.directory.is_dir() and self.use_visits else []
        return self._visits

    def _offset(self, t: datetime, near: list[Checkin]) -> int | None:
        """The nearest check-in's offset within a day, else the photo trail's, else None."""
        best = min(near, key=lambda c: abs(c.time - t), default=None)
        if best is not None and abs(best.time - t) <= timedelta(days=1):
            return best.tzoffset
        return self.offset_at(t) if self.offset_at else None

    def trail_points(self, window: Window) -> list[TrailPoint]:
        out: list[TrailPoint] = []
        cis = [c for c in self.checkins if window.contains(c.time)]
        for c in cis:
            out.append(TrailPoint(c.time, c.lat, c.lon, SOURCE_CHECKIN, tzoffset=c.tzoffset, label=c.venue or None, ref=c.id))
        pad = timedelta(days=1)
        near_all = [c for c in self.checkins if window.start - pad <= c.time <= window.end + pad]
        for v in self.visits:
            end = v.departed or v.arrived
            if end < window.start or v.arrived > window.end:
                continue
            label = f"{v.kind or 'visit'}: {v.city}" if v.city else (v.kind or None)
            t = v.arrived
            while True:
                if window.contains(t):
                    out.append(TrailPoint(t, v.lat, v.lon, SOURCE_VISIT, tzoffset=self._offset(t, near_all), label=label, ref=v.id))
                if t >= end:
                    break
                t = min(t + self.visit_step, end)
        out.sort(key=lambda p: p.time)
        return out

    def constraints(self) -> list[Constraint]:
        return []
