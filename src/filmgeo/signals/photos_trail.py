"""The phone-photo location trail.

Every dated asset in the library is a trail point, whether or not it has a local derivative:
a photo that cannot be *matched* still says where the user was and what the UTC offset was.
Film scans are excluded, tagged or not — they are the thing being located, not evidence of it.
"""

from __future__ import annotations

import bisect
from datetime import datetime, timedelta

from filmgeo.photos.library import Asset
from filmgeo.signals.base import Constraint, TrailPoint, Window

SOURCE = "photos"


class PhotosTrail:
    name = "photos_trail"

    def __init__(self, assets: list[Asset]):
        self.assets = assets  # sorted by date, as library.load() returns them
        self._by_instant: tuple[list[float], list[int]] | None = None
        self._by_wall: tuple[list[float], list[int]] | None = None

    def _index(self) -> None:
        """The photos that carry an offset, once, sorted by instant and by wall clock.

        Every check-in, route sample and NFC tap asks for the offset near it; scanning the whole
        library for each — 141,000 assets and a filename regex apiece — was a roll's entire load
        time (29 s for a window with 134 check-ins)."""
        rows = [(a.date.timestamp(), a.date.replace(tzinfo=None).timestamp(), a.tzoffset)
                for a in self.assets if a.tzoffset is not None and not a.is_scan]
        inst = sorted((r[0], r[2]) for r in rows)
        wall = sorted((r[1], r[2]) for r in rows)
        self._by_instant = ([t for t, _ in inst], [o for _, o in inst])
        self._by_wall = ([t for t, _ in wall], [o for _, o in wall])

    @staticmethod
    def _nearest(index: tuple[list[float], list[int]], t: float, within: timedelta) -> int | None:
        times, offsets = index
        i = bisect.bisect_left(times, t)
        best = None
        for j in (i - 1, i):
            if 0 <= j < len(times) and abs(times[j] - t) < within.total_seconds() and (best is None or abs(times[j] - t) < abs(times[best] - t)):
                best = j
        return offsets[best] if best is not None else None

    def trail_points(self, window: Window) -> list[TrailPoint]:
        return [
            TrailPoint(a.date, a.lat, a.lon, SOURCE, tzoffset=a.tzoffset, ref=a.uuid)
            for a in self.assets
            if not a.is_scan and window.contains(a.date)
        ]

    def constraints(self) -> list[Constraint]:
        return []

    def offset_at(self, instant: datetime, within: timedelta = timedelta(days=3)) -> int | None:
        """UTC offset in force at a tz-aware instant, from the nearest phone photo by instant.

        For sources that know the instant but not the zone (Health routes are in UTC). None if
        no photo with an offset sits within `within`.
        """
        if self._by_instant is None:
            self._index()
        return self._nearest(self._by_instant, instant.timestamp(), within)

    def offset_for(self, naive_local: datetime, lat: float | None = None, lon: float | None = None,
                   within: timedelta = timedelta(days=3)) -> int | None:
        """UTC offset in force at a wall-clock instant, read off the nearest phone photo.

        The NFC log records local wall-clock time with no zone. osxphotos gives each photo a
        tz-aware date *in the photo's own zone*, so its wall clock is comparable directly, and
        the closest photo's `tzoffset` is the best available guess at the zone the user was in.
        None if no photo with an offset sits within `within` — the caller falls back.
        """
        if self._by_wall is None:
            self._index()
        return self._nearest(self._by_wall, naive_local.timestamp(), within)
