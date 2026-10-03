"""The frames whose true place has no photo in the window: what does the atlas say?"""
import collections
import numpy as np
from common import load_run, truth
from run_v2 import solve_v2
from filmgeo.events import haversine_m
T = collections.defaultdict(dict)
for x in truth(): T[x["roll"]][x["n"]] = x
for key, n in (("874466", 8), ("874466", 34), ("874466", 35), ("874472", 1), ("874472", 7), ("00000120", 3), ("00000126", 10), ("00000127", 1), ("00000127", 2)):
    r = load_run(key)
    model, sol, ev = solve_v2(r, verdicts=True, use_atlas=True)
    x = T[key][n]; i = n - 1; fe = ev.frames[i]; a = sol.assignments[i]
    near_truth = [k for k in range(len(fe.idx)) if not np.isnan(fe.lat[k]) and haversine_m((fe.lat[k], fe.lon[k]), (x["lat"], x["lon"])) <= 300]
    atlas_near = sum(1 for p in ev.photos[ev.n_pool:] if haversine_m((p.lat, p.lon), (x["lat"], x["lon"])) <= 300)
    err = haversine_m((a.lat, a.lon), (x["lat"], x["lon"])) if a.lat is not None else None
    print(f"{key}#{n}: atlas photos at the true place {atlas_near:3d}; in the frame's top-30: {len(near_truth)} (best rank {near_truth[0]+1 if near_truth else None}, weight {fe.w[near_truth].sum():.2f}); "
          f"q={fe.q:.2f} top={fe.sims[0]:.3f} hyp={[(round(h.mass,2), round(haversine_m((h.lat,h.lon),(x['lat'],x['lon']))/1000,1)) for h in fe.places[:3]]} -> {a.location_source} pc={a.place_confidence and round(a.place_confidence,2)} err={err and round(err)}")
