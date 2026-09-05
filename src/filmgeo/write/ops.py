"""The safety net around a write: backup first, verify after, record always, and two ways back.

* **Backup.** Before the first write, every scan in the plan is copied to
  `<folder>/.filmgeo_backup/`. A file already there is never overwritten, so the backup stays
  the pristine lab scan however many times the roll is re-written. exiftool's own
  `<name>_original` is kept too, but exiftool refuses to write a file whose `_original`
  already exists, so once the backup holds a file its stale `_original` is cleared before a
  re-write.
* **Verify.** Read back with `-j -n -G1` and compare the keys the engine trusts — Lightroom
  strips `XMP-exif:DateTimeOriginal` and resets `FileModifyDate`, so neither is checked
  (docs/m0-findings.md). GPS in EXIF is unsigned with a hemisphere ref, the XMP mirror signed.
* **Record.** Every write appends to `.filmgeo/writes/<roll>.json`: when, the argfile, and per
  frame what was written and whether it read back — the `writes` table PLAN.md describes.
* **Restore** puts the lab scan back: a byte copy from the backup folder where one exists —
  that is the pristine file — else `exiftool -restore_original`, whose `_original` is only
  the state before the *last* write and, after a re-write or a clear, is itself a written file.
* **Clear** removes the `filmgeo:` provenance keywords (the user's own tags stay), and with
  `everything=True` the written dates, offsets, GPS and Make/Model as well.
"""

from __future__ import annotations

import json
import shutil
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from filmgeo.write.exiftool import (PROVENANCE_PREFIX, WRITES_DIR, FrameWrite, WritePlan, WriteError, apply,
                                    current_tags, exiftool, read_tags, save_argfile)

BACKUP_DIR = ".filmgeo_backup"


# -- backup ----------------------------------------------------------------------------------------


def backup_dir(folder: Path) -> Path:
    return folder / BACKUP_DIR


def backup(p: WritePlan) -> list[Path]:
    """Copy each planned file into the backup folder unless a copy is already there. Returns the new copies."""
    d = backup_dir(p.folder)
    d.mkdir(exist_ok=True)
    copied = []
    for f in p.frames:
        dst = d / f.path.name
        if not dst.exists():
            shutil.copy2(f.path, dst)
            copied.append(dst)
    return copied


def clear_stale_originals(p: WritePlan) -> list[Path]:
    """Drop `<name>_original` for planned files that the backup already holds, so exiftool can write again."""
    d = backup_dir(p.folder)
    removed = []
    for f in p.frames:
        orig = f.path.with_name(f.path.name + "_original")
        if orig.exists() and (d / f.path.name).exists():
            orig.unlink()
            removed.append(orig)
    return removed


# -- verify ----------------------------------------------------------------------------------------


@dataclass
class Check:
    number: int
    file: str
    ok: bool
    problems: list[str] = field(default_factory=list)


def expected(f: FrameWrite) -> dict[str, object]:
    """-G1 keys and the values a correct write produces."""
    want: dict[str, object] = {
        "ExifIFD:DateTimeOriginal": f.local,
        "ExifIFD:OffsetTimeOriginal": f.offset,
        "XMP-photoshop:DateCreated": f"{f.local}{f.offset}",
    }
    if f.lat is not None and f.lon is not None:
        want.update({
            "GPS:GPSLatitude": abs(f.lat), "GPS:GPSLatitudeRef": "N" if f.lat >= 0 else "S",
            "GPS:GPSLongitude": abs(f.lon), "GPS:GPSLongitudeRef": "E" if f.lon >= 0 else "W",
            "XMP-exif:GPSLatitude": f.lat, "XMP-exif:GPSLongitude": f.lon,
        })
    if f.make:
        want["IFD0:Make"] = f.make
    if f.model:
        want["IFD0:Model"] = f.model
    return want


def _close(got, want) -> bool:
    if isinstance(want, float):
        try:
            return abs(float(got) - want) < 1e-4
        except (TypeError, ValueError):
            return False
    return got == want


def verify(p: WritePlan) -> list[Check]:
    tags = read_tags([f.path for f in p.frames])
    out = []
    for f in p.frames:
        t = tags.get(f.path.resolve()) or {}
        problems = [f"{k}: want {v!r}, got {t.get(k)!r}" for k, v in expected(f).items() if not _close(t.get(k), v)]
        for tag in ("XMP-dc:Subject", "IPTC:Keywords"):
            have = t.get(tag) or []
            have = [have] if isinstance(have, str) else have
            missing = [k for k in f.keywords if k not in have]
            if missing:
                problems.append(f"{tag} missing {missing}")
            dupes = sorted({k for k in have if have.count(k) > 1})
            if dupes:
                problems.append(f"{tag} duplicated {dupes}")
        out.append(Check(f.number, f.path.name, not problems, problems))
    return out


# -- record ----------------------------------------------------------------------------------------


def record_path(roll: str, directory: Path = WRITES_DIR) -> Path:
    return directory / f"{roll}.json"


def record(p: WritePlan, argfile: Path, checks: list[Check], warnings: list[str], directory: Path = WRITES_DIR) -> Path:
    """Append this write to the roll's log."""
    path = record_path(p.roll, directory)
    path.parent.mkdir(parents=True, exist_ok=True)
    log = json.loads(path.read_text()) if path.exists() else {"roll": p.roll, "writes": []}
    by_n = {c.number: c for c in checks}
    log["writes"].append({
        "at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "folder": str(p.folder),
        "argfile": str(argfile),
        "warnings": warnings,
        "frames": [
            {"number": f.number, "file": f.path.name, "local": f.local, "offset": f.offset, "lat": f.lat, "lon": f.lon,
             "source": f.source, "confidence": f.confidence, "anchor_uuid": f.anchor_uuid, "keywords": f.keywords,
             "verified": by_n[f.number].ok if f.number in by_n else None, "problems": by_n[f.number].problems if f.number in by_n else []}
            for f in p.frames
        ],
        "skipped": [{"number": s.number, "why": s.why} for s in p.skipped],
    })
    path.write_text(json.dumps(log, indent=1) + "\n")
    return path


# -- the whole write ---------------------------------------------------------------------------------


@dataclass
class WriteResult:
    argfile: Path
    backed_up: list[Path]
    warnings: list[str]
    checks: list[Check]
    record: Path
    sidecar: Path | None = None

    @property
    def ok(self) -> bool:
        return all(c.ok for c in self.checks)


def write_roll(p: WritePlan, writes_dir: Path = WRITES_DIR, assignments: dict | None = None,
               verdicts: dict[int, dict] | None = None, facts=None, overrides=None) -> WriteResult:
    """Backup, clear stale `_original`s, write, read back, record, sidecar. The order is the safety."""
    from filmgeo.write import sidecar

    if not p.frames:
        raise WriteError("nothing to write: no confirmed frames")
    argfile = save_argfile(p, writes_dir)
    backed_up = backup(p)
    clear_stale_originals(p)
    warnings = apply(argfile)
    checks = verify(p)
    rec = record(p, argfile, checks, warnings, writes_dir)
    side = None
    if assignments is not None and facts is not None:
        side = sidecar.write(p.folder, p.roll, p, assignments, verdicts or {}, facts, overrides, {c.number: c.ok for c in checks})
    return WriteResult(argfile, backed_up, warnings, checks, rec, side)


# -- restore -----------------------------------------------------------------------------------------


@dataclass
class Restored:
    file: str
    how: str          # "_original" | "backup" | "nothing to restore"


def restore(folder: Path, files: list[Path] | None = None) -> list[Restored]:
    """Put the scans back as they were before filmgeo: the backup folder first, exiftool's `_original` second."""
    from filmgeo.scans.ingest import ingest

    targets = files if files is not None else [f.path for f in ingest(folder).frames]
    d = backup_dir(folder)
    out = []
    for path in targets:
        orig = path.with_name(path.name + "_original")
        if (d / path.name).exists():
            shutil.copy2(d / path.name, path)
            orig.unlink(missing_ok=True)          # a stale previous-version copy would only confuse the next write
            out.append(Restored(path.name, "backup"))
        elif orig.exists():
            exiftool(["-restore_original", str(path)])
            out.append(Restored(path.name, "_original"))
        else:
            out.append(Restored(path.name, "nothing to restore"))
    return out


# -- clear -------------------------------------------------------------------------------------------

WRITTEN_TAGS = (
    "EXIF:DateTimeOriginal", "EXIF:CreateDate", "EXIF:OffsetTimeOriginal", "EXIF:OffsetTimeDigitized", "EXIF:OffsetTime",
    "XMP-exif:DateTimeOriginal", "XMP-photoshop:DateCreated", "XMP-xmp:CreateDate",
    "EXIF:GPSLatitude", "EXIF:GPSLatitudeRef", "EXIF:GPSLongitude", "EXIF:GPSLongitudeRef",
    "XMP-exif:GPSLatitude", "XMP-exif:GPSLongitude", "EXIF:Make", "EXIF:Model",
)


def clear(folder: Path, everything: bool = False, files: list[Path] | None = None) -> dict[str, list[str]]:
    """Remove the `filmgeo:` keywords from every scan; with `everything`, the written tags too.

    Descriptive keywords (`Film`, the camera, the stock, the lab) are the user's convention
    and are left in place either way. Returns, per file, what was removed.
    """
    from filmgeo.scans.ingest import ingest

    targets = files if files is not None else [f.path for f in ingest(folder).frames]
    by_n = {i: p for i, p in enumerate(targets)}
    current = current_tags(by_n)
    removed: dict[str, list[str]] = {}
    lines = ["-charset", "filename=utf8"]
    first = True
    for i, path in by_n.items():
        prov = current[i].provenance
        if not prov and not everything:
            continue
        if not first:
            lines.append("-execute")
        first = False
        for k in prov:
            lines += [f"-XMP-dc:Subject-={k}", f"-IPTC:Keywords-={k}"]
        if everything:
            lines += [f"-{t}=" for t in WRITTEN_TAGS]
        lines.append(str(path))
        removed[path.name] = prov + (["dates, offsets, GPS, Make/Model"] if everything else [])
    if not removed:
        return {}
    argfile = folder / BACKUP_DIR / "clear.args"
    argfile.parent.mkdir(exist_ok=True)
    argfile.write_text("\n".join(lines) + "\n")
    clear_plan_originals(targets, folder)
    apply(argfile)
    return removed


def clear_plan_originals(targets: list[Path], folder: Path) -> None:
    d = backup_dir(folder)
    for path in targets:
        orig = path.with_name(path.name + "_original")
        if orig.exists() and (d / path.name).exists():
            orig.unlink()
