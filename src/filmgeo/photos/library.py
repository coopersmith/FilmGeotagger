"""Apple Photos adapter.

`osxphotos.PhotosDB()` parses the entire library — about 50 s warm and over ten minutes cold on
a 5 GB `Photos.sqlite`, with no way to load only a date window (measured in M0). So everything
the engine needs per asset is pulled once and cached; callers then filter the cache.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path

from filmgeo.config import DATA_DIR

CACHE = DATA_DIR / "library.json"


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
        """A scan the user has already hand-tagged. Ground truth, and never a candidate."""
        return any(k.lower() == "film" for k in self.keywords)

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

    Film scans are excluded: they live in the same library, and a roll must never be matched
    against itself or against another roll's frames. Assets without a local derivative stay out
    of the visual pool but remain usable as time and GPS trail points (PLAN.md).
    """
    lo = start - timedelta(days=pad_days)
    hi = end + timedelta(days=pad_days)
    return [a for a in assets if lo <= a.date <= hi and not a.is_film and a.derivative]
