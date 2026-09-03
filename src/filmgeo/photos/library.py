"""Apple Photos adapter.

`osxphotos.PhotosDB()` parses the entire library — about 50 s warm and over ten minutes cold on
a 5 GB `Photos.sqlite`, with no way to load only a date window (measured in M0). So everything
the engine needs per asset is pulled once and cached; callers then filter the cache.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path

from filmgeo.config import DATA_DIR

CACHE = DATA_DIR / "library.json"

# Lab filename conventions seen in this library; both encode roll then frame number.
LAB_FILENAME = (
    re.compile(r"^(\d{6})_(\d{4})\.jpe?g$", re.I),      # Richard Photo Lab: 874466_0012.jpg
    re.compile(r"^(\d{8})(\d{4})\.jpe?g$", re.I),       # Indie Film Lab:    000070400016.jpg
)
# Scanner bodies that leave Make/Model in the file. Measured 3 Sept 2026: 248 tagged film
# assets and 81 of the 115 untagged scan copies carry NORITSU KOKI / EZ Controller.
SCANNER_MAKES = {"NORITSU KOKI"}


def lab_key(filename: str) -> tuple[str, int] | tuple[None, None]:
    """(roll, frame) from a lab filename, tolerating Photos' export hash and `_Original`."""
    name = re.sub(r"^[0-9a-f]{8}-", "", filename or "")
    name = re.sub(r"^\d{8}-", "", name)                       # 20200820-354581_0006
    name = re.sub(r"_Original(\.jpe?g)$", r"\1", name, flags=re.I)
    for pat in LAB_FILENAME:
        if m := pat.match(name):
            return m.group(1), int(m.group(2))
    return None, None


@dataclass
class Asset:
    uuid: str
    filename: str
    date: datetime
    tzoffset: int | None
    lat: float | None
    lon: float | None
    keywords: list[str] = field(default_factory=list)
    derivative: str | None = None
    make: str | None = None
    model: str | None = None

    @property
    def is_film(self) -> bool:
        """A scan the user has already hand-tagged with the `Film` keyword: the ground truth."""
        return any(k.lower() == "film" for k in self.keywords)

    @property
    def is_scan(self) -> bool:
        """Any film scan at all, tagged or not — never a candidate, never a trail point.

        The library holds 115 untagged copies of scans (measured 3 Sept 2026), most at the same
        instant as the tagged frame. Filtering on the keyword alone let them into the candidate
        pool, where a frame "matched" its own duplicate: that inflated the anchored ground truth
        from 35 frames to 113 and M1's recall with it. A scan is recognised by the keyword, by
        a lab filename, or by a scanner make.
        """
        return self.is_film or lab_key(self.filename)[0] is not None or (self.make or "") in SCANNER_MAKES

    @property
    def has_location(self) -> bool:
        return self.lat is not None and self.lon is not None


def _largest_derivative(photo) -> str | None:
    """Biggest local preview by file size — a proxy for pixels that avoids opening each file.

    Under Optimize Mac Storage every recent asset had one, topping out at 1024 px (M0).
    """
    paths = photo.path_derivatives or []
    if not paths:
        return None
    return max(paths, key=lambda p: Path(p).stat().st_size if Path(p).exists() else 0)


def build_cache(path: Path = CACHE) -> int:
    """Read the whole library once and write the fields the engine needs."""
    import osxphotos

    path.parent.mkdir(parents=True, exist_ok=True)
    rows = []
    for p in osxphotos.PhotosDB().photos():
        if p.intrash or p.hidden or p.date is None:
            continue
        lat, lon = p.location or (None, None)
        exif = p.exif_info
        rows.append(
            {
                "uuid": p.uuid,
                "filename": p.original_filename or "",
                "date": p.date.isoformat(),
                "tzoffset": p.tzoffset,
                "lat": lat,
                "lon": lon,
                "keywords": p.keywords or [],
                "derivative": None if (p.ismovie or p.screenshot) else _largest_derivative(p),
                "make": exif.camera_make if exif else None,
                "model": exif.camera_model if exif else None,
                "ismovie": p.ismovie,
                "screenshot": p.screenshot,
            }
        )
    path.write_text(json.dumps(rows))
    return len(rows)


def load(path: Path = CACHE) -> list[Asset]:
    if not path.exists():
        raise FileNotFoundError(f"no library cache at {path} — run `filmgeo index` first")
    out = []
    for r in json.loads(path.read_text()):
        if r["ismovie"] or r["screenshot"]:
            continue
        out.append(
            Asset(
                uuid=r["uuid"],
                filename=r["filename"],
                date=datetime.fromisoformat(r["date"]),
                tzoffset=r["tzoffset"],
                lat=r["lat"],
                lon=r["lon"],
                keywords=r["keywords"],
                derivative=r["derivative"],
                make=r["make"],
                model=r["model"],
            )
        )
    out.sort(key=lambda a: a.date)
    return out


def candidates(assets: list[Asset], start: datetime, end: datetime, pad_days: int = 0) -> list[Asset]:
    """Assets usable as visual match candidates in a window.

    Film scans are excluded, tagged or not (`is_scan`): they live in the same library, and a
    roll must never be matched against itself, its own untagged copy, or another roll's frames.
    Assets without a local derivative stay out of the visual pool but remain usable as time and
    GPS trail points (PLAN.md).
    """
    lo = start - timedelta(days=pad_days)
    hi = end + timedelta(days=pad_days)
    return [a for a in assets if lo <= a.date <= hi and not a.is_scan and a.derivative]


def phone_times(assets: list[Asset]) -> "np.ndarray":
    """Sorted timestamps of everything that is not a scan — what `Roll.anchored()` tests against."""
    import numpy as np

    return np.sort(np.array([a.date.timestamp() for a in assets if not a.is_scan]))
