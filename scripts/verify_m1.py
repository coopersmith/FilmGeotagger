#!/usr/bin/env python3
"""M1: measure Claude verification precision on a hand-tagged roll.

    uv run --extra embed --extra verify python scripts/verify_m1.py ROLL [--model ...]

PLAN.md's second M1 exit criterion is that >=95% of *accepted* matches are correct. Retrieval
supplies the candidates; this measures whether Claude's yes/no on them can be trusted enough to
turn a similarity into a timestamp.

Reported per roll:
  precision  — of frames Claude accepted, the share whose chosen candidate really is within
               SAME_MOMENT of the frame's true time. This is the number that must clear 95%:
               a wrong accept places the frame on the wrong day and drags its neighbours.
  recall     — of frames that had a correct candidate in the shortlist, the share Claude found.
  abstain    — frames where Claude said none. Not an error; most frames have no counterpart.
  cost       — tokens and dollars, so the model-tier decision is made on evidence.
"""

from __future__ import annotations

import argparse
import time

import numpy as np

from filmgeo import events as ev
from filmgeo import eval_set, retrieve
from filmgeo.config import SAME_MOMENT
from filmgeo.embed.cache import VectorCache
from filmgeo.photos import library
from filmgeo.verify import claude

# USD per million tokens, claude-opus-5.
PRICE_IN, PRICE_OUT = 5.0, 25.0

# A single pass/fail at 30 minutes measures the threshold as much as the model. The hand-tagged
# ground truth is itself only outing-accurate, and Claude's near-misses are same-session photos
# minutes apart — a very different failure from landing on the wrong day. So precision is
# reported as a curve, and "exact" (Claude picked the same photo the user anchored to by hand)
# is tracked separately as the strongest signal available.
TOLERANCES = [("exact", 0), ("<=5min", 300), ("<=30min", 1800), ("<=2h", 7200), ("same day", 86400)]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("roll")
    ap.add_argument("--model", default=claude.DEFAULT_MODEL)
    ap.add_argument("--pad-days", type=int, default=2)
    ap.add_argument("--k", type=int, default=claude.MAX_CANDIDATES)
    ap.add_argument("--limit", type=int, default=0, help="only the first N frames")
    args = ap.parse_args()

    assets = library.load()
    roll = next((r.clean() for r in eval_set.rolls(assets) if r.key == args.roll), None)
    if roll is None:
        print(f"no hand-tagged roll {args.roll}")
        return 2

    pool = library.candidates(assets, roll.start, roll.end, pad_days=args.pad_days)
    ids, _ = ev.segment(pool)
    caches = {v: VectorCache(v) for v in ("siglip", "dinov2")}
    fv = {v: c.get([f.uuid for f in roll.frames]) for v, c in caches.items()}
    pv = {v: c.get([a.uuid for a in pool]) for v, c in caches.items()}

    client = claude.make_client()
    frames = list(enumerate(roll.frames))[: args.limit or None]
    print(f"roll {roll.key}  {len(frames)} frames  pool {len(pool)}  model {args.model}\n")

    accepted = shortlisted = found = abstained = failed = 0
    within = {label: 0 for label, _ in TOLERANCES}
    tin = tout = 0
    t0 = time.time()

    for i, frame in frames:
        cands = retrieve.top_k({v: fv[v][i] for v in fv}, pv, pool, events=ids, k=args.k)
        refs = [
            claude.CandidateRef(uuid=c.asset.uuid, path=c.asset.derivative, local_time=c.asset.date)
            for c in cands
        ]
        truth = frame.date.timestamp()
        gold = [abs(c.asset.date.timestamp() - truth) <= SAME_MOMENT for c in cands]
        if any(gold):
            shortlisted += 1

        params = claude.request_params(frame.derivative, refs, args.model)
        if params is None:
            failed += 1
            continue
        try:
            resp = client.messages.parse(output_format=claude.Verdict, **params)
        except Exception as e:  # noqa: BLE001 - one bad frame must not end the run
            print(f"  frame {roll.numbers[i]:>3}  ERROR {type(e).__name__}: {str(e)[:90]}")
            failed += 1
            continue
        tin += resp.usage.input_tokens
        tout += resp.usage.output_tokens
        if resp.stop_reason == "refusal":
            # A refusal is "no verdict", never "no match" (PLAN risk 8).
            failed += 1
            print(f"  frame {roll.numbers[i]:>3}  REFUSAL — counted as no verdict")
            continue

        v = resp.parsed_output
        if v.match is None:
            abstained += 1
            mark = "none " + ("(MISS: a correct candidate was present)" if any(gold) else "(ok)")
        else:
            accepted += 1
            valid = 1 <= v.match <= len(cands)
            err = abs((cands[v.match - 1].asset.date - frame.date).total_seconds()) if valid else 1e12
            for label, tol in TOLERANCES:
                within[label] += err <= tol
            hit = valid and gold[v.match - 1]
            found += hit
            delta = cands[v.match - 1].asset.date - frame.date if valid else "invalid index"
            mark = f"#{v.match} conf {v.confidence:.2f} {'HIT ' if hit else 'off  '} ({delta})"
        print(f"  frame {roll.numbers[i]:>3}  {mark}")
        if v.match is not None and not hit:
            print(f"        evidence: {v.evidence[:150]}")

    cost = tin / 1e6 * PRICE_IN + tout / 1e6 * PRICE_OUT
    n = len(frames)
    print(f"\n{'='*60}\nroll {roll.key}  {n} frames in {time.time()-t0:.0f}s")
    print(f"  accepted      {accepted}")
    print(f"  abstained     {abstained}")
    print(f"  no verdict    {failed}  (errors + refusals; never counted as 'no match')")
    if accepted:
        print("  precision of accepted matches, by how far off the chosen photo is:")
        for label, _ in TOLERANCES:
            pct = 100 * within[label] / accepted
            note = "   <- PLAN M1 exit: >= 95%" if label == "<=30min" else ""
            print(f"    {label:10} {within[label]:3}/{accepted:<3} = {pct:5.1f}%{note}")
    if shortlisted:
        print(f"  recall        {found}/{shortlisted} = {100*found/shortlisted:.1f}% "
              f"of frames whose shortlist contained a correct candidate")
    print(f"  tokens        {tin} in / {tout} out")
    print(f"  cost          ${cost:.3f}  (${cost/max(n,1):.4f}/frame, "
          f"~${cost/max(n,1)*36:.2f} per 36-exposure roll)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
