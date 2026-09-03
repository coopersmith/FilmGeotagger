"""The NFC camera-tag log: an Apple Note the user's Shortcut appends to on every scan.

Measured on the real note (3 Sept 2026, 35 entries since April 2024). Entries are separated by
a line of `--` and look like one of these two shapes — the Shortcut has changed over time and
both must parse:

    🕑 Apr 29, 2024 at 5:34 PM                🕑 Aug 12, 2026 at 11:37 AM
    📍41.5536 , -71.1929                      📍43.5828 , 11.3179
    🗺️ 4398 Main Rd                            🗺️ Via Cesare Battisti 8A
    Tiverton RI 02878                          50022 Greve in Chianti Tuscany
    United States                              Italy
    📷 Mamiya 7II
    🎞️ Kodak Portra 160
    📓Notes f11, 125

Quirks to tolerate: a narrow no-break space (U+202F) before AM/PM, emoji with or without the
U+FE0F variation selector, an object-replacement character (U+FFFC) where a photo attachment
sits, a title line that itself starts with 📍🎞️, and address lines that continue for an
unpredictable number of lines. The time is wall-clock local to wherever the scan happened, with
no offset — resolving it needs a second source, which is why `NfcLog` takes an `offset_for`.

Reading the note goes through `osascript`, which on this library takes minutes for a single
`get` (the AppleEvent timed out at the default 2 minutes; 15 minutes succeeds), so the text is
cached under `.filmgeo/` and re-read only on request.

PLAN.md recommends two Shortcut changes that this parser is already ready for: the camera line
is now present on most entries, and a note beginning `loaded` or `finished` is surfaced as
`event` so a future roll-window signal can use it.

**Only the time, location and camera are evidence.** The user has said the film stock in the
note is stale — the Shortcut carries the last value entered, not what is in the camera — so
`NfcEntry.film` is parsed for completeness but never reaches a trail point. Treat the notes
line the same way: it describes the tap, not the roll.
"""

from __future__ import annotations

import re
import subprocess
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone, tzinfo
from pathlib import Path

from filmgeo.config import DATA_DIR
from filmgeo.signals.base import Constraint, TrailPoint, Window

SOURCE = "nfc"
NOTE_TITLE = "📍🎞️ My Film Logs"
CACHE = DATA_DIR / "nfc_log.txt"
OSASCRIPT_TIMEOUT = 900  # seconds; Notes needs minutes, see module docstring

_SEP = re.compile(r"^\s*-{2,}\s*$", re.M)
_VS = "️"
_TIME_FORMATS = ("%b %d, %Y at %I:%M %p", "%b %d, %Y, %I:%M %p", "%Y-%m-%d %H:%M")

OffsetFor = Callable[[datetime, float | None, float | None], int | None]


@dataclass
class NfcEntry:
    local_time: datetime               # naive wall clock, as the Shortcut wrote it
    lat: float | None
    lon: float | None
    address: str | None = None
    camera: str | None = None
    film: str | None = None            # stale in the real note (see module docstring); not evidence
    notes: str | None = None
    line: int | None = None            # 1-based line of the 🕑 in the note, for provenance

    @property
    def event(self) -> str | None:
        """`loaded` / `finished` if the note starts with that word (the PLAN.md extension)."""
        if not self.notes:
            return None
        m = re.match(r"\s*(loaded|finished)\b", self.notes, re.I)
        return m.group(1).lower() if m else None


def _clean(line: str) -> str:
    return line.replace(_VS, "").replace("￼", "").replace(" ", " ").strip()


def _parse_time(text: str) -> datetime | None:
    text = re.sub(r"\s+", " ", text).strip()
    for fmt in _TIME_FORMATS:
        try:
            return datetime.strptime(text, fmt)
        except ValueError:
            continue
    return None


def _blocks(text: str):
    """Yield (first_line_number, lines) for each `--`-separated block, 1-based."""
    lines = text.splitlines()
    start = 0
    for i, l in enumerate(lines + ["--"]):
        if _SEP.match(l):
            if i > start:
                yield start + 1, lines[start:i]
            start = i + 1


def parse(text: str) -> list[NfcEntry]:
    """Every well-formed entry in the note text, in the order written. Malformed ones are skipped."""
    entries: list[NfcEntry] = []
    for first, raw in _blocks(text):
        lines = [_clean(l) for l in raw]
        when = lat = lon = line_no = None
        address: list[str] = []
        camera = film = notes = None
        section = None
        for i, l in enumerate(lines):
            if not l:
                continue
            if l.startswith("🕑"):
                when, section, line_no = _parse_time(l[1:]), None, first + i
            elif l.startswith("📍") and "🎞" not in l:
                m = re.match(r"📍\s*(-?\d+(?:\.\d+)?)\s*,\s*(-?\d+(?:\.\d+)?)", l)
                if m:
                    lat, lon = float(m.group(1)), float(m.group(2))
                section = None
            elif l.startswith("🗺"):
                address, section = [l[1:].strip()], "address"
            elif l.startswith("📷"):
                camera, section = l[1:].strip() or None, None
            elif l.startswith("🎞"):
                film, section = l[1:].strip() or None, None
            elif l.startswith("📓"):
                body = re.sub(r"^📓\s*Notes[.:]?\s*", "", l).strip()
                notes, section = body or None, "notes"
            elif section == "address":
                address.append(l)
            elif section == "notes":
                notes = f"{notes}\n{l}" if notes else l
        if when is None or lat is None:
            continue
        entry = NfcEntry(when, lat, lon, "\n".join(address) or None, camera, film, notes, line_no)
        # Tapping the tag twice appends twice (three identical entries seen at one instant).
        if entries and (entries[-1].local_time, entries[-1].lat, entries[-1].lon) == (when, lat, lon):
            continue
        entries.append(entry)
    return entries


def read_note(title: str = NOTE_TITLE, timeout: int = OSASCRIPT_TIMEOUT) -> str:
    """Fetch the note's plain text from Notes.app. Slow — minutes — so cache the result."""
    script = (
        f"with timeout of {timeout} seconds\n"
        f'tell application "Notes" to get plaintext of first note whose name is "{title}"\n'
        "end timeout"
    )
    out = subprocess.run(["osascript", "-e", script], capture_output=True, text=True, timeout=timeout + 30)
    if out.returncode != 0:
        raise RuntimeError(f"could not read the note {title!r} from Notes.app: {out.stderr.strip()}")
    return out.stdout


def load_text(refresh: bool = False, cache: Path = CACHE, title: str = NOTE_TITLE) -> str:
    if cache.exists() and not refresh:
        return cache.read_text()
    text = read_note(title)
    cache.parent.mkdir(parents=True, exist_ok=True)
    cache.write_text(text)
    return text


def local_offset_for(zone: tzinfo | None = None) -> OffsetFor:
    """Fallback resolver: assume the scan happened in `zone` (default: this Mac's zone)."""

    def offset_for(naive: datetime, lat: float | None, lon: float | None) -> int | None:
        aware = naive.replace(tzinfo=zone) if zone else naive.astimezone()
        off = aware.utcoffset()
        return int(off.total_seconds()) if off is not None else None

    return offset_for


class NfcLog:
    """`Signal` over the parsed entries. Each scan is a strong trail point, never an anchor."""

    name = "nfc_log"

    def __init__(self, entries: list[NfcEntry], offset_for: OffsetFor | None = None):
        self.entries = entries
        self.offset_for = offset_for or local_offset_for()

    @classmethod
    def from_notes(cls, offset_for: OffsetFor | None = None, refresh: bool = False) -> "NfcLog":
        return cls(parse(load_text(refresh=refresh)), offset_for)

    def points(self) -> list[TrailPoint]:
        out = []
        for e in self.entries:
            off = self.offset_for(e.local_time, e.lat, e.lon)
            if off is None:
                continue
            t = e.local_time.replace(tzinfo=timezone(timedelta(seconds=off)))
            label = e.address.splitlines()[0] if e.address else None   # film and notes are not trusted
            out.append(TrailPoint(t, e.lat, e.lon, SOURCE, tzoffset=off, camera=e.camera,
                                  label=label or None, ref=f"line {e.line}" if e.line else None))
        out.sort(key=lambda p: p.time)
        return out

    def trail_points(self, window: Window) -> list[TrailPoint]:
        return [p for p in self.points() if window.contains(p.time)]

    def constraints(self) -> list[Constraint]:
        return []
