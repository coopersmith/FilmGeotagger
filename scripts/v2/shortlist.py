"""Is the v2 posterior a better shortlist for verification than raw similarity (what verify shows now)?
For frames whose true occasion is known: the rank of the true event by each ordering."""
import collections
import numpy as np
from common import ROLLS, load_run, truth
from run_v2 import solve_v2

T = collections.defaultdict(dict)
for x in truth(): T[x["roll"]][x["n"]] = x
ranks_sim, ranks_post, mass_top = [], [], []
skippable = total = 0
for key in ROLLS:
    r = load_run(key)
    model, sol, ev = solve_v2(r, verdicts=False)
    evs = np.asarray(r.event_ids)
    uu = [a.uuid for a in r.pool]
    for i, f in enumerate(r.frames):
        x = T[key][f.number]
        total += 1
        # posterior mass per event
        pm = collections.defaultdict(float)
        for j, s in enumerate(model.states):
            if s.event is not None:
                pm[s.event] += sol.posterior[i, j]
        order_post = sorted(pm, key=lambda e: -pm[e])
        if pm and pm[order_post[0]] >= 0.9:
            skippable += 1
        if x["tier"] != "photo":
            continue
        true_e = int(evs[uu.index(x["uuid"])])
        order = np.argsort(-r.sims[i]); seen = []
        for j in order:
            e = int(evs[j])
            if e not in seen: seen.append(e)
            if e == true_e: break
        ranks_sim.append(len(seen))
        ranks_post.append(order_post.index(true_e) + 1 if true_e in order_post else 999)
        mass_top.append((pm[order_post[0]], order_post[0] == true_e))
rs, rp = np.array(ranks_sim), np.array(ranks_post)
print(f"frames with a known true occasion: {len(rs)}")
for k in (1, 2, 3, 4, 6, 8, 12):
    print(f"  true occasion within top-{k:<2}: by similarity (now) {(rs<=k).sum():2d}   by v2 posterior {(rp<=k).sum():2d}")
mt = np.array([m for m, _ in mass_top]); ok = np.array([o for _, o in mass_top])
for th in (0.5, 0.7, 0.9, 0.95):
    sel = mt >= th
    print(f"  top event's posterior >= {th}: {sel.sum():2d} frames, top is the true occasion in {ok[sel].sum():2d}")
print(f"all 137 frames: {skippable} have one event holding >= 0.9 of the posterior")
