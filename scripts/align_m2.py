#!/usr/bin/env python3
"""M2: does the alignment put unanchored frames in intervals that contain the truth?

    uv run --extra embed python scripts/align_m2.py [--rolls 9] [--mode oracle|ends|none]

No API calls. Anchors are simulated from the hand-tagged ground truth (`Roll.anchored()`, the
frames whose timestamp coincides with a phone photo to the second) so the solver is measured
on its own logic, not on Claude's precision:

  none    no anchors at all — similarity + events + monotone order only
  ends    only the first and last anchored frame ("first and last frame" straw man)
  oracle  every other anchored frame; the held-out ones are scored

Scored per roll on held-out anchored frames only (the guessed half of the truth is excluded,
see docs/m1-findings.md): share whose true time lies inside the 90% interval, median absolute
time error of the assigned time, median interval width, and the mean posterior mass on X.
The window is the facts window if `filmgeo facts` set one, else the true range +/- pad.
"""

from __future__ import annotations

import argparse
import statistics
from datetime import timedelta

import numpy as np

from filmgeo import eval_set, events as ev
from filmgeo.align.model import Anchor, build_model
from filmgeo.align.solve import null_score, solve
from filmgeo.embed.cache import VectorCache
from filmgeo.photos import library
from filmgeo.signals.base import Window, effective_window
from filmgeo.signals.user_facts import RollFacts, UserFacts


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--rolls", type=int, default=9)
    ap.add_argument("--pad-days", type=int, default=2)
    ap.add_argument("--mode", default="oracle", choices=["none", "ends", "oracle"])
    ap.add_argument("--anchor-confidence", type=float, default=0.9)
    ap.add_argument("--tolerance", type=float, default=120,
                    help="seconds of slack on the interval test; the truth is group-level, spaced 1 s apart")
    ap.add_argument("--verbose", action="store_true", help="list every held-out frame outside its interval")
    args = ap.parse_args()
    tol = timedelta(seconds=args.tolerance)

    assets = library.load()
    nonfilm = np.array([a.date.timestamp() for a in assets if not a.is_film])
    cache = VectorCache("siglip")
    rolls = [r.clean() for r in eval_set.rolls(assets)][: args.rolls]
    totals = {"held": 0, "inside": 0, "errs": [], "widths": []}

    for roll in rolls:
        facts = RollFacts.load(roll.key)
        f_lo, f_hi = facts.window()
        cs = UserFacts(facts).constraints()
        if f_lo and f_hi:
            window, how = effective_window(cs, Window(f_lo, f_hi)), "facts"
        else:
            window, how = Window.around(roll.start, roll.end, args.pad_days), f"truth+/-{args.pad_days}d"
        pool = library.candidates(assets, window.start, window.end)
        if not pool:
            print(f"{roll.key}: empty pool, skipped")
            continue
        ids, evs = ev.segment(pool)
        try:
            fv = cache.get([f.uuid for f in roll.frames])
            pv = cache.get([a.uuid for a in pool])
        except KeyError:
            print(f"{roll.key}: vectors not cached for this window, skipped")
            continue
        sims = fv @ pv.T

        anchored = roll.anchored(nonfilm)
        pool_times = np.array([a.date.timestamp() for a in pool])
        truth_idx = {}
        for i in anchored:
            j = int(np.argmin(np.abs(pool_times - roll.frames[i].date.timestamp())))
            if abs(pool_times[j] - roll.frames[i].date.timestamp()) <= 2:
                truth_idx[i] = j
        if args.mode == "none":
            given = []
        elif args.mode == "ends":
            keys = sorted(truth_idx)
            given = [keys[0], keys[-1]] if len(keys) >= 2 else keys
        else:
            given = sorted(truth_idx)[::2]
        anchors = [
            Anchor(i, pool[truth_idx[i]].uuid, pool[truth_idx[i]].date, ids[truth_idx[i]], args.anchor_confidence,
                   similarity=float(sims[i, truth_idx[i]]), lat=pool[truth_idx[i]].lat, lon=pool[truth_idx[i]].lon,
                   tzoffset=pool[truth_idx[i]].tzoffset)
            for i in given
        ]
        model = build_model(window, evs, len(roll.frames), anchors, sims=sims, event_ids=ids, constraints=cs)
        sol = solve(model)
        held = [i for i in truth_idx if i not in given]
        inside = errs = None
        if held:
            inside = sum(1 for i in held if sol.assignments[i].t_lo - tol <= roll.frames[i].date <= sol.assignments[i].t_hi + tol)
            if args.verbose:
                for i in held:
                    a = sol.assignments[i]
                    if not (a.t_lo - tol <= roll.frames[i].date <= a.t_hi + tol):
                        prev = next((sol.assignments[k] for k in range(i - 1, -1, -1) if k in given), None)
                        nxt = next((sol.assignments[k] for k in range(i + 1, len(roll.frames)) if k in given), None)
                        print(f"    MISS frame {roll.numbers[i]:2}: truth {roll.frames[i].date:%m-%d %H:%M:%S}  "
                              f"interval {a.t_lo:%m-%d %H:%M:%S}..{a.t_hi:%m-%d %H:%M:%S}  state {model.states[a.state].label()}  "
                              f"conf {a.confidence:.2f}  anchors around: "
                              f"{prev.time.strftime('%m-%d %H:%M:%S') if prev else '-'} / {nxt.time.strftime('%m-%d %H:%M:%S') if nxt else '-'}")
            errs = [abs((sol.assignments[i].time - roll.frames[i].date).total_seconds()) / 3600 for i in held]
            widths = [(sol.assignments[i].t_hi - sol.assignments[i].t_lo).total_seconds() / 3600 for i in held]
            totals["held"] += len(held)
            totals["inside"] += inside
            totals["errs"] += errs
            totals["widths"] += widths
        x_mass = float(np.mean([a.outside_mass for a in sol.assignments]))
        print(f"{roll.key}  {len(roll.frames):2} frames  window {window.start:%m-%d}..{window.end:%m-%d} ({how})  "
              f"pool {len(pool)} in {len(evs)} events  anchors given {len(given)}  held-out {len(held)}")
        print(f"    proposal {len(model.states)} states  score {sol.log_score:8.1f}  null {null_score(model):8.1f}  "
              f"mean P(outside) {x_mass:.3f}")
        if held:
            print(f"    truth inside 90% interval {inside}/{len(held)}   median |err| {statistics.median(errs):6.1f} h   "
                  f"median width {statistics.median(widths):6.1f} h")
    if totals["held"]:
        print("=" * 70)
        print(f"mode={args.mode}: truth inside interval {totals['inside']}/{totals['held']} = "
              f"{100*totals['inside']/totals['held']:.1f}%   median |err| {statistics.median(totals['errs']):.1f} h   "
              f"median width {statistics.median(totals['widths']):.1f} h")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
