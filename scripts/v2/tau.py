import collections
import numpy as np
from common import ROLLS, load_run, truth
T = collections.defaultdict(dict)
for x in truth(): T[x["roll"]][x["n"]] = x
data = []
for key in ROLLS:
    r = load_run(key)
    evs = np.asarray(r.event_ids); uu = [a.uuid for a in r.pool]
    ne = evs.max() + 1
    for i, f in enumerate(r.frames):
        x = T[key][f.number]
        if x["tier"] != "photo": continue
        best = np.full(ne, -1.0)
        np.maximum.at(best, evs, r.sims[i])
        data.append((best, int(evs[uu.index(x["uuid"])])))
for tau in (0.005, 0.01, 0.015, 0.02, 0.03, 0.05):
    ll, top = [], []
    for best, te in data:
        w = np.exp((best - best.max()) / tau); p = w / w.sum()
        ll.append(np.log(max(p[te], 1e-6))); top.append(p.max())
    print(f"tau {tau}: mean log-lik of the true occasion {np.mean(ll):6.2f}   mean share given to the top event {np.mean(top):.2f}  (top-1 is right {np.mean([np.argmax(b)==t for b,t in data]):.2f})")
