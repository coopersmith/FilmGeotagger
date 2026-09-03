"""What the user knows about a roll, as evidence.

This is the highest-value signal in the whole system and the cheapest to obtain (COO-117):
M1 measured window width at ~9 points of recall@8, and the user can say "April" or "the
Portugal trip" in seconds, which is a tighter bound than anything the tool could infer.

Facts live in one JSON file per roll under `.filmgeo/facts/`, keyed by the roll's name (the
scan folder name, or the lab key for a hand-tagged eval roll). The CLI writes them (`filmgeo
facts`); the M3 review UI edits the same file. Nothing else creates them.

Time handling: the user thinks in local calendar dates, so periods are given as `2026-04`
(the month), `2026-04-12` (the day) or `2026-04-12 14:05` (the minute), and interpreted in the
roll's `tz` — an IANA name, defaulting to this Mac's zone. A period becomes a half-open range
[start, end), so "April" is 1 April 00:00 up to but excluding 1 May 00:00.
"""

from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta, tzinfo
from pathlib import Path
from zoneinfo import ZoneInfo

from filmgeo.config import DATA_DIR
from filmgeo.signals.base import Constraint, TrailPoint, Window

FACTS_DIR = DATA_DIR / "facts"
SOURCE = "user"

_PERIOD = re.compile(
    r"^(?P<y>\d{4})(?:-(?P<m>\d{2})(?:-(?P<d>\d{2})(?:[ T](?P<H>\d{2}):(?P<M>\d{2})(?::(?P<S>\d{2}))?)?)?)?$"
)


def local_zone(name: str | None) -> tzinfo:
    if name:
        return ZoneInfo(name)
    return datetime.now().astimezone().tzinfo  # type: ignore[return-value]


def parse_period(text: str, tz: tzinfo) -> tuple[datetime, datetime]:
    """`2026`, `2026-04`, `2026-04-12`, `2026-04-12 14:05[:SS]` -> half-open [start, end)."""
    m = _PERIOD.match(text.strip())
    if not m:
        raise ValueError(
            f"cannot read {text!r} as a period; use YYYY, YYYY-MM, YYYY-MM-DD or 'YYYY-MM-DD HH:MM'"
        )
    y, mo, d = int(m["y"]), m["m"], m["d"]
    if mo is None:
        start = datetime(y, 1, 1, tzinfo=tz)
        return start, datetime(y + 1, 1, 1, tzinfo=tz)
    if d is None:
        start = datetime(y, int(mo), 1, tzinfo=tz)
        nxt = datetime(y + 1, 1, 1, tzinfo=tz) if int(mo) == 12 else datetime(y, int(mo) + 1, 1, tzinfo=tz)
        return start, nxt
    if m["H"] is None:
        start = datetime(y, int(mo), int(d), tzinfo=tz)
        return start, start + timedelta(days=1)
    sec = int(m["S"] or 0)
    start = datetime(y, int(mo), int(d), int(m["H"]), int(m["M"]), sec, tzinfo=tz)
    # A time given to the minute means "that minute"; to the second, that second.
    return start, start + (timedelta(seconds=1) if m["S"] else timedelta(minutes=1))


@dataclass
class FrameFact:
    number: int
    when: str | None = None            # a period, see parse_period
    lat: float | None = None
    lon: float | None = None
    radius_m: float | None = None
    place_name: str | None = None
    same_day_as: int | None = None
    skip: bool = False
    note: str | None = None

    @property
    def is_empty(self) -> bool:
        return not any((self.when, self.lat is not None, self.same_day_as, self.skip, self.note, self.place_name))


@dataclass
class RollFacts:
    roll: str
    window_from: str | None = None     # period; its start bounds the roll
    window_to: str | None = None       # period; its end bounds the roll
    tz: str | None = None              # IANA zone the periods are read in; None = this Mac's
    camera: str | None = None
    film: str | None = None
    lab: str | None = None
    notes: str | None = None
    reverse: bool = False              # the roll was wound/scanned in reverse order
    frames: dict[int, FrameFact] = field(default_factory=dict)

    # -- persistence ---------------------------------------------------------------------

    @staticmethod
    def path_for(roll: str, directory: Path = FACTS_DIR) -> Path:
        return directory / f"{roll}.json"

    @classmethod
    def load(cls, roll: str, directory: Path = FACTS_DIR) -> "RollFacts":
        p = cls.path_for(roll, directory)
        if not p.exists():
            return cls(roll=roll)
        raw = json.loads(p.read_text())
        frames = {int(k): FrameFact(**v) for k, v in raw.pop("frames", {}).items()}
        return cls(**raw, frames=frames)

    def save(self, directory: Path = FACTS_DIR) -> Path:
        p = self.path_for(self.roll, directory)
        p.parent.mkdir(parents=True, exist_ok=True)
        data = asdict(self)
        data["frames"] = {str(k): asdict(v) for k, v in sorted(self.frames.items()) if not v.is_empty}
        p.write_text(json.dumps(data, indent=2) + "\n")
        return p

    # -- interpretation -------------------------------------------------------------------

    @property
    def zone(self) -> tzinfo:
        return local_zone(self.tz)

    def window(self) -> tuple[datetime | None, datetime | None]:
        lo = parse_period(self.window_from, self.zone)[0] if self.window_from else None
        hi = parse_period(self.window_to, self.zone)[1] if self.window_to else None
        if lo and hi and hi <= lo:
            raise ValueError(f"roll {self.roll}: window ends ({self.window_to}) before it starts ({self.window_from})")
        return lo, hi

    def frame(self, number: int) -> FrameFact:
        return self.frames.setdefault(number, FrameFact(number=number))

    def validate(self, n_frames: int | None = None) -> list[str]:
        """Human-readable problems. Empty means consistent."""
        problems: list[str] = []
        try:
            self.window()
        except ValueError as e:
            problems.append(str(e))
        for n, f in sorted(self.frames.items()):
            if n_frames is not None and not (1 <= n <= n_frames):
                problems.append(f"frame {n} does not exist (roll has {n_frames})")
            if f.when:
                try:
                    parse_period(f.when, self.zone)
                except ValueError as e:
                    problems.append(f"frame {n}: {e}")
            if (f.lat is None) != (f.lon is None):
                problems.append(f"frame {n}: place needs both lat and lon")
            if f.same_day_as is not None and f.same_day_as == n:
                problems.append(f"frame {n}: same-day-as itself")
        # Dated frames must not contradict scan order.
        dated = []
        for n, f in sorted(self.frames.items()):
            if f.when:
                try:
                    dated.append((n, *parse_period(f.when, self.zone)))
                except ValueError:
                    pass
        for (a, a_lo, _), (b, _, b_hi) in zip(dated, dated[1:]):
            if b_hi <= a_lo:
                problems.append(f"frame {b} is dated before frame {a}, but is later in the roll")
        return problems


class UserFacts:
    """`Signal` adapter over a `RollFacts`."""

    name = "user_facts"

    def __init__(self, facts: RollFacts):
        self.facts = facts

    def constraints(self) -> list[Constraint]:
        out: list[Constraint] = []
        lo, hi = self.facts.window()
        if lo or hi:
            out.append(Constraint("roll", SOURCE, t_lo=lo, t_hi=hi, note="roll window"))
        tz = self.facts.zone
        for n, f in sorted(self.facts.frames.items()):
            if f.is_empty:
                continue
            t_lo = t_hi = None
            if f.when:
                t_lo, t_hi = parse_period(f.when, tz)
            out.append(
                Constraint(
                    "frame", SOURCE, frame=n, t_lo=t_lo, t_hi=t_hi,
                    lat=f.lat, lon=f.lon, radius_m=f.radius_m,
                    same_day_as=f.same_day_as, skip=f.skip,
                    note=f.note or f.place_name,
                )
            )
        return out

    def trail_points(self, window: Window) -> list[TrailPoint]:
        """A frame the user placed in both time and space is also a trail point."""
        tz = self.facts.zone
        out = []
        for n, f in sorted(self.facts.frames.items()):
            if not (f.when and f.lat is not None and f.lon is not None):
                continue
            t_lo, t_hi = parse_period(f.when, tz)
            if t_hi - t_lo > timedelta(hours=1):
                continue  # a whole day is a constraint, not a point
            if window.contains(t_lo):
                out.append(TrailPoint(t_lo, f.lat, f.lon, SOURCE, label=f.place_name, ref=f"frame {n}"))
        return out
