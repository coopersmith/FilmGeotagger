"""Ground truth from the rolls the user reviewed and confirmed (September 2026), plus the two
hand-tagged verification rolls. One row per frame, with how strong the truth is:

  photo   the user confirmed a phone photo as the frame's anchor (Claude's pick or their own):
          time and place are that photo's. `by` says who found it: claude | user.
  hand    the user typed a time and/or a place (a fact, a check-in, a pin).
  moment  bound to a neighbour by "moments after/before" — inherits that neighbour's truth.
  weak    an interpolation the user accepted without touching: not evidence, never scored.

Run: uv run --extra embed python scripts/v2/truth.py   -> .filmgeo/eval_v2/truth.json
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

from filmgeo.align import pipeline
from filmgeo.align.overrides import RollOverrides
from filmgeo.config import DATA_DIR
from filmgeo.photos import library
from filmgeo.signals.user_facts import RollFacts

OUT = DATA_DIR / "eval_v2"
ROLLS = ["874466", "874472", "874477", "00000120", "00000121", "00000122", "00000123", "00000124",
         "00000125", "00000126", "00000127"]


def origin_of(key: str) -> str:
    return json.loads((DATA_DIR / "assignments" / f"{key}.json").read_text())["origin"]


def rows_for(key: str, assets) -> list[dict]:
    r = pipeline.run(origin_of(key), assets=assets)
    facts, ov = r.facts, r.overrides or RollOverrides(key)
    by_uuid = {a.uuid: a for a in r.pool}
    out = []
    for f, a in zip(r.frames, r.solution.assignments):
        o = ov.get(f.number)
        ff = facts.frames.get(f.number)
        confirmed = bool(o and o.confirmed)
        v = r.verdicts.get(f.number)
        row = {"roll": key, "n": f.number, "confirmed": confirmed, "tier": "weak", "by": None,
               "t": a.time.isoformat(), "lat": a.lat, "lon": a.lon, "uuid": None,
               "has_time": False, "has_place": False,
               "claude_match": v.match if v else None, "claude_conf": v.confidence if v else None}
        if not confirmed:
            row["tier"] = "unconfirmed"
        elif a.source in ("anchored", "locked") and a.anchor_uuid in by_uuid:
            p = by_uuid[a.anchor_uuid]
            row.update(tier="photo", uuid=p.uuid, t=p.date.isoformat(), has_time=True,
                       by="user" if (o and o.anchor) else "claude")
            if p.lat is not None:
                row.update(lat=p.lat, lon=p.lon, has_place=True)
        elif ff and (ff.when or ff.lat is not None):
            row.update(tier="hand", by="user", has_time=bool(ff.when and len(ff.when) > 10), has_place=ff.lat is not None)
            if ff.same_time_as:
                row["has_time"] = True       # within a minute of a neighbour: as good as typed
        elif ff and ff.same_time_as:
            row.update(tier="moment", by="user", has_time=True, has_place=a.lat is not None)
        out.append(row)
    return out


def main() -> None:
    assets = library.load()
    rows = []
    for key in ROLLS:
        try:
            rr = rows_for(key, assets)
        except Exception as e:                      # a roll whose folder moved: say so, carry on
            print(f"{key}: skipped ({e})", file=sys.stderr)
            continue
        rows += rr
        tiers = {}
        for x in rr:
            tiers[x["tier"] + ("/" + x["by"] if x["by"] else "")] = tiers.get(x["tier"] + ("/" + x["by"] if x["by"] else ""), 0) + 1
        print(key, len(rr), tiers)
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "truth.json").write_text(json.dumps(rows, indent=1))
    print(len(rows), "rows ->", OUT / "truth.json")


if __name__ == "__main__":
    main()
