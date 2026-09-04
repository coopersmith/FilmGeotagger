"""The phone-photo location trail.

Every dated asset in the library is a trail point, whether or not it has a local derivative:
a photo that cannot be *matched* still says where the user was and what the UTC offset was.
Film scans are excluded, tagged or not — they are the thing being located, not evidence of it.
"""

from __future__ import annotations

from datetime import datetime, timedelta

from filmgeo.photos.library import Asset
from filmgeo.signals.base import Constraint, TrailPoint, Window

SOURCE = "photos"


class PhotosTrail:
    name = "photos_trail"

    def __init__(self, assets: list[Asset]):
        self.assets = assets  # sorted by date, as library.load() returns them

    def trail_points(self, window: Window) -> list[TrailPoint]:
        return [
            TrailPoint(a.date, a.lat, a.lon, SOURCE, tzoffset=a.tzoffset, ref=a.uuid)
            for a in self.assets
            if not a.is_scan and window.contains(a.date)
        ]

    def constraints(self) -> list[Constraint]:
        return []

    def offset_for(self, naive_local: datetime, lat: float | None = None, lon: float | None = None,
                   within: timedelta = timedelta(days=3)) -> int | None:
        """UTC offset in force at a wall-clock instant, read off the nearest phone photo.

        The NFC log records local wall-clock time with no zone. osxphotos gives each photo a
        tz-aware date *in the photo's own zone*, so its wall clock is comparable directly, and
        the closest photo's `tzoffset` is the best available guess at the zone the user was in.
        None if no photo with an offset sits within `within` — the caller falls back.
        """
        best, best_gap = None, within
        for a in self.assets:
            if a.is_scan or a.tzoffset is None:
                continue
            gap = abs(a.date.replace(tzinfo=None) - naive_local)
            if gap < best_gap:
                best, best_gap = a, gap
        return best.tzoffset if best else None
