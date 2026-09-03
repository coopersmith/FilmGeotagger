#!/usr/bin/env python3
"""M1: sweep K, fusion method and the per-event cap over already-cached vectors.

Pure numpy over `.filmgeo/vectors/`, so this re-answers "what would have been retrieved" in
seconds without touching the GPU. Its job is to say *why* recall misses: whether the right
candidate is ranked just outside K (raise K), buried (ranking is wrong), or excluded by the
per-event diversity cap (the cap is miscalibrated).
"""

from __future__ import annotations

import argparse

import numpy as np

from filmgeo import events as ev
from filmgeo import eval_set, retrieve
from filmgeo.config import SAME_MOMENT
from filmgeo.embed.cache import VectorCache
from filmgeo.photos import library

KS = (1, 3, 5, 8, 16, 32)


def rank_fuse(sims: dict[str, np.ndarray], k: float = 60.0) -> np.ndarray:
    """Reciprocal rank fusion.

    z-scoring equalises variance but not tail shape: a model whose top candidate sits 8 sigma out
    dominates one whose top sits at 3 sigma, so the weaker-but-more-peaked model wins the sum.
    RRF discards magnitude entirely and combines positions, which is why it is robust to exactly
    that mismatch.
    """
    total = np.zeros(len(next(iter(sims.values()))))
    for s in sims.values():
        ranks = np.empty(len(s), dtype=np.int64)
        ranks[np.argsort(-s)] = np.arange(len(s))
        total += 1.0 / (k + ranks + 1)
    return total


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--rolls", type=int, default=6)
    ap.add_argument("--pad-days", type=int, default=2)
    args = ap.parse_args()

    assets = library.load()
    rolls = [r.clean() for r in eval_set.rolls(assets)][: args.rolls]
    caches = {v: VectorCache(v) for v in ("siglip", "dinov2")}

    # method -> cap -> K -> [found, total]
    tally: dict = {}
    per_roll: dict = {}

    for roll in rolls:
        pool = library.candidates(assets, roll.start, roll.end, pad_days=args.pad_days)
        ids, _ = ev.segment(pool)
        pool_times = np.array([a.date.timestamp() for a in pool])
        frame_keys = [f.uuid for f in roll.frames]
        pool_keys = [a.uuid for a in pool]

        try:
            fv = {v: c.get(frame_keys) for v, c in caches.items()}
            pv = {v: c.get(pool_keys) for v, c in caches.items()}
        except KeyError:
            print(f"roll {roll.key}: vectors not cached, skipping")
            continue

        evaluable = [
            i for i, f in enumerate(roll.frames)
            if np.min(np.abs(pool_times - f.date.timestamp())) <= SAME_MOMENT
        ]

        for i in evaluable:
            truth = roll.frames[i].date.timestamp()
            correct = np.abs(pool_times - truth) <= SAME_MOMENT
            sims = {v: pv[v] @ fv[v][i] for v in fv}

            scored = {
                "siglip": sims["siglip"],
                "dinov2": sims["dinov2"],
                "z-fused": retrieve.fuse(sims),
                "rrf": rank_fuse(sims),
            }
            for method, score in scored.items():
                for cap in (3, 0):
                    order = np.argsort(-score)
                    if cap:
                        seen, keep = {}, []
                        for j in order:
                            e = ids[j]
                            if seen.get(e, 0) >= cap:
                                continue
                            seen[e] = seen.get(e, 0) + 1
                            keep.append(j)
                            if len(keep) >= max(KS):
                                break
                        order = np.array(keep)
                    else:
                        order = order[: max(KS)]
                    for K in KS:
                        hit = bool(correct[order[:K]].any())
                        t = tally.setdefault(method, {}).setdefault(cap, {}).setdefault(K, [0, 0])
                        t[0] += hit
                        t[1] += 1
                        if K == 8 and cap == 3:
                            pr = per_roll.setdefault(roll.key, {}).setdefault(method, [0, 0])
                            pr[0] += hit
                            pr[1] += 1

    print(f"\n{'method':10} {'cap':>4} " + "".join(f"{'@'+str(K):>8}" for K in KS))
    print("-" * (16 + 8 * len(KS)))
    for method in ("siglip", "dinov2", "z-fused", "rrf"):
        for cap in (3, 0):
            row = tally.get(method, {}).get(cap, {})
            cells = "".join(f"{100*row[K][0]/row[K][1]:7.1f}%" for K in KS if K in row)
            print(f"{method:10} {cap or 'none':>4} {cells}")

    print(f"\nper-roll recall@8 (cap 3)\n{'roll':10} " + "".join(f"{m:>10}" for m in ('siglip','dinov2','z-fused','rrf')))
    for key, methods in per_roll.items():
        cells = "".join(f"{100*methods[m][0]/methods[m][1]:9.1f}%" for m in ('siglip','dinov2','z-fused','rrf'))
        print(f"{key:10} {cells}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
