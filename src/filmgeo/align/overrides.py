"""What the user decided about a frame's assignment, as distinct from what they know (facts).

Facts (`signals/user_facts.py`) are statements about the world: frame 12 was on 4 July, at
this pin, on the same day as frame 9. They are constraints and belong to every solver run.
Overrides are decisions about the *matching*: "this is the photo", "that is not a match",
"no phone photo shows this". They edit the anchors that verification produced before the
solver sees them, and they lock the frame — a user pick is an anchor at confidence 1 that
prunes every other state (PLAN.md: overrides become locked states with `filmgeo:manual`
provenance). `confirmed` is the review status the write step (M4) will require; the batch
actions that set it in bulk are COO-126.

One JSON file per roll under `.filmgeo/overrides/`, keyed like the facts file. The review UI
is the only writer; the CLI does not create them.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from pathlib import Path

import numpy as np

from filmgeo.align.model import Anchor
from filmgeo.config import DATA_DIR
from filmgeo.photos.library import Asset

OVERRIDES_DIR = DATA_DIR / "overrides"


@dataclass
class FrameOverride:
    number: int
    anchor: str | None = None                       # uuid the user picked: a locked anchor
    rejected: list[str] = field(default_factory=list)   # "not a match": verdict anchors on these uuids are dropped
    no_reference: bool = False                      # "no reference": every verdict anchor on this frame is dropped
    confirmed: bool = False

    @property
    def is_empty(self) -> bool:
        return not (self.anchor or self.rejected or self.no_reference or self.confirmed)

    @property
    def locks(self) -> bool:
        """Does this override pin the frame's matching, as opposed to merely marking it confirmed?"""
        return bool(self.anchor) or self.no_reference


@dataclass
class RollOverrides:
    roll: str
    frames: dict[int, FrameOverride] = field(default_factory=dict)

    @staticmethod
    def path_for(roll: str, directory: Path = OVERRIDES_DIR) -> Path:
        return directory / f"{roll}.json"

    @classmethod
    def load(cls, roll: str, directory: Path = OVERRIDES_DIR) -> "RollOverrides":
        p = cls.path_for(roll, directory)
        if not p.exists():
            return cls(roll=roll)
        raw = json.loads(p.read_text())
        return cls(roll=roll, frames={int(k): FrameOverride(**v) for k, v in raw.get("frames", {}).items()})

    def save(self, directory: Path = OVERRIDES_DIR) -> Path:
        p = self.path_for(self.roll, directory)
        p.parent.mkdir(parents=True, exist_ok=True)
        data = {"roll": self.roll,
                "frames": {str(k): asdict(v) for k, v in sorted(self.frames.items()) if not v.is_empty}}
        p.write_text(json.dumps(data, indent=2) + "\n")
        return p

    def frame(self, number: int) -> FrameOverride:
        return self.frames.setdefault(number, FrameOverride(number=number))

    def get(self, number: int) -> FrameOverride | None:
        f = self.frames.get(number)
        return None if f is None or f.is_empty else f

    def apply(self, anchors: list[Anchor], pool: list[Asset], event_ids: list[int],
              sims: np.ndarray | None = None) -> list[Anchor]:
        """Verification's anchors, edited by the user's decisions.

        A rejected uuid or a `no_reference` frame loses its verdict anchors; a picked photo
        becomes a locked anchor at that photo's instant. A pick names a photo outside the
        pool (a stale uuid after the window moved) is ignored rather than fatal — the frame
        simply falls back to interpolation.
        """
        index = {a.uuid: j for j, a in enumerate(pool)}
        out = []
        for a in anchors:
            o = self.frames.get(a.frame + 1)
            if o is None or o.is_empty:
                out.append(a)
            elif o.anchor or o.no_reference or a.uuid in o.rejected:
                continue
            else:
                out.append(a)
        for n, o in sorted(self.frames.items()):
            if not o.anchor or o.anchor not in index:
                continue
            j = index[o.anchor]
            asset = pool[j]
            i = n - 1
            sim = float(sims[i, j]) if sims is not None and 0 <= i < sims.shape[0] else 0.0
            out.append(Anchor(i, asset.uuid, asset.date, event_ids[j], 1.0, similarity=sim,
                              lat=asset.lat, lon=asset.lon, tzoffset=asset.tzoffset, locked=True))
        out.sort(key=lambda a: (a.frame, a.time))
        return out
