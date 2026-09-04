"""The `Signal` interface: every evidence source speaks one small vocabulary.

PLAN.md sorts evidence into three kinds. Two of them are what a signal returns:

* **Trail points** — where the user was at an instant (phone photo, NFC tag scan, workout
  route, check-in). Soft evidence: they locate and time-zone the frames that no anchor pins.
* **Constraints** — a frame or a roll *must* lie inside a time range and/or near a place.
  Hard evidence, from the user or from something that binds like a lab delivery date. The
  solver zeros out every state a constraint excludes.

Anchors (a frame visually matched to a specific photo) are the third kind, and they come out
of retrieval + verification rather than a `Signal`, so they are not modelled here.

Times are tz-aware everywhere. `tzoffset` is seconds east of UTC, matching osxphotos.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Literal, Protocol, runtime_checkable


@dataclass(frozen=True)
class Window:
    """A closed time range. `Window.around` is the convenience most callers want."""

    start: datetime
    end: datetime

    def __post_init__(self) -> None:
        if self.start.tzinfo is None or self.end.tzinfo is None:
            raise ValueError("Window bounds must be tz-aware")
        if self.end < self.start:
            raise ValueError(f"Window ends before it starts: {self.start} .. {self.end}")

    @classmethod
    def around(cls, start: datetime, end: datetime, pad_days: int = 0) -> "Window":
        pad = timedelta(days=pad_days)
        return cls(start - pad, end + pad)

    def contains(self, t: datetime) -> bool:
        return self.start <= t <= self.end

    @property
    def span(self) -> timedelta:
        return self.end - self.start


@dataclass(frozen=True)
class TrailPoint:
    """The user was here (or at least their phone / camera tag was) at this instant."""

    time: datetime
    lat: float | None
    lon: float | None
    source: str                    # "photos" | "nfc" | "user" | later adapters
    tzoffset: int | None = None    # seconds east of UTC at the point, if the source knows it
    camera: str | None = None      # NFC log entries can name the body they were scanned on
    label: str | None = None       # free text: a note line, a venue name
    ref: str | None = None         # provenance: asset uuid, note line number

    def __post_init__(self) -> None:
        if self.time.tzinfo is None:
            raise ValueError("TrailPoint.time must be tz-aware")

    @property
    def has_location(self) -> bool:
        return self.lat is not None and self.lon is not None


Scope = Literal["roll", "frame"]


@dataclass(frozen=True)
class Constraint:
    """Something that must be true of a roll or of one frame.

    Any subset of the fields may be set. A frame constraint with only `t_lo`/`t_hi` is "frame 12
    was shot on 4 July"; one with only a place is "frames 1-8 are in Lisbon"; `same_day_as`
    ties two frames together; `skip` says the user wants no assignment at all.
    """

    scope: Scope
    source: str
    frame: int | None = None               # 1-based frame number; required when scope == "frame"
    t_lo: datetime | None = None           # inclusive
    t_hi: datetime | None = None           # exclusive
    lat: float | None = None
    lon: float | None = None
    radius_m: float | None = None
    same_day_as: int | None = None
    skip: bool = False
    note: str | None = None

    def __post_init__(self) -> None:
        if self.scope == "frame" and self.frame is None:
            raise ValueError("a frame constraint needs a frame number")
        if self.scope == "roll" and self.frame is not None:
            raise ValueError("a roll constraint must not name a frame")
        for t in (self.t_lo, self.t_hi):
            if t is not None and t.tzinfo is None:
                raise ValueError("constraint times must be tz-aware")
        if self.t_lo and self.t_hi and self.t_hi <= self.t_lo:
            raise ValueError(f"empty time range {self.t_lo} .. {self.t_hi}")

    @property
    def has_time(self) -> bool:
        return self.t_lo is not None or self.t_hi is not None

    @property
    def has_place(self) -> bool:
        return self.lat is not None and self.lon is not None


@runtime_checkable
class Signal(Protocol):
    """An evidence source. Adapters implement whichever of the two methods they can."""

    name: str

    def trail_points(self, window: Window) -> list[TrailPoint]: ...

    def constraints(self) -> list[Constraint]: ...


@dataclass
class Evidence:
    """Everything the signals had to say, merged and sorted, ready for the solver."""

    trail: list[TrailPoint] = field(default_factory=list)
    constraints: list[Constraint] = field(default_factory=list)

    def for_frame(self, number: int) -> list[Constraint]:
        return [c for c in self.constraints if c.scope == "frame" and c.frame == number]

    def roll_constraints(self) -> list[Constraint]:
        return [c for c in self.constraints if c.scope == "roll"]


def collect(signals: list[Signal], window: Window) -> Evidence:
    ev = Evidence()
    for s in signals:
        ev.trail.extend(s.trail_points(window))
        ev.constraints.extend(s.constraints())
    ev.trail.sort(key=lambda p: p.time)
    return ev


def effective_window(constraints: list[Constraint], default: Window) -> Window:
    """Intersect every roll-level time constraint with the default window.

    This is where "the user said April" turns into the retrieval window. If the constraints
    contradict each other the result is empty, which raises rather than silently widening.
    """
    lo, hi = default.start, default.end
    for c in constraints:
        if c.scope != "roll":
            continue
        if c.t_lo is not None:
            lo = max(lo, c.t_lo)
        if c.t_hi is not None:
            hi = min(hi, c.t_hi)
    if hi < lo:
        raise ValueError(f"roll constraints leave no window: {lo} .. {hi}")
    return Window(lo, hi)


def frame_bounds(
    constraints: list[Constraint], n_frames: int, window: Window
) -> list[tuple[datetime, datetime]]:
    """Per-frame [t_lo, t_hi] after pushing every frame fact through the monotone order.

    A roll is shot in order, so "frame 12 is on 4 July" also means frames 1-11 end by the end
    of 4 July and frames 13+ start no earlier than its start. This is what makes one fact
    tighten its neighbours (PLAN.md), and it costs nothing, so it is computed here rather than
    inside the solver. Frames are 1-based to match scan numbering.
    """
    lo = [window.start] * n_frames
    hi = [window.end] * n_frames
    for c in constraints:
        if c.scope != "frame" or c.frame is None or not (1 <= c.frame <= n_frames):
            continue
        i = c.frame - 1
        if c.t_lo is not None:
            lo[i] = max(lo[i], c.t_lo)
        if c.t_hi is not None:
            hi[i] = min(hi[i], c.t_hi)
    # "Same day as frame N": when either side of the pair is dated, the other takes that
    # local calendar day. Two undated frames that merely share a day cannot be expressed as
    # per-frame bounds — that coupling is a joint constraint (COO-147) and is left alone here.
    pairs = {(c.frame, c.same_day_as) for c in constraints
             if c.scope == "frame" and c.frame and c.same_day_as and 1 <= c.same_day_as <= n_frames}
    changed = True
    while changed and pairs:
        changed = False
        for a, b in list(pairs):
            for src, dst in ((a, b), (b, a)):
                i, j = src - 1, dst - 1
                if (lo[i], hi[i]) == (window.start, window.end):
                    continue                      # nothing known about the source yet
                t = lo[i] if lo[i] != window.start else hi[i] - timedelta(seconds=1)
                day_lo = t.replace(hour=0, minute=0, second=0, microsecond=0)
                day_hi = day_lo + timedelta(days=1)
                new_lo, new_hi = max(lo[j], day_lo), min(hi[j], day_hi)
                if (new_lo, new_hi) != (lo[j], hi[j]):
                    lo[j], hi[j] = new_lo, new_hi
                    changed = True
    for i in range(1, n_frames):                 # lower bounds flow forward
        lo[i] = max(lo[i], lo[i - 1])
    for i in range(n_frames - 2, -1, -1):        # upper bounds flow backward
        hi[i] = min(hi[i], hi[i + 1])
    return list(zip(lo, hi))
