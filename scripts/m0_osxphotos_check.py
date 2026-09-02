#!/usr/bin/env python3
"""M0: confirm osxphotos exposes what the engine needs under Optimize Mac Storage.

Usage:
    python scripts/m0_osxphotos_check.py [--days 30] [--limit 40]

Prints, for a sample of recent photos: date, tz offset, lat/lon, whether the original is
local, and the largest local derivative (path + pixel size). Ends with the fraction of
assets that have a local derivative, which is what matching will run on.
Requires: `uv sync` (osxphotos, pillow) and Full Disk Access for your terminal.
"""

from __future__ import annotations

import argparse
import statistics
from datetime import datetime, timedelta, timezone

from PIL import Image

import osxphotos


def largest_derivative(photo):
    best = None
    for p in photo.path_derivatives or []:
        try:
            with Image.open(p) as im:
                w, h = im.size
        except OSError:
            continue
        if best is None or w * h > best[1] * best[2]:
            best = (p, w, h)
    return best


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int, default=30)
    ap.add_argument("--limit", type=int, default=40)
    args = ap.parse_args()

    since = datetime.now(timezone.utc) - timedelta(days=args.days)
    db = osxphotos.PhotosDB()
    photos = [
        p
        for p in db.photos(from_date=since)
        if not p.ismovie and not p.screenshot and not p.intrash and not p.hidden
    ]
    photos.sort(key=lambda p: p.date)
    print(f"{len(photos)} still photos in the last {args.days} days (library: {db.library_path})\n")

    have_deriv = 0
    long_edges = []
    for p in photos[: args.limit]:
        d = largest_derivative(p)
        lat, lon = (p.location or (None, None))
        off = p.tzoffset / 3600 if p.tzoffset is not None else None
        print(
            f"{p.date:%Y-%m-%d %H:%M:%S}  off={off!s:>5}h  "
            f"lat={lat!s:>10} lon={lon!s:>11}  original_local={not p.ismissing!s:<5}  "
            f"deriv={'none' if d is None else f'{d[1]}x{d[2]}'}"
        )
    for p in photos:
        d = largest_derivative(p)
        if d is not None:
            have_deriv += 1
            long_edges.append(max(d[1], d[2]))

    if photos:
        print(
            f"\nLocal derivative available: {have_deriv}/{len(photos)} "
            f"({100 * have_deriv / len(photos):.0f}%)"
        )
    if long_edges:
        print(f"Median derivative long edge: {statistics.median(long_edges):.0f} px")
    print("\nCheck one travel-day photo above against what Photos shows for its time and zone.")


if __name__ == "__main__":
    main()
