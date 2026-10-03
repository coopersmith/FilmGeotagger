"""How good are the places Claude already read off the frames? Geocode every cached verdict's
signage and place guess near the roll's trail and compare with the truth."""
import collections, sys
import numpy as np
from common import ROLLS, load_run, truth
from filmgeo.events import haversine_m
from filmgeo.gazetteer import Gazetteer, name_match, norm_tokens

T = collections.defaultdict(dict)
for x in truth(): T[x["roll"]][x["n"]] = x
from filmgeo.align.readings import from_verdicts
gz = Gazetteer()
rows = []
for key in ROLLS:
    r = load_run(key)
    for rd in from_verdicts(r.verdicts, r.pool, gz):
        n = rd.frame + 1
        x = T[key][n]
        err = haversine_m((rd.lat, rd.lon), (x["lat"], x["lon"])) if (x["tier"] in ("photo", "hand", "moment") and x["has_place"]) else None
        rows.append((key, n, rd.kind, rd.text, rd.name, err, x["tier"], x["by"]))
        print(f"{key}#{n:<2} {rd.kind:5s} {rd.text[:40]!r:44} -> {rd.name[:36]!r:40} {('%7.0f m' % err) if err is not None else '   no truth'}  [{x['tier']}/{x['by']}]", flush=True)
scored = [r for r in rows if r[5] is not None]
e = np.array([r[5] for r in scored])
print(f"\nreadings found for {len(rows)} of 137 frames; {len(scored)} have truth: within 300 m {(e<=300).sum()}, within 1 km {(e<=1000).sum()}, beyond 5 km {(e>5000).sum()}")
for kind in ("sign", "guess"):
    ee = np.array([r[5] for r in scored if r[2] == kind])
    if len(ee): print(f"  {kind}: n={len(ee)} within 300 m {(ee<=300).sum()} within 1 km {(ee<=1000).sum()} beyond 5 km {(ee>5000).sum()}")
