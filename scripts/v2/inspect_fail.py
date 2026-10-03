import sys
from datetime import datetime
import numpy as np
from common import load_run
from run_v2 import run
from filmgeo.align.evidence import EvidenceParams
from filmgeo.events import haversine_m
verdicts = "--claude" in sys.argv
res, detail = run("", quiet=True, verdicts=verdicts, use_atlas="--atlas" in sys.argv, ep=EvidenceParams())
for key, n, x, a, fe in detail:
    if x["tier"] not in ("photo", "hand", "moment"): continue
    tt = datetime.fromisoformat(x["t"])
    dt = (a.time - tt).total_seconds() / 3600
    pe = haversine_m((a.lat, a.lon), (x["lat"], x["lon"])) if (a.lat is not None and x["lat"] is not None) else None
    bad_t = x["has_time"] and abs(dt) > 3
    bad_p = x["has_place"] and (pe is None or pe > 300)
    if not (bad_t or bad_p): continue
    r = load_run(key)
    top = r.pool[fe.idx[0]] if fe.idx[0] < len(r.pool) else None
    print(f"{key}#{n:<2} {x['tier']}/{x['by']:6s} truth {tt:%m-%d %H:%M} got {a.time.astimezone(tt.tzinfo):%m-%d %H:%M} dt {dt:+7.1f}h  place_err {('%.0f m' % pe) if pe is not None else 'none':>9}  src={a.location_source} pc={a.place_confidence and round(a.place_confidence,2)} conf={a.confidence:.2f} "
          f"q={fe.q:.2f} top={fe.sims[0]:.3f} top@{(top.date.astimezone(tt.tzinfo).strftime('%m-%d %H:%M') if top else 'atlas')} places={[(round(h.mass,2)) for h in fe.places[:3]]}")
