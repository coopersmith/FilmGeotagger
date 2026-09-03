#!/usr/bin/env python3
"""M1: measure whether embeddings can find a film frame's phone counterpart.

    uv run --extra embed python scripts/eval_m1.py [--rolls N] [--variants siglip,dinov2,fused]

PLAN.md's exit criterion for M1 is that >=80% of frames which *have* a phone counterpart are
found in the top 8. This script measures exactly that, on rolls the user hand-tagged, using the
hand-set date as the answer key.

Metric design follows what the answer key can actually support (see filmgeo/eval_set.py):

  * A frame **has a counterpart** if some non-film photo sits within SAME_MOMENT of its true
    time. Frames with no counterpart are excluded from recall — nothing could have found them —
    but counted and reported, because that fraction is itself a finding.
  * A retrieved candidate is **correct** if it falls within SAME_MOMENT of the frame's true time.
    Not to the second: the ground truth is group-level.
  * Frames flagged by `Roll.outliers()` are dropped, and the count is reported.
"""

from __future__ import annotations

import argparse
import time
from datetime import timedelta

import numpy as np

from filmgeo import events as ev
from filmgeo import eval_set, retrieve
from filmgeo.config import DEFAULT_VARIANTS, SAME_MOMENT, TOP_K
from filmgeo.embed.cache import embed_cached
from filmgeo.photos import library

VARIANTS = {
    "siglip": ("SigLIP", dict(grayscale=False)),
    "siglip_gray": ("SigLIP", dict(grayscale=True)),
    "dinov2": ("DINOv2", dict(grayscale=False)),
    "dinov2_gray": ("DINOv2", dict(grayscale=True)),
}


def build(variant: str):
    from filmgeo.embed import models

    cls, kwargs = VARIANTS[variant]
    return getattr(models, cls)(**kwargs)


def vectors(variant: str, keys: list[str], paths: list[str]) -> np.ndarray:
    embedder = build(variant)
    return embed_cached(embedder, keys, paths, variant)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--rolls", type=int, default=3, help="how many recent hand-tagged rolls")
    ap.add_argument("--pad-days", type=int, default=2, help="window padding around the roll")
    ap.add_argument("--variants", default=",".join(DEFAULT_VARIANTS))
    ap.add_argument("--k", type=int, default=TOP_K)
    args = ap.parse_args()
    variants = args.variants.split(",")

    assets = library.load()
    all_rolls = [r.clean() for r in eval_set.rolls(assets)]
    chosen = all_rolls[: args.rolls]
    print(f"library: {len(assets)} assets | {len(all_rolls)} hand-tagged rolls | evaluating {len(chosen)}\n")

    totals = {v: [0, 0] for v in [*variants, "fused"]}   # [found, evaluable]
    for roll in chosen:
        raw = next(r for r in eval_set.rolls(assets) if r.key == roll.key)
        dropped = len(raw.frames) - len(roll.frames)
        pool = library.candidates(assets, roll.start, roll.end, pad_days=args.pad_days)
        if not pool:
            print(f"roll {roll.key}: no candidates in window, skipping")
            continue
        ids, evs = ev.segment(pool)

        # Which frames could possibly be found?
        pool_times = np.array([a.date.timestamp() for a in pool])
        evaluable = []
        for i, f in enumerate(roll.frames):
            if np.min(np.abs(pool_times - f.date.timestamp())) <= SAME_MOMENT:
                evaluable.append(i)

        print(f"roll {roll.key}  {roll.format:10} {len(roll.frames):3} frames  "
              f"{roll.start:%Y-%m-%d} .. {roll.end:%Y-%m-%d}  span {str(roll.span).split('.')[0]}")
        print(f"  candidate pool {len(pool)} photos in {len(evs)} events"
              f"{f'  ({dropped} outlier frame(s) dropped)' if dropped else ''}")
        print(f"  frames with a phone counterpart within {SAME_MOMENT//60} min: "
              f"{len(evaluable)}/{len(roll.frames)}")
        if not evaluable:
            print()
            continue

        frame_paths = [f.derivative for f in roll.frames]
        if any(p is None for p in frame_paths):
            missing = sum(1 for p in frame_paths if p is None)
            print(f"  WARNING {missing} frames have no local derivative; skipped")
        pool_paths = [a.derivative for a in pool]

        fv, pv = {}, {}
        for v in variants:
            t0 = time.time()
            fv[v] = vectors(v, [f"{f.uuid}" for f in roll.frames], frame_paths)
            pv[v] = vectors(v, [f"{a.uuid}" for a in pool], pool_paths)
            print(f"  {v:12} embedded in {time.time()-t0:5.1f}s")

        for name in [*variants, "fused"]:
            use = variants if name == "fused" else [name]
            found = 0
            for i in evaluable:
                cands = retrieve.top_k(
                    {v: fv[v][i] for v in use}, {v: pv[v] for v in use}, pool, events=ids, k=args.k
                )
                truth = roll.frames[i].date.timestamp()
                if any(abs(c.asset.date.timestamp() - truth) <= SAME_MOMENT for c in cands):
                    found += 1
            totals[name][0] += found
            totals[name][1] += len(evaluable)
            print(f"    recall@{args.k}  {name:12} {found}/{len(evaluable)} = {100*found/len(evaluable):5.1f}%")
        print()

    print("=" * 62)
    print(f"OVERALL recall@{args.k} across {len(chosen)} rolls   (PLAN.md M1 exit: >= 80%)")
    for name, (found, total) in totals.items():
        if total:
            pct = 100 * found / total
            print(f"  {name:14} {found:4}/{total:<4} = {pct:5.1f}%   {'PASS' if pct >= 80 else 'below target'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
