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
from filmgeo.events import haversine_m
from filmgeo.geo import place
from filmgeo.photos import library
from filmgeo.signals.base import Window, effective_window
from filmgeo.signals.photos_trail import PhotosTrail
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
    ap.add_argument("--shift-days", type=int, default=0,
                    help="move the window by this many days (a deliberately wrong window; forces --mode none)")
    args = ap.parse_args()
    tol = timedelta(seconds=args.tolerance)

    assets = library.load()
    nonfilm = library.phone_times(assets)
    cache = VectorCache("siglip")
    rolls = [r.clean() for r in eval_set.rolls(assets)][: args.rolls]
    totals = {"held": 0, "inside": 0, "errs": [], "widths": [], "loc": {}, "gps_err": [], "off_ok": [0, 0],
              "amb": [0, 0, 0], "n_clusters": []}   # ambiguous frames with truth: total, truth in any cluster, in the top one
    trail = PhotosTrail(assets)

    for roll in rolls:
        facts = RollFacts.load(roll.key)
        f_lo, f_hi = facts.window()
        cs = UserFacts(facts).constraints()
        if f_lo and f_hi:
            window, how = effective_window(cs, Window(f_lo, f_hi)), "facts"
        else:
            window, how = Window.around(roll.start, roll.end, args.pad_days), f"truth+/-{args.pad_days}d"
        if args.shift_days:
            window = Window(window.start + timedelta(days=args.shift_days), window.end + timedelta(days=args.shift_days))
            how += f" shifted {args.shift_days:+d}d"
            args.mode = "none"
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
        sol = place(solve(model), trail.trail_points(window))
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
        # Location and offset on held-out frames whose hand-tagged GPS exists.
        loc = {}
        gps_err = []
        off_ok = [0, 0]
        for i in held:
            a, f = sol.assignments[i], roll.frames[i]
            loc[a.location] = loc.get(a.location, 0) + 1
            if a.location == "ok" and f.has_location:
                gps_err.append(haversine_m((a.lat, a.lon), (f.lat, f.lon)))
            if a.location == "ambiguous" and f.has_location and a.clusters:
                hits = [haversine_m((c.lat, c.lon), (f.lat, f.lon)) <= 500 for c in a.clusters]
                totals["amb"][0] += 1
                totals["amb"][1] += any(hits)
                totals["amb"][2] += hits[0]
                totals["n_clusters"].append(len(a.clusters))
            if a.tzoffset is not None and f.tzoffset is not None:
                off_ok[1] += 1
                off_ok[0] += a.tzoffset == f.tzoffset
        for k, v in loc.items():
            totals["loc"][k] = totals["loc"].get(k, 0) + v
        totals["gps_err"] += gps_err
        totals["off_ok"][0] += off_ok[0]
        totals["off_ok"][1] += off_ok[1]
        x_mass = float(np.mean([a.outside_mass for a in sol.assignments]))
        top = sims.max(axis=1)
        margin = top - np.median(sims, axis=1)                     # how far the best candidate stands out
        z = (top - sims.mean(axis=1)) / sims.std(axis=1)
        print(f"    per-frame score above null {(sol.log_score - null_score(model)) / len(roll.frames):5.2f}   "
              f"best sim median {np.median(top):.3f}   margin over pool median {np.median(margin):.3f}   "
              f"z median {np.median(z):.2f}   frames z>=4: {int((z >= 4).sum())}/{len(roll.frames)}")
        print(f"{roll.key}  {len(roll.frames):2} frames  window {window.start:%m-%d}..{window.end:%m-%d} ({how})  "
              f"pool {len(pool)} in {len(evs)} events  anchors given {len(given)}  held-out {len(held)}")
        print(f"    proposal {len(model.states)} states  score {sol.log_score:8.1f}  null {null_score(model):8.1f}  "
              f"mean P(outside) {x_mass:.3f}")
        if held:
            print(f"    truth inside 90% interval {inside}/{len(held)}   median |err| {statistics.median(errs):6.1f} h   "
                  f"median width {statistics.median(widths):6.1f} h")
            print(f"    location {loc}   median GPS error where ok {statistics.median(gps_err)/1000:.2f} km ({len(gps_err)})   "
                  f"offset right {off_ok[0]}/{off_ok[1]}" if gps_err else f"    location {loc}   offset right {off_ok[0]}/{off_ok[1]}")
    if totals["held"]:
        print("=" * 70)
        print(f"mode={args.mode}: truth inside interval {totals['inside']}/{totals['held']} = "
              f"{100*totals['inside']/totals['held']:.1f}%   median |err| {statistics.median(totals['errs']):.1f} h   "
              f"median width {statistics.median(totals['widths']):.1f} h")
        n_loc = sum(totals["loc"].values())
        print("location: " + "  ".join(f"{k} {v}/{n_loc}" for k, v in sorted(totals["loc"].items()))
              + (f"   median GPS error where ok {statistics.median(totals['gps_err'])/1000:.2f} km, "
                 f"90th pct {np.percentile(totals['gps_err'], 90)/1000:.1f} km ({len(totals['gps_err'])} frames)" if totals["gps_err"] else "")
              + f"   offset right {totals['off_ok'][0]}/{totals['off_ok'][1]}")
        amb = totals["amb"]
        if amb[0]:
            print(f"ambiguous frames with a true pin: {amb[0]}; truth within 500 m of some offered cluster {amb[1]}/{amb[0]}, "
                  f"of the top cluster {amb[2]}/{amb[0]}; median clusters offered {statistics.median(totals['n_clusters']):.0f}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
