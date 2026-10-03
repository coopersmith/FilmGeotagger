from __future__ import annotations
import collections, json
from datetime import datetime
import numpy as np
from common import ROLLS, load_run, truth
from filmgeo.embed.cache import VectorCache
from filmgeo.events import haversine_m
from filmgeo.photos import library
from place_knn import vote

T = collections.defaultdict(dict)
for x in truth():
    T[x["roll"]][x["n"]] = x

# --- Q2: centring each domain (frames, phone photos) before the cosine
cache = VectorCache("siglip")
def evaluate(label, transform):
    ok300 = n = top1 = nocc = 0
    for key in ROLLS:
        r = load_run(key)
        fv = cache.get([f.key for f in r.frames]); pv = cache.get([a.uuid for a in r.pool])
        sims = transform(fv, pv)
        ev = np.asarray(r.event_ids)
        uu = [a.uuid for a in r.pool]
        for i, f in enumerate(r.frames):
            x = T[key][f.number]
            if x["tier"] not in ("photo", "hand", "moment") or not x["has_place"]:
                continue
            lat, lon, share, top = vote(sims[i], r.pool, temp=0.03 * (sims[i].std() / 0.06))
            n += 1; ok300 += haversine_m((lat, lon), (x["lat"], x["lon"])) <= 300
            if x["tier"] == "photo":
                nocc += 1
                top1 += ev[int(np.argmax(sims[i]))] == ev[uu.index(x["uuid"])]
    print(f"{label:40s} place<=300m {ok300}/{n}   top-1 photo in true event {top1}/{nocc}")

def raw(fv, pv): return fv @ pv.T
def centred(fv, pv):
    f = fv - fv.mean(0); p = pv - pv.mean(0)
    f /= np.linalg.norm(f, axis=1, keepdims=True); p /= np.linalg.norm(p, axis=1, keepdims=True)
    return f @ p.T
def pool_centred(fv, pv):
    m = pv.mean(0); f = fv - m; p = pv - m
    f /= np.linalg.norm(f, axis=1, keepdims=True); p /= np.linalg.norm(p, axis=1, keepdims=True)
    return f @ p.T
def hub(fv, pv):                       # CSLS-style: discount photos that are similar to every frame
    s = fv @ pv.T
    return s - 0.5 * s.mean(0, keepdims=True) - 0.5 * s.mean(1, keepdims=True)
evaluate("raw cosine (now)", raw)
evaluate("each domain centred", centred)
evaluate("both centred on the pool mean", pool_centred)
evaluate("hub-corrected", hub)

# --- Q3: places with no phone photo in the window: photographed at any other time?
assets = library.load()
phone = [a for a in assets if a.lat is not None and not a.is_scan]
print(f"\nlibrary: {len(assets)} assets, {len(phone)} located phone photos; siglip cache holds {len(cache.keys)}")
diag = json.loads(open(__import__('common').OUT / "diag.json").read())
for d in diag:
    if d["n_occ"] == 0 or (d["place_rank"] and d["place_rank"] > 50):
        x = T[d["roll"]][d["n"]]
        if x["lat"] is None: continue
        near = [a for a in phone if abs(a.lat - x["lat"]) < 0.01 and haversine_m((a.lat, a.lon), (x["lat"], x["lon"])) <= 300]
        cached = sum(a.uuid in cache.index for a in near)
        years = collections.Counter(a.date.year for a in near)
        print(f"  {d['roll']}#{d['n']:<2} photos within 300 m of the true place, all time: {len(near):4d} (embedded: {cached}) {dict(sorted(years.items()))}")
