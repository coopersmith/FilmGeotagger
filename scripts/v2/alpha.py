"""P(the visit whose photo looks most like the frame is the true visit), by how similar that photo is."""
import collections
from datetime import datetime
import numpy as np
from common import ROLLS, load_run, truth
from filmgeo.events import haversine_m
T = collections.defaultdict(dict)
for x in truth(): T[x["roll"]][x["n"]] = x
rows = []
for key in ROLLS:
    r = load_run(key)
    evs = np.asarray(r.event_ids); uu = [a.uuid for a in r.pool]
    dates = np.array([a.date.timestamp() for a in r.pool])
    for i, f in enumerate(r.frames):
        x = T[key][f.number]
        if x["tier"] == "photo":
            te = {int(evs[uu.index(x["uuid"])])}
        elif x["tier"] in ("hand", "moment") and x["has_time"] and x["has_place"]:
            t = datetime.fromisoformat(x["t"]).timestamp()
            te = {int(evs[j]) for j in np.where(np.abs(dates - t) <= 5400)[0]
                  if r.pool[j].lat is not None and haversine_m((r.pool[j].lat, r.pool[j].lon), (x["lat"], x["lon"])) <= 300}
            if not te: continue
        else:
            continue
        j = int(np.argmax(r.sims[i]))
        rows.append((float(r.sims[i, j]), int(evs[j]) in te, x["tier"], x["by"]))
rows.sort()
s = np.array([r[0] for r in rows]); ok = np.array([r[1] for r in rows])
for lo, hi in ((0, .8), (.8, .84), (.84, .87), (.87, .9), (.9, .93), (.93, 1)):
    m = (s >= lo) & (s < hi)
    print(f"best photo's similarity {lo:.2f}-{hi:.2f}: n={m.sum():2d}  its visit is the true one {ok[m].sum():2d} ({100*ok[m].mean() if m.sum() else 0:.0f}%)")
for c, k in ((0.84, 30), (0.85, 40), (0.86, 40), (0.88, 40)):
    a = 1 / (1 + np.exp(-k * (s - c)))
    a = np.clip(a, 1e-3, 1 - 1e-3)
    print(f"logistic centre {c} slope {k}: log-lik {np.mean(np.where(ok, np.log(a), np.log(1 - a))):.3f}")
