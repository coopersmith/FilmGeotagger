#!/usr/bin/env python3
"""M0: write the full PLAN.md tag set to copies of a few scans and verify it reads back.

Usage:
    python scripts/m0_exiftool_roundtrip.py path/to/scan1.jpg path/to/scan2.tif ...

Copies each input into scripts/out/, writes date + offset + GPS + provenance keywords
with exiftool, reads them back with `exiftool -j -n`, and prints PASS/FAIL per file.
Leaves the `_original` backups in place so you can confirm restore works:
    exiftool -restore_original scripts/out/*
Requires: `brew install exiftool`.
"""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
from pathlib import Path

OUT = Path(__file__).resolve().parent / "out"

# Sample capture moment: Lisbon, 14 July 2026, 15:32:10 local (UTC+01:00).
LOCAL = "2026:07:14 15:32:10"
ISO = "2026-07-14T15:32:10+01:00"
OFFSET = "+01:00"
LAT, LON = 38.7223, -9.1393
KEYWORDS = ["filmgeo:anchored", "filmgeo:conf:high"]

WRITE_ARGS = [
    f"-EXIF:DateTimeOriginal={LOCAL}",
    f"-EXIF:CreateDate={LOCAL}",
    f"-EXIF:OffsetTimeOriginal={OFFSET}",
    f"-EXIF:OffsetTimeDigitized={OFFSET}",
    f"-EXIF:OffsetTime={OFFSET}",
    f"-XMP-exif:DateTimeOriginal={ISO}",
    f"-XMP-photoshop:DateCreated={ISO}",
    f"-XMP-xmp:CreateDate={ISO}",
    f"-EXIF:GPSLatitude={LAT}",
    "-EXIF:GPSLatitudeRef=N",
    f"-EXIF:GPSLongitude={LON}",
    "-EXIF:GPSLongitudeRef=W",
    f"-XMP-exif:GPSLatitude={LAT}",
    f"-XMP-exif:GPSLongitude={LON}",
    *[f"-XMP-dc:Subject+={k}" for k in KEYWORDS],
    *[f"-IPTC:Keywords+={k}" for k in KEYWORDS],
    "-FileModifyDate<DateTimeOriginal",
]

CHECKS = {
    "EXIF:DateTimeOriginal": LOCAL,
    "EXIF:OffsetTimeOriginal": OFFSET,
    "XMP-photoshop:DateCreated": ISO,
    "Composite:GPSLatitude": LAT,
    "Composite:GPSLongitude": LON,
}


def read(path: Path) -> dict:
    out = subprocess.run(
        ["exiftool", "-j", "-n", "-G1", str(path)], check=True, capture_output=True, text=True
    ).stdout
    return json.loads(out)[0]


def close(a, b) -> bool:
    if isinstance(a, float) or isinstance(b, float):
        try:
            return abs(float(a) - float(b)) < 1e-4
        except (TypeError, ValueError):
            return False
    return a == b


def main(paths: list[str]) -> int:
    if not paths:
        print(__doc__)
        return 2
    if shutil.which("exiftool") is None:
        print("exiftool not found. brew install exiftool")
        return 2
    OUT.mkdir(exist_ok=True)
    failures = 0
    for src in map(Path, paths):
        dst = OUT / src.name
        shutil.copy2(src, dst)
        subprocess.run(["exiftool", *WRITE_ARGS, str(dst)], check=True, capture_output=True)
        tags = read(dst)
        problems = []
        for key, want in CHECKS.items():
            got = tags.get(key)
            if not close(got, want):
                problems.append(f"{key}: want {want!r}, got {got!r}")
        subjects = tags.get("XMP-dc:Subject") or []
        if isinstance(subjects, str):
            subjects = [subjects]
        for k in KEYWORDS:
            if k not in subjects:
                problems.append(f"XMP-dc:Subject missing {k}")
        if not (OUT / f"{src.name}_original").exists():
            problems.append("no _original backup written")
        status = "PASS" if not problems else "FAIL"
        failures += bool(problems)
        print(f"{status}  {dst}")
        for p in problems:
            print(f"      {p}")
    print(f"\nTagged copies are in {OUT}. Import them into Photos and open the folder in Lightroom.")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
