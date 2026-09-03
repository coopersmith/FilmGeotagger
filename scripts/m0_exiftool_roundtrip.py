#!/usr/bin/env python3
"""M0: write the full PLAN.md tag set to copies of a few scans and verify it reads back.

Usage:
    python scripts/m0_exiftool_roundtrip.py [options] scan1.jpg scan2.tif ...

Options:
    --datetime "YYYY:MM:DD HH:MM:SS"   capture time to write (default 2026:07:14 15:32:10)
    --offset   "+HH:MM"                capture UTC offset (default +01:00)
    --camera   "Make Model"            camera identity (default "Contax T2"); "" writes none
    --film     "Portra 400"            film stock, written as a keyword; "" writes none
    --iso      400                     speed the roll was shot at, written to EXIF:ISO

Copies each input into scripts/out/, writes date + offset + GPS + camera + provenance keywords
with exiftool, reads them back with `exiftool -j -n -G1`, and prints PASS/FAIL per file.
Also verifies that `-restore_original` returns byte-identical originals, on throwaway copies,
so the tagged files in scripts/out/ stay ready for the Photos and Lightroom checks.

`--datetime` exists for the delete-then-reimport check (COO-105): re-run with a different day,
re-import, and see whether Photos reads the new date or silently keeps the old one.

Requires: `brew install exiftool`.

Two exiftool details this script exists to pin down:
  * `-FileModifyDate<DateTimeOriginal` reads its source from the file as it was *before*
    this command's assignments, so on a scan with no EXIF it warns "No writable tags set"
    and silently leaves the mtime alone. It has to be a second pass, after the tags land.
  * Read-back must use -G1 group names (ExifIFD:, GPS:, XMP-exif:) — the -G0 names used
    when writing (EXIF:) do not appear as keys in a -G1 dump.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

OUT = Path(__file__).resolve().parent / "out"

# Sample capture moment: Lisbon, 14 July 2026, 15:32:10 local (UTC+01:00).
DEFAULT_LOCAL = "2026:07:14 15:32:10"
DEFAULT_OFFSET = "+01:00"
# Film scans carry no camera identity, so neither Photos nor Lightroom can filter a batch by
# body. The roll's camera name is already a per-roll user fact in PLAN.md; writing it into
# EXIF:Make/Model makes that filtering work. Proven here so M4 can rely on it.
DEFAULT_CAMERA = "Contax T2"
# Film stock is a per-roll user fact with nowhere canonical to live in EXIF. The speed goes to
# EXIF:ISO (which Photos and Lightroom both display, and which is otherwise blank on a scan);
# the stock name goes to a `filmgeo:film:` keyword so it stays searchable and the clear command
# can remove it with the rest of our provenance. If the roll was pushed or pulled, --iso is the
# speed it was actually shot at, which is what EXIF:ISO means.
DEFAULT_FILM = "Portra 400"
DEFAULT_ISO = 400

LAT, LON = 38.7223, -9.1393
BASE_KEYWORDS = ["filmgeo:anchored", "filmgeo:conf:high"]


def keywords(film: str) -> list[str]:
    return [*BASE_KEYWORDS, f"filmgeo:film:{film.strip()}"] if film.strip() else list(BASE_KEYWORDS)


def split_camera(camera: str) -> tuple[str, str]:
    """"Contax T2" -> ("Contax", "T2"). A one-word value is treated as the model."""
    make, _, model = camera.strip().partition(" ")
    return (make, model.strip()) if model.strip() else ("", make)


def write_args(local: str, offset: str, camera: str, film: str, iso: int | None) -> list[str]:
    xmp_stamp = f"{local.replace(':', '-', 2).replace(' ', 'T')}{offset}"
    args = [
        f"-EXIF:DateTimeOriginal={local}",
        f"-EXIF:CreateDate={local}",
        f"-EXIF:OffsetTimeOriginal={offset}",
        f"-EXIF:OffsetTimeDigitized={offset}",
        f"-EXIF:OffsetTime={offset}",
        f"-XMP-exif:DateTimeOriginal={xmp_stamp}",
        f"-XMP-photoshop:DateCreated={xmp_stamp}",
        f"-XMP-xmp:CreateDate={xmp_stamp}",
        f"-EXIF:GPSLatitude={LAT}",
        "-EXIF:GPSLatitudeRef=N",
        f"-EXIF:GPSLongitude={LON}",
        "-EXIF:GPSLongitudeRef=W",
        f"-XMP-exif:GPSLatitude={LAT}",
        f"-XMP-exif:GPSLongitude={LON}",
        *[f"-XMP-dc:Subject+={k}" for k in keywords(film)],
        *[f"-IPTC:Keywords+={k}" for k in keywords(film)],
    ]
    if iso:
        args.append(f"-EXIF:ISO={iso}")
    make, model = split_camera(camera)
    if make:
        args.append(f"-EXIF:Make={make}")
    if model:
        args.append(f"-EXIF:Model={model}")
    return args


# Second pass: the source tag has to exist in the file before it can be copied.
FILEMODIFY_ARGS = ["-FileModifyDate<DateTimeOriginal"]

LIST_CHECKS = ["XMP-dc:Subject", "IPTC:Keywords"]


def checks(local: str, offset: str, camera: str, iso: int | None) -> dict:
    """Keys are -G1 group names, values as read back with -n (numeric) -j (json)."""
    stamp = f"{local}{offset}"
    expected = {
        "ExifIFD:DateTimeOriginal": local,
        "ExifIFD:CreateDate": local,
        "ExifIFD:OffsetTimeOriginal": offset,
        "ExifIFD:OffsetTimeDigitized": offset,
        "ExifIFD:OffsetTime": offset,
        "XMP-exif:DateTimeOriginal": stamp,
        "XMP-photoshop:DateCreated": stamp,
        "XMP-xmp:CreateDate": stamp,
        # EXIF stores GPS unsigned with a hemisphere ref; XMP and Composite are signed.
        "GPS:GPSLatitude": abs(LAT),
        "GPS:GPSLatitudeRef": "N",
        "GPS:GPSLongitude": abs(LON),
        "GPS:GPSLongitudeRef": "W",
        "XMP-exif:GPSLatitude": LAT,
        "XMP-exif:GPSLongitude": LON,
        "Composite:GPSLatitude": LAT,
        "Composite:GPSLongitude": LON,
    }
    make, model = split_camera(camera)
    if make:
        expected["IFD0:Make"] = make
    if model:
        expected["IFD0:Model"] = model
    if iso:
        expected["ExifIFD:ISO"] = iso
    return expected


def exiftool(args: list[str]) -> tuple[str, str]:
    """Run exiftool, returning (stdout, stderr). Warnings are the point, so keep them."""
    p = subprocess.run(["exiftool", *args], capture_output=True, text=True)
    if p.returncode != 0:
        raise RuntimeError(f"exiftool failed: {p.stderr.strip() or p.stdout.strip()}")
    return p.stdout, p.stderr


def read(path: Path) -> dict:
    out, _ = exiftool(["-j", "-n", "-G1", str(path)])
    return json.loads(out)[0]


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def close(got, want) -> bool:
    if isinstance(want, float):
        try:
            return abs(float(got) - want) < 1e-4
        except (TypeError, ValueError):
            return False
    return got == want


def check_restore(tagged: Path, source: Path, problems: list[str]) -> None:
    """`exiftool -restore_original` must give back the source bytes exactly."""
    backup = tagged.with_name(f"{tagged.name}_original")
    if not backup.exists():
        problems.append("no _original backup written")
        return
    with tempfile.TemporaryDirectory() as tmp:
        probe = Path(tmp) / tagged.name
        shutil.copy2(tagged, probe)
        shutil.copy2(backup, probe.with_name(f"{probe.name}_original"))
        exiftool(["-restore_original", str(probe)])
        if sha256(probe) != sha256(source):
            problems.append("restore_original did not reproduce the source bytes")


def tag_one(src: Path, local: str, offset: str, camera: str, film: str, iso: int | None) -> list[str]:
    dst = OUT / src.name
    backup = dst.with_name(f"{dst.name}_original")
    backup.unlink(missing_ok=True)  # exiftool refuses to overwrite a stale backup
    shutil.copy2(src, dst)

    problems: list[str] = []
    for args in (write_args(local, offset, camera, film, iso), FILEMODIFY_ARGS):
        _, err = exiftool([*args, str(dst)])
        for line in err.splitlines():
            if line.strip():
                problems.append(f"exiftool: {line.strip()}")

    tags = read(dst)
    for key, want in checks(local, offset, camera, iso).items():
        got = tags.get(key)
        if not close(got, want):
            problems.append(f"{key}: want {want!r}, got {got!r}")

    for key in LIST_CHECKS:
        values = tags.get(key) or []
        if isinstance(values, str):
            values = [values]
        for k in keywords(film):
            if k not in values:
                problems.append(f"{key} missing {k}")

    mtime = str(tags.get("System:FileModifyDate") or "")
    if not mtime.startswith(local):
        problems.append(f"System:FileModifyDate: want {local}..., got {mtime!r}")

    check_restore(dst, src, problems)
    return problems


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--datetime", dest="local", default=DEFAULT_LOCAL)
    ap.add_argument("--offset", default=DEFAULT_OFFSET)
    ap.add_argument("--camera", default=DEFAULT_CAMERA)
    ap.add_argument("--film", default=DEFAULT_FILM)
    ap.add_argument("--iso", type=int, default=DEFAULT_ISO)
    ap.add_argument("scans", nargs="*")
    args = ap.parse_args()

    if not args.scans:
        ap.print_help()
        return 2
    if shutil.which("exiftool") is None:
        print("exiftool not found. brew install exiftool")
        return 2

    OUT.mkdir(exist_ok=True)
    print(
        f"writing {args.local}{args.offset}  camera={args.camera or '(none)'}  "
        f"film={args.film or '(none)'}  iso={args.iso or '(none)'}\n"
    )
    failures = 0
    for src in map(Path, args.scans):
        problems = tag_one(src, args.local, args.offset, args.camera, args.film, args.iso)
        failures += bool(problems)
        print(f"{'PASS' if not problems else 'FAIL'}  {OUT / src.name}")
        for p in problems:
            print(f"      {p}")
    print(f"\nTagged copies are in {OUT}. Import them into Photos and open the folder in Lightroom.")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
