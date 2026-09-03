"""Roll ingest: a directory of scans becomes an ordered list of frames.

Scan order is shooting order, so filename order is the sequence the alignment relies on — it
must be natural-sorted, or frame 10 lands before frame 2.
"""

from __future__ import annotations

import hashlib
import re
import subprocess
from dataclasses import dataclass
from pathlib import Path

SCAN_SUFFIXES = {".jpg", ".jpeg", ".tif", ".tiff"}


def natural_key(path: Path) -> list:
    """Split digits out so 874466_0002 sorts before 874466_0010."""
    return [int(t) if t.isdigit() else t.lower() for t in re.split(r"(\d+)", path.name)]


def sha256(path: Path, limit: int = 1 << 20) -> str:
    """Hash the first megabyte plus the size — full hashes of 28 MB scans are not worth the wait."""
    h = hashlib.sha256()
    with path.open("rb") as f:
        h.update(f.read(limit))
    h.update(str(path.stat().st_size).encode())
    return h.hexdigest()


@dataclass
class Frame:
    number: int
    path: Path
    sha: str
    already_tagged: bool


@dataclass
class ScanRoll:
    directory: Path
    frames: list[Frame]

    @property
    def name(self) -> str:
        return self.directory.name

    @property
    def format_guess(self) -> str:
        """Frame count identifies the format (M0): ten frames is a 6x7 roll on 120."""
        return "120 (6x7)" if len(self.frames) <= 12 else "35mm"


def _tagged(paths: list[Path]) -> dict[Path, bool]:
    """A frame is already tagged if it has DateTimeOriginal.

    The lab JPGs arrive with no EXIF IFD at all (M0), so presence of the tag is a reliable
    "we have written this" signal. Some labs do leave scanner Make/Model behind, which is why
    the test is the date and not merely the existence of EXIF.
    """
    if not paths:
        return {}
    try:
        out = subprocess.run(
            ["exiftool", "-j", "-EXIF:DateTimeOriginal", *[str(p) for p in paths]],
            capture_output=True, text=True, check=True,
        ).stdout
    except (OSError, subprocess.CalledProcessError):
        return {p: False for p in paths}
    import json

    result = {p: False for p in paths}
    for row in json.loads(out or "[]"):
        result[Path(row["SourceFile"])] = bool(row.get("DateTimeOriginal"))
    return result


def ingest(directory: str | Path) -> ScanRoll:
    directory = Path(directory)
    paths = sorted(
        (p for p in directory.iterdir() if p.suffix.lower() in SCAN_SUFFIXES and not p.name.startswith(".")),
        key=natural_key,
    )
    tagged = _tagged(paths)
    frames = [
        Frame(number=i, path=p, sha=sha256(p), already_tagged=tagged.get(p, False))
        for i, p in enumerate(paths, 1)
    ]
    return ScanRoll(directory=directory, frames=frames)
