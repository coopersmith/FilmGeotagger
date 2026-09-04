#!/usr/bin/env python3
"""COO-146: retrieval recall on genuine anchors — K, per-event cap, calibration, cost.

    uv run python scripts/sweep_retrieval.py [--rolls 9] [--pad-days 2]

Pure numpy over cached SigLIP vectors; no embedding, no API. Scores only the frames the user
genuinely anchored to a *phone* photo (`Roll.anchored(library.phone_times(assets))`, 35 frames
across the 2026 batch — see the correction at the end of docs/m1-findings.md). Two hit
definitions are reported side by side:

* **exact** — a retrieved candidate is within 2 s of the frame's true time, i.e. the very photo
  the user copied the timestamp from. This is what "anchored frames exact" needs.
* **30 min** — M1's `SAME_MOMENT` definition: any candidate on the same occasion.

Prints: the K x cap grid, per-roll recall at K=6 and K=8, a miss analysis at K=6/cap=3, a
logistic calibration of P(true | similarity), one alternative ranking (events by margin), and
the verification cost per 36-frame roll at each K.
"""

from __future__ import annotations

import argparse
import statistics

import numpy as np

from filmgeo import events as ev
from filmgeo import eval_set
from filmgeo.config import SAME_MOMENT
from filmgeo.embed.cache import VectorCache
from filmgeo.photos import library

KS = (6, 8, 12, 16, 24, 32)
CAPS = (1, 2, 3, 5, None)
EXACT = 2.0                       # seconds: same tolerance as Roll.anchored()
COST_PER_FRAME_AT_K6 = 0.035      # $ on claude-opus-5, measured in M1 (docs/m1-findings.md)
FRAMES_PER_ROLL = 36
CI_NOTE = "n = 35 anchored frames; the 95% confidence interval is about +/-16 points"


def capped_order(order: np.ndarray, ids: list[int], cap: int | None, limit: int) -> np.ndarray:
    """First `limit` indices of `order` keeping at most `cap` per event (None = no cap)."""
    if not cap:
        return order[:limit]
    seen: dict[int, int] = {}
    keep: list[int] = []
    for j in order:
        e = ids[j]
        if seen.get(e, 0) >= cap:
            continue
        seen[e] = seen.get(e, 0) + 1
        keep.append(j)
        if len(keep) >= limit:
            break
    return np.array(keep, dtype=np.int64)


def margin_order(sims: np.ndarray, ids: np.ndarray) -> np.ndarray:
    """Alternative ranking: top-1 per event, events ordered by (best - runner-up) similarity.

    The idea: an exact counterpart should stand clear of its own event's other photos, whereas
    a merely same-scene match has many near-equals. A single-photo event has no runner-up, so
    its runner-up is taken as the frame's pool-wide median similarity.
    """
    pool_median = float(np.median(sims))
    rows = []
    for e in np.unique(ids):
        cols = np.where(ids == e)[0]
        s = np.sort(sims[cols])[::-1]
        runner = s[1] if len(s) > 1 else pool_median
        rows.append((s[0] - runner, cols[np.argmax(sims[cols])]))
    rows.sort(key=lambda t: -t[0])
    return np.array([j for _, j in rows], dtype=np.int64)


def fit_logistic(pos: np.ndarray, neg: np.ndarray) -> tuple[float, float, float]:
    """Grid-search a logistic P(true | sim) minimising mean cross-entropy."""
    x = np.concatenate([pos, neg])
    y = np.concatenate([np.ones(len(pos)), np.zeros(len(neg))])
    best = (None, None, np.inf)
    for centre in np.arange(0.50, 0.995, 0.01):
        for slope in (5, 10, 15, 20, 30, 40, 60):
            p = 1.0 / (1.0 + np.exp(-slope * (x - centre)))
            p = np.clip(p, 1e-9, 1 - 1e-9)
            loss = -np.mean(y * np.log(p) + (1 - y) * np.log(1 - p))
            if loss < best[2]:
                best = (round(float(centre), 2), float(slope), float(loss))
    return best


def pct(hit: int, n: int) -> str:
    return f"{100 * hit / n:5.1f}%" if n else "   n/a"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--rolls", type=int, default=9)
    ap.add_argument("--pad-days", type=int, default=2)
    ap.add_argument("--miss-k", type=int, default=6)
    ap.add_argument("--miss-cap", type=int, default=3)
    ap.add_argument("--variant", default="siglip",
                    help="vector cache to score (siglip, or siglip_gray once scripts/eval_m1.py has built it)")
    args = ap.parse_args()

    assets = library.load()
    rolls = [r.clean() for r in eval_set.rolls(assets)][: args.rolls]
    phone_times = library.phone_times(assets)
    cache = VectorCache(args.variant)

    # cap -> K -> definition -> [hit, n]
    grid: dict = {cap: {K: {"exact": [0, 0], "30min": [0, 0]} for K in KS} for cap in CAPS}
    per_roll: dict = {}          # roll -> (K, cap) -> [hit_exact, n]
    margin_tally = {K: {"exact": [0, 0], "30min": [0, 0]} for K in (6, 8)}
    misses: list[dict] = []
    pos_sims: list[float] = []
    neg_sims: list[float] = []
    pool_medians: list[float] = []
    ev_pos: list[float] = []          # best similarity inside the true event
    ev_neg_best: list[float] = []     # best similarity of the best *other* event
    ev_neg_all: list[float] = []      # best similarity of every other event
    n_anchored = n_scored = n_absent = 0
    true_event_sizes: list[int] = []
    true_ranks: list[int] = []
    within_event_ranks: list[int] = []
    # Lightroom spaces a tagged group one second apart, so consecutive anchored frames share
    # one phone photo and at most one of them depicts it. Group-level exact recall: did *any*
    # frame of the group retrieve the shared photo? group key -> cap -> K -> hit
    groups: dict[tuple[str, int], dict] = {}
    # Two-stage yield: event found at K (cap 1) and the exact photo within the event's top-N
    # by similarity. N -> K -> hits
    two_stage = {N: {K: 0 for K in KS} for N in (3, 6, 10)}

    for roll in rolls:
        pool = library.candidates(assets, roll.start, roll.end, pad_days=args.pad_days)
        ids, evs = ev.segment(pool)
        ids_arr = np.asarray(ids)
        pool_times = np.array([a.date.timestamp() for a in pool])
        anchored = roll.anchored(phone_times)
        n_anchored += len(anchored)
        if not anchored:
            continue
        try:
            fv = cache.get([roll.frames[i].uuid for i in anchored])
            pv = cache.get([a.uuid for a in pool])
        except KeyError:
            print(f"roll {roll.key}: {args.variant} vectors not cached for this window, skipping "
                  f"({len(anchored)} anchored frames unscored)")
            continue

        prev_truth = None
        for row, i in enumerate(anchored):
            truth = roll.frames[i].date.timestamp()
            exact = np.abs(pool_times - truth) <= EXACT
            near = np.abs(pool_times - truth) <= SAME_MOMENT
            if not exact.any():
                n_absent += 1
                continue                      # anchored to a photo with no derivative: unfindable
            n_scored += 1
            sims = pv @ fv[row]
            order = np.argsort(-sims)
            rank = np.empty(len(sims), dtype=np.int64)
            rank[order] = np.arange(1, len(sims) + 1)
            true_cols = np.where(exact)[0]
            true_rank = int(rank[true_cols].min())
            true_ranks.append(true_rank)
            true_event = int(ids_arr[true_cols[np.argmin(rank[true_cols])]])
            event_size = int((ids_arr == true_event).sum())
            true_event_sizes.append(event_size)

            # Calibration data: the true photo vs the best photo of any other event.
            pos_sims.append(float(sims[true_cols].max()))
            other = ids_arr != true_event
            if other.any():
                neg_sims.append(float(sims[other].max()))
            pool_medians.append(float(np.median(sims)))
            # Event-level, which is what the HMM emission consumes (best similarity per event).
            best_by_event = {int(e): float(sims[ids_arr == e].max()) for e in np.unique(ids_arr)}
            ev_pos.append(best_by_event[true_event])
            others = [v for e, v in best_by_event.items() if e != true_event]
            ev_neg_best.append(max(others))
            ev_neg_all.extend(others)
            # Rank of the true photo among its own event's photos.
            in_event = np.where(ids_arr == true_event)[0]
            by_sim = in_event[np.argsort(-sims[in_event])]
            wrank = int(np.where(np.isin(by_sim, true_cols))[0].min()) + 1
            within_event_ranks.append(wrank)

            if prev_truth is None or abs(truth - prev_truth) > 10:
                gkey = (roll.key, truth)                    # a new anchor photo
            prev_truth = truth
            grp = groups.setdefault(gkey, {cap: {K: False for K in KS} for cap in CAPS})

            for cap in CAPS:
                top = capped_order(order, ids, cap, max(KS))
                for K in KS:
                    shown = top[:K]
                    hit_e = bool(exact[shown].any())
                    hit_n = bool(near[shown].any())
                    grid[cap][K]["exact"][0] += hit_e
                    grid[cap][K]["exact"][1] += 1
                    grid[cap][K]["30min"][0] += hit_n
                    grid[cap][K]["30min"][1] += 1
                    grp[cap][K] = grp[cap][K] or hit_e
                    if cap == 1 and (ids_arr[shown] == true_event).any():
                        for N in two_stage:
                            two_stage[N][K] += wrank <= N
                    if K in (6, 8):
                        pr = per_roll.setdefault(roll.key, {}).setdefault((K, cap), [0, 0])
                        pr[0] += hit_e
                        pr[1] += 1
                    if K == args.miss_k and cap == args.miss_cap and not hit_e:
                        misses.append({
                            "roll": roll.key,
                            "frame": roll.numbers[i],
                            "true_rank": true_rank,
                            "event_in_topk": bool((ids_arr[shown] == true_event).any()),
                            "event_size": event_size,
                            "n_events_in_topk": int(len(np.unique(ids_arr[shown]))),
                            "same_occasion_hit": hit_n,
                            "true_sim": float(sims[true_cols].max()),
                            "top_sim": float(sims[shown[0]]),
                        })

            morder = margin_order(sims, ids_arr)
            for K in (6, 8):
                shown = morder[:K]
                margin_tally[K]["exact"][0] += bool(exact[shown].any())
                margin_tally[K]["exact"][1] += 1
                margin_tally[K]["30min"][0] += bool(near[shown].any())
                margin_tally[K]["30min"][1] += 1

    # ------------------------------------------------------------------ 1. the grid
    print(f"\nanchored frames: {n_anchored}; scored: {n_scored}; anchored to a photo absent "
          f"from the pool (no derivative): {n_absent}")
    print(f"variant {args.variant}, window = roll true range +/-{args.pad_days} d. {CI_NOTE}.")
    for defn, label in (("exact", "hit = exact photo (within 2 s)"), ("30min", "hit = same occasion (within 30 min)")):
        print(f"\nrecall@K, {label}")
        print(f"{'cap':>5} " + "".join(f"{'@' + str(K):>8}" for K in KS))
        print("-" * (6 + 8 * len(KS)))
        for cap in CAPS:
            cells = "".join(f"{pct(*grid[cap][K][defn]):>8}" for K in KS)
            print(f"{str(cap or 'none'):>5} {cells}")

    print(f"\nrecall@K by distinct anchor photo (n = {len(groups)}): hit = any frame of the group "
          f"retrieves the shared photo")
    print(f"{'cap':>5} " + "".join(f"{'@' + str(K):>8}" for K in KS))
    print("-" * (6 + 8 * len(KS)))
    for cap in CAPS:
        cells = "".join(f"{pct(sum(g[cap][K] for g in groups.values()), len(groups)):>8}" for K in KS)
        print(f"{str(cap or 'none'):>5} {cells}")

    print("\nper-roll recall (exact), hits/anchored")
    head = "".join(f"{'@' + str(K) + '/' + str(cap or 'none'):>10}" for K in (6, 8) for cap in CAPS)
    print(f"{'roll':10}{head}")
    for key, cells in per_roll.items():
        row = "".join(f"{cells[(K, cap)][0]:>7}/{cells[(K, cap)][1]:<2}" for K in (6, 8) for cap in CAPS)
        print(f"{key:10}{row}")

    # ------------------------------------------------------------------ 2. misses
    print(f"\nmiss analysis at K={args.miss_k}, cap={args.miss_cap} (exact definition): "
          f"{len(misses)} of {n_scored} missed")
    print(f"{'roll':10}{'frame':>6}{'true rank':>10}{'event size':>11}{'event in top-K':>16}"
          f"{'events in top-K':>16}{'30-min hit':>11}{'true sim':>9}{'top sim':>8}")
    for m in misses:
        print(f"{m['roll']:10}{m['frame']:>6}{m['true_rank']:>10}{m['event_size']:>11}"
              f"{'yes' if m['event_in_topk'] else 'no':>16}{m['n_events_in_topk']:>16}"
              f"{'yes' if m['same_occasion_hit'] else 'no':>11}{m['true_sim']:>9.3f}{m['top_sim']:>8.3f}")
    right_event = [m for m in misses if m["event_in_topk"]]
    wrong_event = [m for m in misses if not m["event_in_topk"]]
    print(f"\n  wrong photo within the right event: {len(right_event)}"
          + (f" (median event size {statistics.median(m['event_size'] for m in right_event):.0f}, "
               f"median true rank {statistics.median(m['true_rank'] for m in right_event):.0f})"
               if right_event else ""))
    print(f"  event missing entirely:             {len(wrong_event)}"
          + (f" (median true rank {statistics.median(m['true_rank'] for m in wrong_event):.0f}, "
               f"ranks {sorted(m['true_rank'] for m in wrong_event)})"
               if wrong_event else ""))
    print(f"  same-occasion (30 min) hit anyway:  {sum(m['same_occasion_hit'] for m in misses)}")
    if true_ranks:
        print(f"\n  plain-similarity rank of the true photo over all {n_scored} scored frames: "
              f"median {statistics.median(true_ranks):.0f}, "
              f"rank 1 on {sum(r == 1 for r in true_ranks)}, <=6 on {sum(r <= 6 for r in true_ranks)}, "
              f"<=32 on {sum(r <= 32 for r in true_ranks)}, worst {max(true_ranks)}")
        print(f"  true event size: median {statistics.median(true_event_sizes):.0f}, "
              f"max {max(true_event_sizes)}, singletons {sum(s == 1 for s in true_event_sizes)}")
        w = within_event_ranks
        print(f"  rank of the true photo *within its own event* by similarity: median "
              f"{statistics.median(w):.0f}; rank 1 on {sum(r == 1 for r in w)}, <=3 on {sum(r <= 3 for r in w)}, "
              f"<=6 on {sum(r <= 6 for r in w)}, <=10 on {sum(r <= 10 for r in w)}, worst {max(w)}")
        print("\n  two-stage yield: event found at K (cap 1), then exact photo within the event's top-N by similarity")
        print(f"{'N':>5} " + "".join(f"{'@' + str(K):>8}" for K in KS))
        for N, row in two_stage.items():
            print(f"{N:>5} " + "".join(f"{pct(row[K], n_scored):>8}" for K in KS))

    # ------------------------------------------------------------------ 3. calibration
    pos, neg = np.array(pos_sims), np.array(neg_sims)
    print("\ncalibration: true photo vs best photo of any other event (SigLIP cosine)")
    print(f"  true:        median {np.median(pos):.3f}, range {pos.min():.3f}-{pos.max():.3f}")
    print(f"  best other:  median {np.median(neg):.3f}, range {neg.min():.3f}-{neg.max():.3f}")
    print(f"  pool median: median {np.median(pool_medians):.3f}")
    print(f"  true > best other on {int((pos > neg).sum())} / {len(pos)} frames")
    centre, slope, loss = fit_logistic(pos, neg)
    print(f"  grid-fit logistic: centre {centre:.2f}, slope {slope:.0f} (mean cross-entropy {loss:.3f})")
    # Neighbours of the optimum, so the reader can see how flat the surface is.
    for c, s in ((centre - 0.02, slope), (centre + 0.02, slope), (0.88, 10.0)):
        p = np.clip(1 / (1 + np.exp(-s * (np.concatenate([pos, neg]) - c))), 1e-9, 1 - 1e-9)
        y = np.concatenate([np.ones(len(pos)), np.zeros(len(neg))])
        ce = -np.mean(y * np.log(p) + (1 - y) * np.log(1 - p))
        print(f"    centre {c:.2f}, slope {s:.0f}: cross-entropy {ce:.3f}"
              + ("  <- current AlignParams" if (c, s) == (0.88, 10.0) else ""))
    q = len(pos) / (len(pos) + len(neg))
    print(f"    constant predictor: cross-entropy {-(q * np.log(q) + (1 - q) * np.log(1 - q)):.3f}")

    epos, eneg_best, eneg_all = np.array(ev_pos), np.array(ev_neg_best), np.array(ev_neg_all)
    print("\ncalibration at the event level (what AlignParams.calibrate is applied to: best similarity per event)")
    print(f"  true event's best:       median {np.median(epos):.3f}, range {epos.min():.3f}-{epos.max():.3f}")
    print(f"  best other event's best: median {np.median(eneg_best):.3f}; true beats it on "
          f"{int((epos > eneg_best).sum())} / {len(epos)}")
    print(f"  every other event's best: median {np.median(eneg_all):.3f} (n = {len(eneg_all)})")
    for label, neg_ in (("vs best other event", eneg_best), ("vs every other event", eneg_all)):
        c, s, l = fit_logistic(epos, neg_)
        q = len(epos) / (len(epos) + len(neg_))
        const = -(q * np.log(q) + (1 - q) * np.log(1 - q))
        print(f"  grid-fit {label}: centre {c:.2f}, slope {s:.0f} (cross-entropy {l:.3f}; constant predictor {const:.3f})")

    print("\nalternative ranking: top-1 per event, events by margin (best - runner-up)")
    for K in (6, 8):
        print(f"  @{K}: exact {pct(*margin_tally[K]['exact'])}  30-min {pct(*margin_tally[K]['30min'])}"
              f"   (similarity, cap 1: exact {pct(*grid[1][K]['exact'])}; cap 3: exact {pct(*grid[3][K]['exact'])})")

    # ------------------------------------------------------------------ 4. cost
    print(f"\nverification cost, ${COST_PER_FRAME_AT_K6:.3f}/frame at K=6 on claude-opus-5, "
          f"linear in images shown, {FRAMES_PER_ROLL}-frame roll")
    print(f"{'K':>4}{'$/frame':>10}{'$/roll':>9}{'exact@K cap3':>14}{'exact@K none':>14}")
    for K in KS:
        per_frame = COST_PER_FRAME_AT_K6 * K / 6
        print(f"{K:>4}{per_frame:>10.3f}{per_frame * FRAMES_PER_ROLL:>9.2f}"
              f"{pct(*grid[3][K]['exact']):>14}{pct(*grid[None][K]['exact']):>14}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
