import sys
from datetime import datetime
import numpy as np
from run_v2 import run
from filmgeo.align.evidence import EvidenceParams
from filmgeo.align.model import AlignParams
from filmgeo.events import haversine_m

_EV = {}
def true_event(key, uuid):
    if key not in _EV:
        from common import load_run
        r = load_run(key)
        _EV[key] = {a.uuid: e for a, e in zip(r.pool, r.event_ids)}
    return _EV[key].get(uuid)

def line(label, results, detail=None):
    T = [(x, a) for x, a in results if x["tier"] in ("photo", "hand", "moment")]
    pl = [(x, a) for x, a in T if x["has_place"]]; tm = [(x, a) for x, a in T if x["has_time"]]
    pe = np.array([haversine_m((a.lat, a.lon), (x["lat"], x["lon"])) if a.lat is not None else 1e9 for x, a in pl])
    miss = np.array([x["by"] == "user" for x, a in pl])
    te = np.array([abs((a.time - datetime.fromisoformat(x["t"])).total_seconds()) / 3600 for x, a in tm])
    day = np.array([a.time.astimezone(datetime.fromisoformat(x["t"]).tzinfo).date() == datetime.fromisoformat(x["t"]).date() for x, a in tm])
    photo = [(x, a) for x, a in T if x["tier"] == "photo"]
    occ = sum(a.event is not None and a.event == true_event(x["roll"], x["uuid"]) for x, a in photo)
    pday = sum(a.time.astimezone(datetime.fromisoformat(x["t"]).tzinfo).date() == datetime.fromisoformat(x["t"]).date() for x, a in photo)
    placed = sum(a.lat is not None for x, a in results)
    wrong = int(((pe > 1000) & (pe < 1e8)).sum())
    print(f"{label:52s} place<=300m {(pe<=300).sum():2d}/{len(pe)} misses {(pe[miss]<=300).sum():2d}/{miss.sum()} wrong>1km {wrong:2d} | occasion {occ:2d}/{len(photo)} day(photo) {pday:2d} day(all) {day.sum():2d}/{len(day)} <=1h {(te<=1).sum():2d} | placed {placed}/137", flush=True)

if __name__ == "__main__":
    atlas = "--atlas" in sys.argv
    res, _ = run("", quiet=True, verdicts=False, use_atlas=atlas); line("v2 free", res)
    res, _ = run("", quiet=True, verdicts=False, readings=True, use_atlas=atlas); line("v2 free + readings (as from a read-the-frame call)", res)
    res, _ = run("", quiet=True, verdicts=True, readings=False, use_atlas=atlas); line("v2 + Claude verdicts, no readings", res)
    res, _ = run("", quiet=True, verdicts=True, use_atlas=atlas); line("v2 + Claude verdicts + readings", res)
    res, _ = run("", quiet=True, verdicts=True, use_atlas=atlas, layers=True); line("v2 + Claude + readings + recorded visits", res)
