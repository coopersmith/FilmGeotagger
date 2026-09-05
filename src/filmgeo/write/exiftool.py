"""The write step: confirmed assignments become an exiftool argfile, and the argfile becomes tags.

Everything the engine has decided lives in `.filmgeo/assignments/<roll>.json` (the solved
proposal, with `status` per frame) and `.filmgeo/facts/<roll>.json` (camera, film, lab). This
module turns the *confirmed* frames of a roll into one `-@` argfile with the tag set PLAN.md
specifies and M0 proved (docs/m0-findings.md), shows the user what it would write, and runs
exiftool over the scan folder.

Rules that came out of M0 and are load-bearing here:

* Never `-overwrite_original`: exiftool keeps `<name>_original` beside every file it touches
  (COO-128 adds a folder copy on top, read-back verification and restore).
* `FileModifyDate` is set to an explicit value *with the capture offset*, not copied from
  `DateTimeOriginal`: the copy operator reads the file as it was before the command and
  silently does nothing on a scan with no EXIF, and the copy would take the Mac's offset.
* exiftool exits 0 on warnings ("Not an integer", "No writable tags set"), so stderr is kept
  and shown, never discarded.
* Keywords follow the user's own convention — plain `Film`, `Mamiya 7II`, `Kodak Portra 400`,
  `Richard Photo Lab` — and the `filmgeo:` prefix is machine provenance only, which is what
  `clear` (COO-128) removes.
* Only frames whose `status` is `confirmed` are written; skipped frames never are. The review
  UI is where confirmation happens; the writer refuses to guess.
"""

from __future__ import annotations

import json
import subprocess
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path

from filmgeo.config import DATA_DIR
from filmgeo.signals.user_facts import RollFacts

ASSIGNMENTS_DIR = DATA_DIR / "assignments"
WRITES_DIR = DATA_DIR / "writes"

FILM_KEYWORD = "Film"
PROVENANCE_PREFIX = "filmgeo:"
CONF_HIGH, CONF_MID = 0.8, 0.5

# Tags the engine trusts on read-back (Lightroom strips XMP-exif:DateTimeOriginal and resets
# FileModifyDate; both are still written, neither is load-bearing). -G1 names.
VERIFY_KEYS = ("ExifIFD:DateTimeOriginal", "ExifIFD:OffsetTimeOriginal", "XMP-photoshop:DateCreated")


class WriteError(Exception):
    pass


# -- what one frame gets ------------------------------------------------------------------------


def local_stamp(iso: str, tzoffset: int | None) -> tuple[str, str, datetime]:
    """An assignment's instant as exiftool's `YYYY:MM:DD HH:MM:SS` wall clock and `+HH:MM` offset.

    The instant is rendered in the frame's own offset (what the trail said the user's clock
    was), not the ISO string's zone — a UTC-dated camera in the pool must not leak UTC into a
    scan shot in New York.
    """
    t = datetime.fromisoformat(iso)
    off = tzoffset if tzoffset is not None else int((t.utcoffset() or timedelta()).total_seconds())
    local = t.astimezone(timezone(timedelta(seconds=off))).replace(microsecond=0)
    sign = "+" if off >= 0 else "-"
    a = abs(off)
    return local.strftime("%Y:%m:%d %H:%M:%S"), f"{sign}{a // 3600:02d}:{(a % 3600) // 60:02d}", local


def split_camera(camera: str | None) -> tuple[str | None, str | None]:
    """"Mamiya 7II" -> ("Mamiya", "7II"). A one-word name is the model; None stays None."""
    if not camera or not camera.strip():
        return None, None
    make, _, model = camera.strip().partition(" ")
    return (make, model.strip()) if model.strip() else (None, make)


def provenance(source: str, confidence: float, has_location: bool) -> list[str]:
    kind = {"anchored": "anchored", "locked": "manual", "interpolated": "interpolated"}.get(source, source)
    band = "high" if confidence >= CONF_HIGH else "medium" if confidence >= CONF_MID else "low"
    out = [f"{PROVENANCE_PREFIX}{kind}", f"{PROVENANCE_PREFIX}conf:{band}"]
    if not has_location:
        out.append(f"{PROVENANCE_PREFIX}location-unknown")
    return out


@dataclass
class FrameWrite:
    number: int
    path: Path
    local: str                    # YYYY:MM:DD HH:MM:SS
    offset: str                   # +HH:MM
    instant: datetime
    lat: float | None
    lon: float | None
    keywords: list[str]
    make: str | None
    model: str | None
    source: str
    confidence: float
    anchor_uuid: str | None
    interval: tuple[str, str]
    current: str | None = None    # DateTimeOriginal already in the file, if any
    stale: list[str] = field(default_factory=list)   # filmgeo: keywords already in the file, to replace

    @property
    def xmp_stamp(self) -> str:
        return f"{self.local.replace(':', '-', 2).replace(' ', 'T')}{self.offset}"

    def args(self) -> list[str]:
        """The exiftool arguments for this file, one per line in the argfile."""
        a = [
            f"-EXIF:DateTimeOriginal={self.local}",
            f"-EXIF:CreateDate={self.local}",
            f"-EXIF:OffsetTimeOriginal={self.offset}",
            f"-EXIF:OffsetTimeDigitized={self.offset}",
            f"-EXIF:OffsetTime={self.offset}",
            f"-XMP-exif:DateTimeOriginal={self.xmp_stamp}",
            f"-XMP-photoshop:DateCreated={self.xmp_stamp}",
            f"-XMP-xmp:CreateDate={self.xmp_stamp}",
            f"-FileModifyDate={self.local}{self.offset}",
        ]
        if self.lat is not None and self.lon is not None:
            a += [
                f"-EXIF:GPSLatitude={abs(self.lat)}",
                f"-EXIF:GPSLatitudeRef={'N' if self.lat >= 0 else 'S'}",
                f"-EXIF:GPSLongitude={abs(self.lon)}",
                f"-EXIF:GPSLongitudeRef={'E' if self.lon >= 0 else 'W'}",
                f"-XMP-exif:GPSLatitude={self.lat}",
                f"-XMP-exif:GPSLongitude={self.lon}",
            ]
        # Keywords: drop the provenance a previous write left (the user's own tags are never
        # touched), then remove-and-add each of ours — exiftool's idiom for "add if absent",
        # since a bare += duplicates an item that is already in the list (measured).
        for tag in ("XMP-dc:Subject", "IPTC:Keywords"):
            a += [f"-{tag}-={k}" for k in self.stale if k not in self.keywords]
            for k in self.keywords:
                a += [f"-{tag}-={k}", f"-{tag}+={k}"]
        if self.make:
            a.append(f"-EXIF:Make={self.make}")
        if self.model:
            a.append(f"-EXIF:Model={self.model}")
        a.append(str(self.path))
        return a


@dataclass
class Skipped:
    number: int
    path: Path | None
    why: str                      # "not confirmed" | "skipped" | "no file" | "unchanged"


@dataclass
class WritePlan:
    roll: str
    folder: Path
    frames: list[FrameWrite]
    skipped: list[Skipped] = field(default_factory=list)

    @property
    def n_total(self) -> int:
        return len(self.frames) + len(self.skipped)

    def argfile_text(self) -> str:
        """exiftool `-@` format: one argument per line, `-execute` between files, one process."""
        lines = ["# filmgeo write plan for roll " + self.roll, "# generated " + datetime.now().astimezone().isoformat(timespec="seconds"),
                 "# originals are kept: exiftool leaves <name>_original beside each file it touches", "-charset", "filename=utf8"]
        for i, f in enumerate(self.frames):
            if i:
                lines.append("-execute")
            lines.extend(f.args())
        return "\n".join(lines) + "\n"


# -- building the plan ---------------------------------------------------------------------------


def load_assignments(key: str, directory: Path = ASSIGNMENTS_DIR) -> dict:
    p = directory / f"{key}.json"
    if not p.exists():
        raise WriteError(f"no assignments for {key!r}: run `filmgeo align` or review it in the UI first")
    return json.loads(p.read_text())


def scan_files(folder: Path) -> dict[int, Path]:
    """Frame number -> file, in the same natural order the pipeline used."""
    from filmgeo.scans.ingest import ingest

    return {f.number: f.path for f in ingest(folder).frames}


def plan(key: str, folder: Path | None = None, assignments: dict | None = None, facts: RollFacts | None = None,
         files: dict[int, Path] | None = None, current: dict[int, "Current"] | None = None,
         written: dict[int, dict] | None = None, force: bool = False) -> WritePlan:
    """What would be written for a roll: its confirmed frames, and why the rest are left alone.

    `folder` defaults to the assignments' `origin`; a roll whose origin is a hand-tagged key
    has its frames inside the Photos library and cannot be written to. `current` is what the
    files say now (`current_tags`), for the preview and for replacing stale provenance;
    `written` is the sidecar's record, so a frame already written as it stands is left alone
    unless `force`.
    """
    from filmgeo.write import sidecar

    a = assignments if assignments is not None else load_assignments(key)
    facts = facts or RollFacts.load(key)
    if folder is None:
        origin = Path(str(a.get("origin") or "")).expanduser()
        if not origin.is_dir():
            raise WriteError(f"{key} was aligned from {a.get('origin')!r}, which is not a scan folder — "
                             "its frames live in the Photos library. Point --folder at the scans to write them.")
        folder = origin
    files = files if files is not None else scan_files(folder)
    make, model = split_camera(facts.camera)
    descriptive = [FILM_KEYWORD, *(v.strip() for v in (facts.camera, facts.film, facts.lab) if v and v.strip())]

    frames: list[FrameWrite] = []
    skipped: list[Skipped] = []
    for fr in a.get("frames", []):
        n = fr["number"]
        path = files.get(n)
        if path is None:
            skipped.append(Skipped(n, None, "no file"))
            continue
        if fr.get("source") == "skipped":
            skipped.append(Skipped(n, path, "skipped"))
            continue
        if fr.get("status") != "confirmed":
            skipped.append(Skipped(n, path, "not confirmed"))
            continue
        local, offset, instant = local_stamp(fr["time"], fr.get("tzoffset"))
        has_loc = fr.get("lat") is not None and fr.get("lon") is not None
        fw = FrameWrite(
            number=n, path=path, local=local, offset=offset, instant=instant,
            lat=fr.get("lat") if has_loc else None, lon=fr.get("lon") if has_loc else None,
            keywords=[*descriptive, *provenance(fr["source"], float(fr.get("confidence", 0.0)), has_loc)],
            make=make, model=model, source=fr["source"], confidence=float(fr.get("confidence", 0.0)),
            anchor_uuid=fr.get("anchor_uuid"), interval=(fr["t_lo"], fr["t_hi"]),
            current=(current or {}).get(n, Current()).date, stale=(current or {}).get(n, Current()).provenance,
        )
        if not force and sidecar.unchanged(fw, (written or {}).get(n)):
            skipped.append(Skipped(n, path, "unchanged"))
            continue
        frames.append(fw)
    return WritePlan(key, folder, frames, skipped)


# -- exiftool ------------------------------------------------------------------------------------


def exiftool(args: list[str]) -> tuple[str, str]:
    """Run exiftool; return (stdout, stderr). Raise on a non-zero exit, keep warnings for the caller."""
    try:
        p = subprocess.run(["exiftool", *args], capture_output=True, text=True)
    except FileNotFoundError:
        raise WriteError("exiftool not found — `brew install exiftool`")
    if p.returncode != 0:
        raise WriteError(f"exiftool failed: {p.stderr.strip() or p.stdout.strip()}")
    return p.stdout, p.stderr


def read_tags(paths: list[Path]) -> dict[Path, dict]:
    """`-j -n -G1` for every file: the only form whose keys match what verification expects (M0)."""
    if not paths:
        return {}
    out, _ = exiftool(["-j", "-n", "-G1", *[str(p) for p in paths]])
    rows = json.loads(out or "[]")
    return {Path(r["SourceFile"]).resolve(): r for r in rows}


@dataclass
class Current:
    """What a scan file says before the write: its date, and any provenance an earlier write left."""

    date: str | None = None
    provenance: list[str] = field(default_factory=list)


def current_tags(files: dict[int, Path]) -> dict[int, Current]:
    tags = read_tags(list(files.values()))
    out = {}
    for n, p in files.items():
        t = tags.get(p.resolve()) or {}
        subj = t.get("XMP-dc:Subject") or []
        kw = t.get("IPTC:Keywords") or []
        seen = [x for x in ([subj] if isinstance(subj, str) else subj) + ([kw] if isinstance(kw, str) else kw)]
        out[n] = Current(t.get("ExifIFD:DateTimeOriginal"), sorted({k for k in seen if str(k).startswith(PROVENANCE_PREFIX)}))
    return out


def save_argfile(p: WritePlan, directory: Path = WRITES_DIR) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"{p.roll}.args"
    path.write_text(p.argfile_text())
    return path


def apply(argfile: Path) -> list[str]:
    """Run the argfile. Returns exiftool's warnings, one per line, which the caller must show."""
    out, err = exiftool(["-@", str(argfile)])
    warnings = [line.strip() for line in err.splitlines() if line.strip()]
    return warnings + [line.strip() for line in out.splitlines() if "error" in line.lower()]
