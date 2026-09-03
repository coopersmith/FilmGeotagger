"""Ground truth for M1, reconstructed from rolls the user already hand-tagged.

Those scans live in the Photos library, keyworded `Film`, carrying the date and GPS the user
copied across by hand. That makes them both the frames to match and the answer key — no separate
fixture set is needed. Two properties of that answer key shape every metric here (measured while
building it):

* **Times are group-level, not per-frame.** The user tagged an outing at a time and Lightroom
  spaced the frames a second apart, so eight consecutive frames can share `09:00:0X`. Only the
  frames they anchored to a phone photo are exact. Nothing may be scored to the second.
* **Only about half of it is real.** The user anchored some frames to a specific phone photo and
  copied its EXIF; for the frames *between* two such anchors they often picked a plausible date
  at random ("I knew frame 29 and I knew the next roll's frame 1, so I guessed in between").
  A guessed timestamp is not evidence: scoring against it penalises a correct match and rewards
  a lucky one on a densely photographed day. `anchored()` recovers the real ones — a frame whose
  timestamp coincides with an actual photo's to the second was copied from it — and metrics that
  matter are reported on those alone. Measured share across the 2026 batch: 113 of 227 frames,
  ranging from 95% on one roll to 0% on another.
* **It contains real errors.** Roll `00007038` frame 1 is dated 38 days *after* frames 2-38 of
  the same monotone roll. Ground truth is a strong signal, not gospel; `outliers()` flags frames
  that contradict their own roll so a metric can exclude them and say so.
"""

from __future__ import annotations

import collections
import re
import statistics
from dataclasses import dataclass
from datetime import datetime, timedelta

from filmgeo.photos.library import Asset

# Lab filename conventions seen in this library. Both encode roll then frame number.
_PATTERNS = (
    re.compile(r"^(\d{6})_(\d{4})\.jpg$", re.I),      # Richard Photo Lab: 874466_0012.jpg
    re.compile(r"^(\d{8})(\d{4})\.jpg$", re.I),       # Indie Film Lab:    000070400016.jpg
)


def _roll_and_frame(filename: str) -> tuple[str, int] | tuple[None, None]:
    name = re.sub(r"^[0-9a-f]{8}-", "", filename or "")   # strip Photos export hash
    name = re.sub(r"_Original\.jpg$", ".jpg", name, flags=re.I)
    for pat in _PATTERNS:
        if m := pat.match(name):
            return m.group(1), int(m.group(2))
    return None, None


@dataclass
class Roll:
    key: str
    frames: list[Asset]          # in scan order
    numbers: list[int]

    @property
    def start(self) -> datetime:
        return min(f.date for f in self.frames)

    @property
    def end(self) -> datetime:
        return max(f.date for f in self.frames)

    @property
    def span(self) -> timedelta:
        return self.end - self.start

    @property
    def format(self) -> str:
        """Frame count identifies the film format (M0): 10 frames is a 6x7 roll on 120."""
        return "120 (6x7)" if len(self.frames) <= 12 else "35mm"

    def outliers(self) -> set[int]:
        """Indices whose date contradicts the rest of a monotone roll.

        A roll is monotone in time, so a frame sitting far outside the span of every other frame
        is a tagging error rather than evidence. Uses a generous multiple of the roll's own span
        so that legitimately long-lived rolls are not flagged.
        """
        if len(self.frames) < 4:
            return set()
        dates = [f.date for f in self.frames]
        median = statistics.median(d.timestamp() for d in dates)
        others = sorted(abs(d.timestamp() - median) for d in dates)
        typical = others[int(len(others) * 0.75)] or 1.0
        return {i for i, d in enumerate(dates) if abs(d.timestamp() - median) > max(10 * typical, 86400 * 14)}

    def anchored(self, nonfilm_times: "np.ndarray", tolerance: float = 2.0) -> list[int]:
        """Indices of frames the user genuinely anchored, rather than derived or guessed.

        Hand-anchoring means copying a phone photo's EXIF, so the frame's timestamp lands on that
        photo's to the second. A derived or guessed time almost never does. `tolerance` is a
        couple of seconds because Lightroom spaces a tagged group one second apart.
        """
        import numpy as np

        out = []
        for i, f in enumerate(self.frames):
            t = f.date.timestamp()
            j = np.searchsorted(nonfilm_times, t)
            near = nonfilm_times[max(0, j - 1) : j + 2]
            if len(near) and float(np.min(np.abs(near - t))) <= tolerance:
                out.append(i)
        return out

    def clean(self) -> "Roll":
        bad = self.outliers()
        keep = [i for i in range(len(self.frames)) if i not in bad]
        return Roll(self.key, [self.frames[i] for i in keep], [self.numbers[i] for i in keep])


def rolls(assets: list[Asset], min_frames: int = 6) -> list[Roll]:
    """Group hand-tagged film assets into rolls by lab filename."""
    grouped: dict[str, list[tuple[int, Asset]]] = collections.defaultdict(list)
    for a in assets:
        if not a.is_film:
            continue
        key, frame = _roll_and_frame(a.filename)
        if key is not None:
            grouped[key].append((frame, a))
    out = []
    for key, items in grouped.items():
        items.sort(key=lambda t: t[0])
        if len(items) >= min_frames:
            out.append(Roll(key, [a for _, a in items], [n for n, _ in items]))
    out.sort(key=lambda r: r.start, reverse=True)
    return out
