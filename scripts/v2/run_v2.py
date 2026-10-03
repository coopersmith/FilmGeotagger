"""The place-first engine on the reviewed rolls, unaided (no user facts, no overrides), scored
against the truth beside the v1 baseline."""
from __future__ import annotations

import argparse
import collections
import dataclasses
import pickle
import time

import numpy as np

from baseline import score
from common import OUT, ROLLS, load_run, truth
from filmgeo.align import evidence as evmod
from filmgeo.align.checks import RollInputs
from filmgeo.align.locate import locate
from filmgeo.align.model import AlignParams
from filmgeo.align.pipeline import anchors_from_verdicts, clues_from_verdicts
from filmgeo.align.solve import solve
from filmgeo.embed.cache import VectorCache
from filmgeo.geo import place
from filmgeo.photos import library
from filmgeo.signals.user_facts import UserFacts

_cache = None
_assets = None


def cache():
    global _cache
    if _cache is None:
        _cache = VectorCache("siglip")
    return _cache


def atlas_for(r, use_atlas: bool, ep):
    if not use_atlas:
        return [], None
    global _assets
    if _assets is None:
        _assets = library.load()
    c = cache()
    inpool = {a.uuid for a in r.pool}
    cand = [a for a in _assets if a.uuid in c.index and a.uuid not in inpool and a.lat is not None and not a.is_scan
            and "/scopes/syndication/" not in (a.derivative or "")]
    cand = evmod.reachable_atlas(r.pool, cand, ep.atlas_reach_m)
    vecs = c.get([a.uuid for a in cand])
    ok = np.linalg.norm(vecs, axis=1) > 0.5
    return [a for a, k in zip(cand, ok) if k], vecs[ok]


def solve_v2(r, verdicts=True, use_atlas=False, ep=None, ap=None, place_min=0.5):
    ep = ep or evmod.EvidenceParams()
    c = cache()
    fv = c.get([f.key for f in r.frames])
    pv = c.get([a.uuid for a in r.pool])
    atlas, av = atlas_for(r, use_atlas, ep)
    ev = evmod.build(fv, r.pool, pv, r.event_ids, atlas, av, ep)
    facts = dataclasses.replace(r.facts, frames={})
    n = len(r.frames)
    vd = r.verdicts if verdicts else {}
    anchors = anchors_from_verdicts(vd, r.pool, r.event_ids, r.sims)
    inputs = RollInputs(r.window, r.events, n, anchors, r.sims, r.event_ids, clues_from_verdicts(vd, n),
                        UserFacts(facts).constraints(), r.outings.same_outing_pairs(n) if (r.outings and verdicts) else set(),
                        params=ap, evidence=ev)
    model = inputs.build()
    sol = solve(model)
    place(sol, [p for p in r.trail if p.source != "user_facts"], {})
    locate(model, sol, place_min=place_min)
    return model, sol, ev


def run(label, rolls=ROLLS, quiet=False, **kw):
    T = collections.defaultdict(dict)
    for x in truth():
        T[x["roll"]][x["n"]] = x
    results, detail = [], []
    for key in rolls:
        r = load_run(key)
        model, sol, ev = solve_v2(r, **kw)
        results += [(T[key][f.number], a) for f, a in zip(r.frames, sol.assignments)]
        detail += [(key, f.number, T[key][f.number], a, ev.frames[i]) for i, (f, a) in enumerate(zip(r.frames, sol.assignments))]
    out = score(label, results) if not quiet else None
    return results, detail


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--atlas", action="store_true")
    args = ap.parse_args()
    t = time.time()
    run("v2 free (no Claude), window only", verdicts=False)
    run("v2 + Claude verdicts, window only", verdicts=True)
    if args.atlas:
        run("v2 free (no Claude), with atlas", verdicts=False, use_atlas=True)
        run("v2 + Claude verdicts, with atlas", verdicts=True, use_atlas=True)
    print(f"{time.time()-t:.0f}s")
