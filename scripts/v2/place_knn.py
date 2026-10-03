"""How well does visual similarity alone recover *where*, ignoring when?
Predicted place = winner of a similarity-weighted vote among the top-k photos, grouped by distance."""
from __future__ import annotations

import collections
import numpy as np

from common import ROLLS, load_run, truth
from filmgeo.events import haversine_m


def vote(sims, pool, k=20, radius=300.0, temp=0.03):
    order = [j for j in np.argsort(-sims) if pool[j].lat is not None][:k]
    w = np.exp((sims[order] - sims[order[0]]) / temp)
    clusters = []            # (lat, lon, weight, members)
    for j, wj in zip(order, w):
        p = (pool[j].lat, pool[j].lon)
        for c in clusters:
            if haversine_m(p, (c[0], c[1])) <= radius:
                c[2] += wj; c[3].append(j); break
        else:
            clusters.append([p[0], p[1], wj, [j]])
    clusters.sort(key=lambda c: -c[2])
    tot = sum(c[2] for c in clusters)
    return clusters[0][0], clusters[0][1], clusters[0][2] / tot, float(sims[order[0]])


def main():
    T = collections.defaultdict(dict)
    for x in truth():
        T[x["roll"]][x["n"]] = x
    res = []
    for key in ROLLS:
        r = load_run(key)
        for i, f in enumerate(r.frames):
            x = T[key].get(f.number)
            lat, lon, share, top = vote(r.sims[i], r.pool)
            a = r.solution.assignments[i]
            row = dict(roll=key, n=f.number, tier=x["tier"], by=x["by"], share=share, top=top)
            if x["tier"] in ("photo", "hand", "moment") and x["has_place"]:
                row["err"] = haversine_m((lat, lon), (x["lat"], x["lon"]))
            res.append(row)
    scored = [x for x in res if "err" in x]
    print(f"frames with place truth: {len(scored)}")
    for name, sel in (("all", scored), ("found by Claude", [x for x in scored if x["by"] == "claude"]), ("the misses (user-supplied)", [x for x in scored if x["by"] == "user"])):
        e = np.array([x["err"] for x in sel])
        print(f"  {name:28s} n={len(sel):3d}  within 300 m: {(e<=300).sum():3d}  within 1 km: {(e<=1000).sum():3d}  within 5 km: {(e<=5000).sum():3d}  median {np.median(e):8.0f} m")
    print("\nprecision by confidence (vote share x top similarity):")
    for s_min, t_min in ((0.0, 0.0), (0.5, 0.0), (0.7, 0.0), (0.9, 0.0), (0.5, 0.8), (0.7, 0.8), (0.7, 0.85), (0.9, 0.85)):
        sel = [x for x in scored if x["share"] >= s_min and x["top"] >= t_min]
        allsel = [x for x in res if x["share"] >= s_min and x["top"] >= t_min]
        e = np.array([x["err"] for x in sel]) if sel else np.array([])
        print(f"  share>={s_min:.1f} top>={t_min:.2f}: covers {len(allsel):3d}/137 frames; of {len(sel):2d} with truth, {(e<=300).sum():2d} within 300 m, {(e<=1000).sum():2d} within 1 km ({100*(e<=1000).mean() if len(e) else 0:.0f}%)")
    print("\nfrailest: truth frames the vote gets wrong by > 1 km")
    for x in scored:
        if x["err"] > 1000:
            print(f"  {x['roll']}#{x['n']:<2} {x['tier']}/{x['by']} err {x['err']/1000:7.1f} km share {x['share']:.2f} top {x['top']:.3f}")
    unk = [x for x in res if x["tier"] in ("weak", "unconfirmed")]
    print(f"\nframes with no truth (accepted interpolations + unconfirmed): {len(unk)}; share>=0.7 & top>=0.8: {sum(x['share']>=0.7 and x['top']>=0.8 for x in unk)}")


if __name__ == "__main__":
    main()
