"""Apple Health workout routes as trail points (COO-132).

A Health export (`Health → profile → Export All Health Data`) unpacks to
`apple_health_export/`, and every workout that recorded a route is a GPX file under
`workout-routes/`, named `route_2026-04-05_10.12am.gpx`. Each track point carries a latitude,
longitude, elevation and an ISO-8601 time **in UTC** (`2026-04-05T14:12:03Z`), one a second or
so for the length of the walk or ride. Nothing else in the export locates the user, so this is
the whole adapter.

Why it earns a place: a walk with the film camera and no phone photo leaves no trail at all in
Photos, and the roll's frames from that hour fall into a gap state with an unknown place. The
route puts a point every minute along the walk, so the frames get a location and a tighter
offset, and the timeline shows where the silence went.

Points are subsampled to one per `STEP` seconds — a two-hour walk is 7,000 track points and
the trail needs a few hundred. The UTC offset is not in the file; it comes from the nearest
phone photo by instant (`offset_at`), the same way the NFC log borrows one by wall clock.

Drop the folder (or just its `workout-routes/`) under `.filmgeo/signals/health/`; the adapter
finds every `.gpx` beneath it. Only files whose name-date falls inside the window (±1 day) are
opened, so a decade of exports costs nothing per roll.
"""

from __future__ import annotations

import re
import xml.etree.ElementTree as ET
from collections.abc import Callable
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

from filmgeo.config import DATA_DIR
from filmgeo.signals.base import Constraint, TrailPoint, Window

SOURCE = "health"
HEALTH_DIR = DATA_DIR / "signals" / "health"
STEP = timedelta(seconds=60)

_NAME_DATE = re.compile(r"(\d{4})-(\d{2})-(\d{2})")
_NS = {"gpx": "http://www.topografix.com/GPX/1/1"}

OffsetAt = Callable[[datetime], int | None]


def name_date(path: Path) -> date | None:
    m = _NAME_DATE.search(path.name)
    return date(int(m.group(1)), int(m.group(2)), int(m.group(3))) if m else None


def parse_gpx(path: Path, step: timedelta = STEP) -> list[tuple[datetime, float, float, float | None]]:
    """(time, lat, lon, elevation) per track point, subsampled to one per `step`. Times are tz-aware."""
    try:
        root = ET.parse(path).getroot()
    except ET.ParseError:
        return []
    ns = "{http://www.topografix.com/GPX/1/1}"
    pts = root.iter(f"{ns}trkpt") if root.tag.startswith(ns) else root.iter("trkpt")
    out: list[tuple[datetime, float, float, float | None]] = []
    last: datetime | None = None
    for p in pts:
        t_el = p.find(f"{ns}time") if root.tag.startswith(ns) else p.find("time")
        if t_el is None or not t_el.text:
            continue
        t = datetime.fromisoformat(t_el.text.strip().replace("Z", "+00:00"))
        if t.tzinfo is None:
            t = t.replace(tzinfo=timezone.utc)
        if last is not None and t - last < step:
            continue
        ele_el = p.find(f"{ns}ele") if root.tag.startswith(ns) else p.find("ele")
        ele = float(ele_el.text) if ele_el is not None and ele_el.text else None
        out.append((t, float(p.get("lat")), float(p.get("lon")), ele))
        last = t
    return out


class HealthRoutes:
    """`Signal` adapter over a folder of Health workout-route GPX files."""

    name = "health_routes"

    def __init__(self, directory: Path = HEALTH_DIR, offset_at: OffsetAt | None = None, step: timedelta = STEP):
        self.directory = Path(directory)
        self.offset_at = offset_at
        self.step = step

    def files(self, window: Window | None = None) -> list[Path]:
        if not self.directory.is_dir():
            return []
        out = []
        for p in sorted(self.directory.rglob("*.gpx")):
            d = name_date(p)
            if window is not None and d is not None:
                if not (window.start.date() - timedelta(days=1) <= d <= window.end.date() + timedelta(days=1)):
                    continue
            out.append(p)
        return out

    def trail_points(self, window: Window) -> list[TrailPoint]:
        out: list[TrailPoint] = []
        for path in self.files(window):
            for t, lat, lon, _ in parse_gpx(path, self.step):
                if not window.contains(t):
                    continue
                off = self.offset_at(t) if self.offset_at else None
                out.append(TrailPoint(t, lat, lon, SOURCE, tzoffset=off, label=path.stem.removeprefix("route_"), ref=path.name))
        return out

    def constraints(self) -> list[Constraint]:
        return []
