"""Do the confidences mean what they say? Occasion confidence against occasion-right, place confidence against place-right."""
import sys
import numpy as np
from run_v2 import run
from sweep import true_event
from filmgeo.align.evidence import EvidenceParams
from filmgeo.align.model import AlignParams
from filmgeo.events import haversine_m
for label, kw in (("free", dict(verdicts=False)), ("free + readings", dict(verdicts=False, readings=True)), ("Claude + readings", dict(verdicts=True)), ("Claude + readings + layers", dict(verdicts=True, layers=True))):
    res, detail = run("", quiet=True, **kw)
    occ, plc = [], []
    for key, n, x, a, fe in detail:
        if x["tier"] == "photo":
            occ.append((a.confidence, a.event == true_event(key, x["uuid"])))
        if x["tier"] in ("photo", "hand", "moment") and x["has_place"] and a.place_confidence is not None and a.source not in ("anchored", "locked"):
            ok = a.lat is not None and haversine_m((a.lat, a.lon), (x["lat"], x["lon"])) <= 300
            plc.append((a.place_confidence, ok, a.lat is not None))
    print(f"== {label}")
    c = np.array([o[0] for o in occ]); r = np.array([o[1] for o in occ])
    for lo, hi in ((0, .3), (.3, .6), (.6, .8), (.8, .95), (.95, 1.01)):
        m = (c >= lo) & (c < hi)
        print(f"   occasion confidence {lo:.2f}-{hi:.2f}: {m.sum():2d} frames, right {r[m].sum():2d}" + (f" ({100*r[m].mean():.0f}%)" if m.sum() else ""))
    all_conf = np.array([a.confidence for _, _, _, a, _ in detail])
    print(f"   all 137 frames: confidence >= 0.8 on {(all_conf>=0.8).sum()}, >= 0.6 on {(all_conf>=0.6).sum()}")
    c = np.array([o[0] for o in plc]); r = np.array([o[1] for o in plc])
    for lo, hi in ((0, .4), (.4, .6), (.6, .8), (.8, .95), (.95, 1.01)):
        m = (c >= lo) & (c < hi)
        print(f"   place confidence {lo:.2f}-{hi:.2f} (unanchored): {m.sum():2d} frames, within 300 m {r[m].sum():2d}" + (f" ({100*r[m].mean():.0f}%)" if m.sum() else ""))
