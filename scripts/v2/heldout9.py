"""Held-out, free mode only: all hand-tagged rolls whose windows are embedded (no verdicts for most)."""
from filmgeo import config, eval_set
from filmgeo.align import pipeline
from filmgeo.align.overrides import RollOverrides
from filmgeo.events import haversine_m
from filmgeo.photos import library
from filmgeo.signals.user_facts import RollFacts

assets = library.load()
phone = library.phone_times(assets)
by_time = {}
for a in assets:
    if not a.is_scan:
        by_time.setdefault(int(a.date.timestamp()), a)
tot = {"v1": [0, 0, 0, 0, 0], "v2": [0, 0, 0, 0, 0]}
used = 0
for roll in [r.clean() for r in eval_set.rolls(assets)]:
    anchored = set(roll.anchored(phone))
    if not anchored or roll.start.year < 2026:
        continue
    row = {}
    try:
        for engine in ("v1", "v2"):
            config.ENGINE = engine
            r = pipeline.run(roll.key, assets=assets, alias="__heldout__", overrides=RollOverrides("__heldout__"), facts=RollFacts("__heldout__"), lookup=False)
            n = day = h1 = p300 = placed = 0
            for i, (f, a) in enumerate(zip(r.frames, r.solution.assignments)):
                if i not in anchored: continue
                n += 1
                t = roll.frames[i].date
                day += a.time.astimezone(t.tzinfo).date() == t.date(); h1 += abs((a.time - t).total_seconds()) <= 3600
                photo = next((by_time[int(t.timestamp()) + d] for d in (0, -1, 1, -2, 2) if int(t.timestamp()) + d in by_time), None)
                if photo is not None and photo.lat is not None:
                    placed += 1
                    p300 += a.lat is not None and haversine_m((a.lat, a.lon), (photo.lat, photo.lon)) <= 300
            row[engine] = (n, day, h1, p300, placed)
    except Exception as e:
        print(f"{roll.key}: skipped ({type(e).__name__}: {str(e)[:70]})"); continue
    used += 1
    for engine, v in row.items():
        for k in range(5): tot[engine][k] += v[k]
    print(f"{roll.key} ({len(roll.frames)} frames, {row['v1'][0]} with anchored truth): v1 day {row['v1'][1]} place {row['v1'][3]}/{row['v1'][4]} | v2 day {row['v2'][1]} 1h {row['v2'][2]} place {row['v2'][3]}/{row['v2'][4]}", flush=True)
print(f"\n{used} rolls, no verification:")
for engine, (n, day, h1, p300, placed) in tot.items():
    print(f"  {engine}: right day {day}/{n}, within 1 h {h1}/{n}, place within 300 m {p300}/{placed}")
