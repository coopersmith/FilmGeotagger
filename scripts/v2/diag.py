"""Where is each frame lost? For every frame with strong truth: is there a phone photo of the
true occasion at all, how does SigLIP rank it, was it shown to Claude, what did Claude say."""
from __future__ import annotations

import collections
from datetime import datetime, timedelta

import numpy as np

from common import ROLLS, load_run, truth
from filmgeo.events import haversine_m

NEAR_M = 300
NEAR_T = timedelta(minutes=90)


def main():
    T = collections.defaultdict(dict)
    for x in truth():
        T[x["roll"]][x["n"]] = x
    rows = []
    for key in ROLLS:
        r = load_run(key)
        ev = np.asarray(r.event_ids)
        pool = r.pool
        dates = np.array([a.date.timestamp() for a in pool])
        for i, f in enumerate(r.frames):
            x = T[key].get(f.number)
            if not x or x["tier"] not in ("photo", "hand", "moment"):
                continue
            t = datetime.fromisoformat(x["t"]).timestamp()
            sims = r.sims[i]
            order = np.argsort(-sims)
            rank_of = np.empty(len(order), int); rank_of[order] = np.arange(len(order))
            # event ranking, cap 1: an event's rank is the rank of its best photo among events
            best_by_event = {}
            for j in order:
                best_by_event.setdefault(int(ev[j]), len(best_by_event))
            near_place = np.array([a.lat is not None and x["lat"] is not None and haversine_m((a.lat, a.lon), (x["lat"], x["lon"])) <= NEAR_M for a in pool])
            near_time = np.abs(dates - t) <= NEAR_T.total_seconds()
            if x["tier"] == "photo":
                true_occ = (ev == ev[[a.uuid for a in pool].index(x["uuid"])])
            elif x["has_time"] and x["has_place"]:
                true_occ = near_place & near_time
            elif x["has_place"]:
                true_occ = near_place          # the place, any day: location truth only
            else:
                true_occ = near_time
            shown = [c.asset.uuid for c in r.candidates.get(f.number, [])]
            v = r.verdicts.get(f.number)
            vshown = set(v.candidates) if v else set()
            occ_idx = np.where(true_occ)[0]
            row = dict(roll=key, n=f.number, tier=x["tier"], by=x["by"], has_time=x["has_time"], has_place=x["has_place"],
                       n_occ=len(occ_idx), n_place=int(near_place.sum()),
                       photo_rank=int(rank_of[occ_idx].min()) + 1 if len(occ_idx) else None,
                       event_rank=min(best_by_event[int(ev[j])] for j in occ_idx) + 1 if len(occ_idx) else None,
                       place_rank=int(rank_of[np.where(near_place)[0]].min()) + 1 if near_place.any() else None,
                       best_sim=float(sims[occ_idx].max()) if len(occ_idx) else None, top_sim=float(sims.max()),
                       shown=bool(vshown & {pool[j].uuid for j in occ_idx}),
                       claude=("match" if v and v.match else "none") if v else "unverified",
                       claude_right=bool(v and v.match and v.match in {pool[j].uuid for j in occ_idx}),
                       conf=v.confidence if v else None)
            rows.append(row)
    import json
    from common import OUT
    (OUT / "diag.json").write_text(json.dumps(rows, indent=1))

    def show(title, rr):
        print(f"\n== {title}: {len(rr)} frames")
        no_photo = [x for x in rr if x["n_occ"] == 0]
        print(f"   no phone photo of the true occasion in the window: {len(no_photo)}")
        have = [x for x in rr if x["n_occ"]]
        for k in (1, 3, 6, 12, 24, 48):
            print(f"   true occasion in top-{k:<2} events: {sum(x['event_rank'] <= k for x in have):3d}/{len(have)}")
        shown = [x for x in have if x["shown"]]
        print(f"   shown to Claude: {len(shown)}/{len(have)};  Claude right: {sum(x['claude_right'] for x in have)};"
              f"  Claude said none though shown: {sum(x['claude']=='none' for x in shown)};"
              f"  Claude matched elsewhere: {sum(x['claude']=='match' and not x['claude_right'] for x in have)}")

    show("all strong truth", rows)
    show("found by Claude (confirmed)", [x for x in rows if x["by"] == "claude"])
    show("supplied by the user (the misses)", [x for x in rows if x["by"] == "user"])
    print("\nthe misses, one by one:")
    for x in rows:
        if x["by"] == "user":
            print(f"  {x['roll']}#{x['n']:<2} {x['tier']:<6} time={x['has_time']!s:<5} place={x['has_place']!s:<5} occ_photos={x['n_occ']:<3} place_photos={x['n_place']:<4} "
                  f"event_rank={x['event_rank']} place_rank={x['place_rank']} best_sim={x['best_sim'] and round(x['best_sim'],3)} top_sim={round(x['top_sim'],3)} shown={x['shown']} claude={x['claude']} conf={x['conf']}")


if __name__ == "__main__":
    main()
