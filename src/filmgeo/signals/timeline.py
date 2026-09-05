"""Google Maps Timeline as trail points (COO-133), in the three shapes Google has shipped.

* **`Timeline.json`** — the current, on-device export (Maps → your timeline → export). Times
  are ISO-8601 *with their offset* (`2026-04-05T10:12:00.000-04:00`), so the zone comes free.
  `semanticSegments` hold `timelinePath` (a polyline of `point` "lat°, lng°" with a time each),
  `visit` (a stay: `topCandidate.placeLocation.latLng`, `semanticType` HOME / WORK / …,
  `probability`) and `activity` (a move from `start.latLng` to `end.latLng`). `rawSignals`
  hold `position` fixes with `LatLng`, `accuracyMeters`, `timestamp`. `userLocationProfile
  .frequentPlaces` names places (`label` HOME / WORK) by `placeId`.
* **`Records.json`** — the legacy Takeout dump: `locations[]` with `latitudeE7`,
  `longitudeE7`, `timestamp` (UTC `Z`), `accuracy`. Dense, unlabelled.
* **`Semantic Location History/<year>/<year>_<MONTH>.json`** — legacy Takeout months:
  `timelineObjects[]` of `placeVisit` (a named `location` with `latitudeE7`/`longitudeE7`,
  `name`, `address`; `duration.startTimestamp`/`endTimestamp`) and `activitySegment`
  (start and end locations and times, `waypointPath.waypoints[]`).

Every shape yields the same two things: `timeline` points along paths and fixes, subsampled
to one a minute, and `visit` points at a stay's start, end and every half hour between,
labelled with the place's name or its semantic type. Offsets come from the timestamp when
it carries one, else from the nearest phone photo by instant (`offset_at`). Drop the export
under `.filmgeo/signals/timeline/`; every `*.json` beneath it is inspected and files that are
none of the three shapes are ignored.

Not measured on a real export — none on this Mac. The fixtures in `tests/test_timeline.py`
follow Google's published field names for each format.
"""

from __future__ import annotations

import json
import re
from collections.abc import Callable
from datetime import datetime, timedelta, timezone
from pathlib import Path

from filmgeo.config import DATA_DIR
from filmgeo.signals.base import Constraint, TrailPoint, Window

SOURCE_PATH = "timeline"
SOURCE_VISIT = "visit"
TIMELINE_DIR = DATA_DIR / "signals" / "timeline"
STEP = timedelta(seconds=60)
VISIT_STEP = timedelta(minutes=30)
MAX_ACCURACY_M = 200.0

OffsetAt = Callable[[datetime], int | None]
_LATLNG = re.compile(r"(-?\d+(?:\.\d+)?)\s*°?\s*,\s*(-?\d+(?:\.\d+)?)\s*°?")


def parse_latlng(text: str | None) -> tuple[float, float] | None:
    if not text:
        return None
    m = _LATLNG.search(text)
    return (float(m.group(1)), float(m.group(2))) if m else None


def parse_time(text: str | None) -> datetime | None:
    if not text:
        return None
    try:
        t = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    return t if t.tzinfo else t.replace(tzinfo=timezone.utc)


def _e7(obj: dict, lat_key: str, lon_key: str) -> tuple[float, float] | None:
    if obj is None or obj.get(lat_key) is None or obj.get(lon_key) is None:
        return None
    return obj[lat_key] / 1e7, obj[lon_key] / 1e7


class Timeline:
    """`Signal` adapter over a folder holding any of the three Google Timeline export shapes."""

    name = "timeline"

    def __init__(self, directory: Path = TIMELINE_DIR, offset_at: OffsetAt | None = None,
                 step: timedelta = STEP, visit_step: timedelta = VISIT_STEP):
        self.directory = Path(directory)
        self.offset_at = offset_at
        self.step = step
        self.visit_step = visit_step
        self._loaded: list[tuple[str, dict]] | None = None

    # -- loading ---------------------------------------------------------------------------

    def files(self) -> list[tuple[str, dict]]:
        """(shape, data) for every recognised JSON file: 'timeline', 'records' or 'semantic'."""
        if self._loaded is not None:
            return self._loaded
        out: list[tuple[str, dict]] = []
        if self.directory.is_dir():
            for p in sorted(self.directory.rglob("*.json")):
                try:
                    data = json.loads(p.read_text())
                except (json.JSONDecodeError, OSError):
                    continue
                if not isinstance(data, dict):
                    continue
                if "semanticSegments" in data or "rawSignals" in data:
                    out.append(("timeline", data))
                elif "locations" in data:
                    out.append(("records", data))
                elif "timelineObjects" in data:
                    out.append(("semantic", data))
        self._loaded = out
        return out

    # -- points ----------------------------------------------------------------------------

    def _offset(self, t: datetime) -> int | None:
        off = t.utcoffset()
        if off is not None and t.tzinfo is not timezone.utc:
            return int(off.total_seconds())
        return self.offset_at(t) if self.offset_at else None

    def _fix(self, t: datetime, lat: float, lon: float, ref: str, label: str | None = None) -> TrailPoint:
        return TrailPoint(t, lat, lon, SOURCE_PATH, tzoffset=self._offset(t), label=label, ref=ref)

    def _stay(self, start: datetime, end: datetime | None, lat: float, lon: float, label: str | None, ref: str,
              window: Window) -> list[TrailPoint]:
        end = end or start
        if end < window.start or start > window.end:
            return []
        out, t = [], start
        while True:
            if window.contains(t):
                out.append(TrailPoint(t, lat, lon, SOURCE_VISIT, tzoffset=self._offset(t), label=label, ref=ref))
            if t >= end:
                break
            t = min(t + self.visit_step, end)
        return out

    def _subsample(self, pts: list[TrailPoint]) -> list[TrailPoint]:
        out: list[TrailPoint] = []
        last: dict[str, datetime] = {}
        for p in sorted(pts, key=lambda p: p.time):
            key = p.source
            if key == SOURCE_PATH and key in last and p.time - last[key] < self.step:
                continue
            last[key] = p.time
            out.append(p)
        return out

    def trail_points(self, window: Window) -> list[TrailPoint]:
        pts: list[TrailPoint] = []
        for shape, data in self.files():
            if shape == "timeline":
                pts += self._from_timeline(data, window)
            elif shape == "records":
                pts += self._from_records(data, window)
            else:
                pts += self._from_semantic(data, window)
        return self._subsample(pts)

    def constraints(self) -> list[Constraint]:
        return []

    # -- the three shapes --------------------------------------------------------------------

    def _from_timeline(self, data: dict, window: Window) -> list[TrailPoint]:
        labels = {}
        for fp in (data.get("userLocationProfile") or {}).get("frequentPlaces", []) or []:
            if fp.get("placeId") and fp.get("label"):
                labels[fp["placeId"]] = str(fp["label"]).title()
        out: list[TrailPoint] = []
        for i, seg in enumerate(data.get("semanticSegments", []) or []):
            start, end = parse_time(seg.get("startTime")), parse_time(seg.get("endTime"))
            if start is None:
                continue
            if end is not None and (end < window.start or start > window.end):
                continue
            ref = f"segment {i}"
            if "timelinePath" in seg:
                for pt in seg["timelinePath"] or []:
                    ll, t = parse_latlng(pt.get("point")), parse_time(pt.get("time"))
                    if ll and t and window.contains(t):
                        out.append(self._fix(t, *ll, ref))
            if "visit" in seg:
                v = seg["visit"] or {}
                cand = v.get("topCandidate") or {}
                ll = parse_latlng((cand.get("placeLocation") or {}).get("latLng"))
                if ll:
                    kind = cand.get("semanticType") or ""
                    label = labels.get(cand.get("placeId")) or (kind.title() if kind and kind not in ("UNKNOWN", "INFERRED_OTHER") else None)
                    out += self._stay(start, end, *ll, label, ref, window)
            if "activity" in seg:
                a = seg["activity"] or {}
                for key, t in (("start", start), ("end", end)):
                    ll = parse_latlng(((a.get(key) or {}).get("latLng")))
                    if ll and t and window.contains(t):
                        out.append(self._fix(t, *ll, ref, ((a.get("topCandidate") or {}).get("type") or "").title() or None))
        for sig in data.get("rawSignals", []) or []:
            pos = sig.get("position")
            if not pos:
                continue
            ll, t = parse_latlng(pos.get("LatLng") or pos.get("latLng")), parse_time(pos.get("timestamp"))
            acc = pos.get("accuracyMeters")
            if ll and t and window.contains(t) and (acc is None or float(acc) <= MAX_ACCURACY_M):
                out.append(self._fix(t, *ll, "raw"))
        return out

    def _from_records(self, data: dict, window: Window) -> list[TrailPoint]:
        out = []
        for i, loc in enumerate(data.get("locations", []) or []):
            ll, t = _e7(loc, "latitudeE7", "longitudeE7"), parse_time(loc.get("timestamp") or loc.get("timestampMs"))
            acc = loc.get("accuracy")
            if ll and t and window.contains(t) and (acc is None or float(acc) <= MAX_ACCURACY_M):
                out.append(self._fix(t, *ll, f"records {i}"))
        return out

    def _from_semantic(self, data: dict, window: Window) -> list[TrailPoint]:
        out: list[TrailPoint] = []
        for i, obj in enumerate(data.get("timelineObjects", []) or []):
            if "placeVisit" in obj:
                pv = obj["placeVisit"] or {}
                loc = pv.get("location") or {}
                ll = _e7(loc, "latitudeE7", "longitudeE7")
                dur = pv.get("duration") or {}
                start, end = parse_time(dur.get("startTimestamp")), parse_time(dur.get("endTimestamp"))
                if ll and start:
                    out += self._stay(start, end, *ll, loc.get("name") or loc.get("address"), f"visit {i}", window)
            if "activitySegment" in obj:
                seg = obj["activitySegment"] or {}
                dur = seg.get("duration") or {}
                start, end = parse_time(dur.get("startTimestamp")), parse_time(dur.get("endTimestamp"))
                kind = (seg.get("activityType") or "").title() or None
                for key, t in (("startLocation", start), ("endLocation", end)):
                    ll = _e7(seg.get(key) or {}, "latitudeE7", "longitudeE7")
                    if ll and t and window.contains(t):
                        out.append(self._fix(t, *ll, f"activity {i}", kind))
                wps = ((seg.get("waypointPath") or {}).get("waypoints") or [])
                if wps and start and end and len(wps) > 1:
                    span = (end - start) / (len(wps) - 1)
                    for k, wp in enumerate(wps):
                        ll = _e7(wp, "latE7", "lngE7")
                        t = start + span * k
                        if ll and window.contains(t):
                            out.append(self._fix(t, *ll, f"activity {i}", kind))
        return out
