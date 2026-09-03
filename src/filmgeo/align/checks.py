"""Roll-level sanity checks on a solved alignment (COO-118).

Two ways a roll can be wrong as a whole rather than frame by frame:

* **Reverse-wound.** Scan order is shooting order — unless the lab or the camera reversed it.
  Solve the roll backwards too; if the reversed order wins clearly *and* holds at least three
  anchors, flag it. One or two anchors can be reordered by a wrong verification; three in
  the wrong order is a roll.
* **Wrong window.** The user said "April" and it was May. Measured on the hand-tagged rolls
  (docs/m2-findings.md): with no verification, nothing about similarity separates the right
  window from one shifted a fortnight — best similarity, its margin over the pool and the
  solver's score above the null path all overlap. So the flag rests on what verification
  says: a roll whose frames were verified and almost none anchored is doubtful, and the
  posterior's best-scoring days are reported so the UI can suggest where to look. Widening
  is cheap because retrieval is; only new top-K candidates need verifying.
"""

from __future__ import annotations

import dataclasses
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta

import numpy as np

from filmgeo.align.model import AlignParams, Anchor, FrameClues, RollModel, build_model
from filmgeo.align.solve import Solution, null_score, solve
from filmgeo.events import Event
from filmgeo.signals.base import Constraint, Window


@dataclass
class RollInputs:
    """Everything `build_model` takes, so a roll can be solved forwards and backwards alike."""

    window: Window
    events: list[Event]
    n_frames: int
    anchors: list[Anchor] = field(default_factory=list)
    sims: np.ndarray | None = None
    event_ids: list[int] | None = None
    clues: list[FrameClues | None] | None = None
    constraints: list[Constraint] = field(default_factory=list)
    same_outing: set[tuple[int, int]] = field(default_factory=set)
    params: AlignParams | None = None

    def build(self) -> RollModel:
        return build_model(self.window, self.events, self.n_frames, self.anchors, self.sims, self.event_ids,
                           self.clues, self.constraints, self.same_outing, self.params)

    def reversed(self) -> "RollInputs":
        n = self.n_frames
        flip = lambda i: n - 1 - i                                   # 0-based
        flip1 = lambda f: None if f is None else n + 1 - f          # 1-based
        return RollInputs(
            window=self.window, events=self.events, n_frames=n,
            anchors=[dataclasses.replace(a, frame=flip(a.frame)) for a in self.anchors],
            sims=None if self.sims is None else self.sims[::-1].copy(),
            event_ids=self.event_ids,
            clues=None if self.clues is None else list(reversed(self.clues)),
            constraints=[dataclasses.replace(c, frame=flip1(c.frame), same_day_as=flip1(c.same_day_as))
                         if c.scope == "frame" else c for c in self.constraints],
            same_outing={(flip(b), flip(a)) for a, b in self.same_outing},
            params=self.params,
        )


@dataclass
class ReverseTest:
    forward_score: float
    reverse_score: float
    forward_anchored: int
    reverse_anchored: int
    suspect: bool

    @property
    def margin(self) -> float:
        return self.reverse_score - self.forward_score


def reverse_test(inputs: RollInputs, forward: Solution | None = None, min_anchors: int = 3, extra_anchors: int = 2) -> ReverseTest:
    """Flag a roll whose reversed order scores higher *and* holds clearly more anchors.

    Score alone is not a clear win: one verified anchor is worth about as much as a week's
    jump (see AlignParams), so scores of the two orders sit close. Anchors held is what a
    reversed roll changes decisively — the wrong order can keep only one of any two anchors
    that lie in the wrong sequence.
    """
    fwd = forward or solve(inputs.build())
    rev = solve(inputs.reversed().build())
    suspect = rev.log_score > fwd.log_score and rev.anchored >= max(min_anchors, fwd.anchored + extra_anchors)
    return ReverseTest(fwd.log_score, rev.log_score, fwd.anchored, rev.anchored, suspect)


@dataclass
class WindowCheck:
    anchored: int
    n_frames: int
    n_verified: int | None            # frames that went through verification; None = none did
    score_per_frame: float            # proposal minus null path, per frame
    outside_mass: float               # mean posterior mass on X
    best_days: list[tuple[date, float]]
    doubtful: bool
    reason: str

    @property
    def anchored_fraction(self) -> float:
        return self.anchored / self.n_frames if self.n_frames else 0.0


def best_days(model: RollModel, solution: Solution, top: int = 5) -> list[tuple[date, float]]:
    """Posterior mass per local calendar day, summed over frames. Where the roll thinks it is."""
    mass: dict[date, float] = defaultdict(float)
    mids = [s.t_lo + (s.t_hi - s.t_lo) / 2 for s in model.states]
    for i in range(model.n_frames):
        for j, p in enumerate(solution.posterior[i]):
            if p > 1e-4 and model.states[j].kind != "outside":
                mass[mids[j].date()] += float(p)
    return sorted(mass.items(), key=lambda kv: -kv[1])[:top]


def window_check(
    model: RollModel,
    solution: Solution,
    n_verified: int | None = None,
    min_anchored_fraction: float = 0.1,
    min_anchors: int = 2,
    max_outside_mass: float = 0.25,
) -> WindowCheck:
    """Doubtful when verification ran and fewer than max(`min_anchors`, fraction × frames) anchored.

    The count floor matters on 10-frame 6x7 rolls: one lucky match is 10%, and the wrong-month
    run of `00007044` produced exactly one (docs/m2-findings.md).
    """
    outside = float(np.mean([a.outside_mass for a in solution.assignments]))
    per_frame = (solution.log_score - null_score(model)) / max(1, model.n_frames)
    days = best_days(model, solution)
    anchored = solution.anchored
    if n_verified:
        need = max(min_anchors, min_anchored_fraction * model.n_frames)
        if anchored < need:
            return WindowCheck(anchored, model.n_frames, n_verified, per_frame, outside, days, True,
                               f"only {anchored} of {model.n_frames} frames anchored after verifying {n_verified}")
        reason = f"{anchored} of {model.n_frames} frames anchored"
    else:
        reason = "no verification yet; similarity alone cannot tell a wrong window (measured, docs/m2-findings.md)"
    if outside > max_outside_mass:
        return WindowCheck(anchored, model.n_frames, n_verified, per_frame, outside, days, True,
                           f"mean posterior mass outside the window {outside:.2f}")
    return WindowCheck(anchored, model.n_frames, n_verified, per_frame, outside, days, False, reason)


def widen(window: Window, months: int = 1) -> Window:
    """The window a doubtful roll is re-run with: a month more on each side."""
    pad = timedelta(days=31 * months)
    return Window(window.start - pad, window.end + pad)


def new_candidates(before: dict[int, list[str]], after: dict[int, list[str]]) -> dict[int, list[str]]:
    """Per frame, the top-K candidate uuids that appeared only after widening — the ones to verify."""
    return {i: [u for u in after.get(i, []) if u not in set(before.get(i, []))] for i in after}
