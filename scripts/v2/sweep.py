import itertools, sys, time
from datetime import datetime
import numpy as np
from run_v2 import run
from filmgeo.align.evidence import EvidenceParams
from filmgeo.align.model import AlignParams
from filmgeo.events import haversine_m

def line(label, results):
    T = [(x, a) for x, a in results if x["tier"] in ("photo", "hand", "moment")]
    pl = [(x, a) for x, a in T if x["has_place"]]; tm = [(x, a) for x, a in T if x["has_time"]]
    pe = np.array([haversine_m((a.lat, a.lon), (x["lat"], x["lon"])) if a.lat is not None else 1e9 for x, a in pl])
    miss = np.array([x["by"] == "user" for x, a in pl])
    te = np.array([abs((a.time - datetime.fromisoformat(x["t"])).total_seconds()) / 3600 for x, a in tm])
    day = np.array([a.time.astimezone(datetime.fromisoformat(x["t"]).tzinfo).date() == datetime.fromisoformat(x["t"]).date() for x, a in tm])
    placed = sum(a.lat is not None for x, a in results)
    wrong = int(((pe > 1000) & (pe < 1e8)).sum())
    print(f"{label:58s} place<=300m {(pe<=300).sum():2d}/{len(pe)} misses {(pe[miss]<=300).sum():2d}/{miss.sum()} wrong>1km {wrong:2d} | day {day.sum():2d}/{len(day)} <=1h {(te<=1).sum():2d} | placed {placed}/137", flush=True)

if __name__ == "__main__":
    which = sys.argv[1] if len(sys.argv) > 1 else "occ"
    atlas = "--atlas" in sys.argv
    if which == "occ":
        for verdicts in (False, True):
            for rho in (0.05, 0.1, 0.15, 0.25, 0.4):
                res, _ = run("", quiet=True, verdicts=verdicts, use_atlas=atlas, ep=EvidenceParams(other_visit=rho))
                line(f"claude={verdicts!s:5} other_visit={rho}", res)
    if which == "pmin":
        for verdicts in (False, True):
            for pm in (0.3, 0.4, 0.5, 0.6, 0.7, 0.8):
                res, _ = run("", quiet=True, verdicts=verdicts, use_atlas=atlas, place_min=pm)
                line(f"claude={verdicts!s:5} place_min={pm}", res)
    if which == "atlas":
        for verdicts in (False, True):
            for use in (False, True):
                res, _ = run("", quiet=True, verdicts=verdicts, use_atlas=use)
                line(f"claude={verdicts!s:5} atlas={use}", res)
    if which == "main":
        res, _ = run("", quiet=True, verdicts=False, use_atlas=atlas); line("v2 free", res)
        res, _ = run("", quiet=True, verdicts=True, readings=False, use_atlas=atlas); line("v2 + Claude verdicts, no readings", res)
        res, _ = run("", quiet=True, verdicts=True, use_atlas=atlas); line("v2 + Claude verdicts + readings", res)
        res, _ = run("", quiet=True, verdicts=False, readings=True, use_atlas=atlas); line("v2 free + readings only (a read-the-frame call)", res)
        res, _ = run("", quiet=True, verdicts=True, use_atlas=atlas, layers=True); line("v2 + Claude + readings + optional layers (check-ins)", res)
