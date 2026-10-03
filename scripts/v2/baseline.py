"""What the current engine proposes on its own — verdicts in, the user's overrides and frame
facts out — scored against the truth. This is the bar the new engine has to beat."""
from __future__ import annotations

import collections
import dataclasses
import pickle
import sys
from datetime import datetime

import numpy as np

from common import OUT, ROLLS, load_run, truth
from filmgeo.align import pipeline
from filmgeo.align.overrides import RollOverrides
from filmgeo.events import haversine_m
from filmgeo.signals.user_facts import RollFacts


def auto_run(r, verdicts=True):
    facts = dataclasses.replace(r.facts, frames={})
    return pipeline.solve_run(r.key, r.origin, r.frames, facts, r.window, r.window_source, r.pool, r.events, r.event_ids,
                              r.sims, r.candidates, r.verdicts if verdicts else {}, r.trail, r.outings, RollOverrides(r.key))


def score(name, results):
    """results: list of (truth_row, assignment-like with .time .lat .lon .source .confidence)."""
    T = [(x, a) for x, a in results if x["tier"] in ("photo", "hand", "moment")]
    place = [(x, a) for x, a in T if x["has_place"]]
    time = [(x, a) for x, a in T if x["has_time"]]
    pe = np.array([haversine_m((a.lat, a.lon), (x["lat"], x["lon"])) if a.lat is not None else 1e9 for x, a in place])
    te = np.array([abs((a.time - datetime.fromisoformat(x["t"])).total_seconds()) / 3600 for x, a in time])
    day = np.array([a.time.astimezone(datetime.fromisoformat(x["t"]).tzinfo).date() == datetime.fromisoformat(x["t"]).date() for x, a in time])
    miss = [(x, a) for x, a in place if x["by"] == "user"]
    me = np.array([haversine_m((a.lat, a.lon), (x["lat"], x["lon"])) if a.lat is not None else 1e9 for x, a in miss])
    print(f"{name:34s} place n={len(pe)}: <=300m {(pe<=300).sum():3d} ({100*(pe<=300).mean():.0f}%)  <=1km {(pe<=1000).sum():3d}  <=5km {(pe<=5000).sum():3d} | "
          f"misses n={len(me)}: <=300m {(me<=300).sum():2d} <=1km {(me<=1000).sum():2d} | "
          f"time n={len(te)}: right day {day.sum():3d} ({100*day.mean():.0f}%)  <=1h {(te<=1).sum():3d}  <=3h {(te<=3).sum():3d}  median {np.median(te):.2f} h")
    return dict(place_300=int((pe <= 300).sum()), place_1k=int((pe <= 1000).sum()), day=int(day.sum()), t1h=int((te <= 1).sum()))


def main():
    T = collections.defaultdict(dict)
    for x in truth():
        T[x["roll"]][x["n"]] = x
    for label, verdicts in (("v1 auto, with Claude verdicts", True), ("v1 auto, no Claude", False)):
        results = []
        for key in ROLLS:
            r = load_run(key)
            p = OUT / "runs" / f"{key}.v1{'v' if verdicts else 'n'}.pkl"
            if p.exists():
                sol = pickle.loads(p.read_bytes())
            else:
                sol = auto_run(r, verdicts).solution
                p.write_bytes(pickle.dumps(sol))
            results += [(T[key][f.number], a) for f, a in zip(r.frames, sol.assignments)]
        score(label, results)


if __name__ == "__main__":
    main()
