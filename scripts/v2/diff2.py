from datetime import datetime
from run_v2 import run
from filmgeo.events import haversine_m
a, da = run("", quiet=True, verdicts=False, readings=True)
b, db = run("", quiet=True, verdicts=True, layers=False)
def err(x, s): return haversine_m((s.lat, s.lon), (x["lat"], x["lon"])) if (s.lat is not None and x["lat"] is not None) else None
for (key, n, x, aa, fa), (_, _, _, bb, fb) in zip(da, db):
    if x["tier"] not in ("photo", "hand", "moment") or not x["has_place"]: continue
    ea, eb = err(x, aa), err(x, bb)
    oka, okb = ea is not None and ea <= 300, eb is not None and eb <= 300
    if oka != okb or not okb:
        print(f"{key}#{n:<2} {x['tier']}/{x['by']:6s} free: {('%.0f m' % ea) if ea is not None else 'none':>8} {aa.location_source} pc={aa.place_confidence and round(aa.place_confidence,2)} | claude: {('%.0f m' % eb) if eb is not None else 'none':>8} {bb.source}/{bb.location_source} pc={bb.place_confidence and round(bb.place_confidence,2)} name={bb.place_name}  q={fb.q:.2f} claude_conf={x['claude_conf']} match={'Y' if x['claude_match'] else 'N'}")
