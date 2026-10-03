"""Held-out check: the two hand-tagged rolls (never used to shape v2). Truth = frames the user
anchored to a phone photo by hand (their timestamp lands on a phone photo's second), with that
photo's GPS. Unaided proposals: no frame facts, no overrides."""
import dataclasses
from datetime import timedelta
import numpy as np
from filmgeo import config, eval_set
from filmgeo.align import pipeline
from filmgeo.align.overrides import RollOverrides
from filmgeo.events import haversine_m
from filmgeo.photos import library

assets = library.load()
phone = library.phone_times(assets)
by_time = {}
for a in assets:
    if not a.is_scan:
        by_time.setdefault(int(a.date.timestamp()), a)
rolls = {r.key: r.clean() for r in eval_set.rolls(assets)}
tot = {}
for key, alias in (("00007037", None), ("00007044", "00007044-k12")):
    roll = rolls[key]
    anchored = set(roll.anchored(phone))
    for engine in ("v1", "v2"):
        for verdicts in (False, True):
            config.ENGINE = engine
            r = pipeline.run(key, assets=assets, alias=alias, overrides=RollOverrides(alias or key),
                             facts=dataclasses.replace(pipeline.RollFacts.load(alias or key), frames={}))
            if not verdicts:
                r = pipeline.solve_run(r.key, r.origin, r.frames, r.facts, r.window, r.window_source, r.pool, r.events, r.event_ids, r.sims,
                                       r.candidates, {}, r.trail, None, RollOverrides(r.key), r.evidence)
            n = day = h1 = p300 = placed = 0
            for i, (f, a) in enumerate(zip(r.frames, r.solution.assignments)):
                if i not in anchored: continue
                n += 1
                t = roll.frames[i].date
                dt = abs((a.time - t).total_seconds())
                day += a.time.astimezone(t.tzinfo).date() == t.date(); h1 += dt <= 3600
                photo = min((by_time.get(int(t.timestamp()) + d) for d in (0, -1, 1, -2, 2) if by_time.get(int(t.timestamp()) + d)), key=lambda p: abs(p.date - t), default=None)
                if photo is not None and photo.lat is not None:
                    placed += 1
                    p300 += a.lat is not None and haversine_m((a.lat, a.lon), (photo.lat, photo.lon)) <= 300
            lab = f"{engine} {'+ Claude' if verdicts else 'free   '}"
            k = tot.setdefault(lab, [0, 0, 0, 0, 0]); k[0] += n; k[1] += day; k[2] += h1; k[3] += p300; k[4] += placed
            print(f"{key} {lab}: anchored-truth frames {n}: right day {day}, within 1 h {h1}, place within 300 m {p300}/{placed}", flush=True)
print()
for lab, (n, day, h1, p300, placed) in tot.items():
    print(f"BOTH ROLLS {lab}: right day {day}/{n}, within 1 h {h1}/{n}, place within 300 m {p300}/{placed}")
