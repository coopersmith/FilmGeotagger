import itertools, sys, time
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
    if which == "reach":
        for reach in (5_000, 10_000, 25_000, 150_000):
            for verdicts in (False, True):
                res, _ = run("", quiet=True, verdicts=verdicts, use_atlas=True, layers=verdicts, ep=EvidenceParams(atlas_reach_m=reach))
                line(f"atlas reach {reach//1000:3d} km claude+readings+layers={verdicts}", res)
        for verdicts in (False, True):
            res, _ = run("", quiet=True, verdicts=verdicts, use_atlas=False, layers=verdicts); line(f"no atlas claude+readings+layers={verdicts}", res)
    if which == "scale":
        for verdicts in (False, True):
            for sc, jw in itertools.product((0.3, 0.5, 0.7, 1.0), (0.35, 0.7)):
                res, _ = run("", quiet=True, verdicts=verdicts, layers=verdicts, ap=AlignParams(evidence_scale=sc, jump_weight=jw))
                line(f"claude+layers={verdicts!s:5} evidence_scale={sc} jump={jw}", res)
    if which == "gate":
        for verdicts in (False, True):
            for qm, pm in itertools.product((0.5, 0.6, 0.7, 0.8), (0.4, 0.5, 0.6, 0.7)):
                res, _ = run("", quiet=True, verdicts=verdicts, readings=True, q_min=qm, place_min=pm)
                line(f"claude={verdicts!s:5} +readings q_min={qm} place_min={pm}", res)
    if which == "rho":
        for verdicts in (False, True):
            for rho, sd in itertools.product((0.15, 0.3, 0.5, 0.7), (0.6, 1.0)):
                res, _ = run("", quiet=True, verdicts=verdicts, readings=True, q_min=0.6, ep=EvidenceParams(other_visit=rho, stay_discount=sd))
                line(f"claude={verdicts!s:5} +readings other_visit={rho} stay={sd}", res)
    if which == "decode":
        for dec, alo in itertools.product(("viterbi", "posterior"), (0.3, 0.5)):
            for verdicts, rd, ly in ((False, False, False), (False, True, False), (True, True, False), (True, True, True)):
                res, _ = run("", quiet=True, verdicts=verdicts, readings=rd, layers=ly, q_min=0.6, ap=AlignParams(decode=dec), ep=EvidenceParams(alpha_lo=alo))
                line(f"{dec:9s} alpha_lo={alo} claude={verdicts!s:5} readings={rd!s:5} layers={ly!s:5}", res)
    if which == "form":
        for form, dec in itertools.product(("ratio", "calibrated"), ("viterbi", "posterior")):
            for verdicts, rd, ly in ((False, False, False), (False, True, False), (True, True, False), (True, True, True)):
                res, _ = run("", quiet=True, verdicts=verdicts, readings=rd, layers=ly, ap=AlignParams(decode=dec), ep=EvidenceParams(occasion_form=form))
                line(f"{form:10s} {dec:9s} claude={verdicts!s:5} readings={rd!s:5} layers={ly!s:5}", res)
    if which == "rhoalpha":
        for oba in (0.0, 0.25, 0.5):
            for verdicts, rd in ((False, False), (True, True)):
                res, _ = run("", quiet=True, verdicts=verdicts, readings=rd, ep=EvidenceParams(other_by_alpha=oba))
                line(f"other_by_alpha={oba} claude+readings={verdicts}", res)
