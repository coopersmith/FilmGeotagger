"""`<roll>/filmgeo.json`: what was written, why, and the decisions behind it — beside the scans.

The `.filmgeo/` caches are derived state on one machine; the sidecar travels with the roll.
It holds, per frame, the written time and offset, GPS, source, confidence, interval, anchor
photo, Claude's reasoning and when it was written, plus the roll facts and the user's
overrides at that moment. Two things use it:

* **Re-writing only what changed.** `plan()` compares each confirmed frame's values against
  the sidecar's and leaves an unchanged frame alone (`--force` writes anyway).
* **Reopening a roll** whose `.filmgeo/` files are gone: `adopt()` seeds the facts and
  overrides from the sidecar so the review UI shows the prior decisions and can change them.
"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

from filmgeo.align.overrides import RollOverrides
from filmgeo.signals.user_facts import RollFacts

NAME = "filmgeo.json"


def path_for(folder: Path) -> Path:
    return folder / NAME


def load(folder: Path) -> dict | None:
    p = path_for(folder)
    if not p.exists():
        return None
    try:
        return json.loads(p.read_text())
    except json.JSONDecodeError:
        return None


def frame_record(fw, assignment: dict, verdict: dict | None, verified: bool | None, at: str) -> dict:
    """One frame's entry: the written values and the reasoning that produced them."""
    return {
        "number": fw.number,
        "file": fw.path.name,
        "written_at": at,
        "verified": verified,
        "time": assignment.get("time"),
        "local": fw.local,
        "offset": fw.offset,
        "lat": fw.lat,
        "lon": fw.lon,
        "source": fw.source,
        "confidence": fw.confidence,
        "interval": list(fw.interval),
        "anchor_uuid": fw.anchor_uuid,
        "location": assignment.get("location"),
        "evidence": (verdict or {}).get("evidence"),
        "claude_confidence": (verdict or {}).get("confidence"),
        "keywords": fw.keywords,
    }


def write(folder: Path, key: str, plan, assignments: dict, verdicts: dict[int, dict], facts: RollFacts,
          overrides: RollOverrides | None, checks: dict[int, bool | None], at: str | None = None) -> Path:
    """Merge this write into the sidecar: frames written now are replaced, others kept."""
    at = at or datetime.now().astimezone().isoformat(timespec="seconds")
    existing = load(folder) or {}
    frames = {int(f["number"]): f for f in existing.get("frames", [])}
    by_n = {f["number"]: f for f in assignments.get("frames", [])}
    for fw in plan.frames:
        frames[fw.number] = frame_record(fw, by_n.get(fw.number, {}), verdicts.get(fw.number), checks.get(fw.number), at)
    data = {
        "filmgeo": 1,
        "roll": key,
        "folder": str(folder),
        "written_at": at,
        "window": assignments.get("window"),
        "facts": {k: v for k, v in facts.__dict__.items() if k != "frames"} | {"frames": {str(n): f.__dict__ for n, f in facts.frames.items() if not f.is_empty}},
        "overrides": {str(n): o.__dict__ for n, o in (overrides.frames.items() if overrides else ()) if not o.is_empty},
        "frames": [frames[n] for n in sorted(frames)],
    }
    p = path_for(folder)
    p.write_text(json.dumps(data, indent=1) + "\n")
    return p


def written_frames(folder: Path) -> dict[int, dict]:
    s = load(folder)
    return {int(f["number"]): f for f in (s or {}).get("frames", [])}


def unchanged(fw, written: dict | None) -> bool:
    """Would writing this frame change anything the sidecar says is already in the file?"""
    if not written or written.get("verified") is False:
        return False
    same_loc = (written.get("lat") is None and fw.lat is None) or (
        written.get("lat") is not None and fw.lat is not None
        and abs(written["lat"] - fw.lat) < 1e-6 and abs(written["lon"] - fw.lon) < 1e-6)
    return written.get("local") == fw.local and written.get("offset") == fw.offset and same_loc \
        and list(written.get("keywords") or []) == list(fw.keywords)


def adopt(key: str, folder: Path, facts_dir: Path, overrides_dir: Path) -> list[str]:
    """Seed missing facts/overrides files from the sidecar. Returns what was created."""
    s = load(folder)
    if not s:
        return []
    made = []
    if not RollFacts.path_for(key, facts_dir).exists() and s.get("facts"):
        raw = dict(s["facts"])
        raw["roll"] = key
        frames = raw.pop("frames", {}) or {}
        from filmgeo.signals.user_facts import FrameFact

        rf = RollFacts(**raw, frames={int(n): FrameFact(**v) for n, v in frames.items()})
        rf.save(facts_dir)
        made.append("facts")
    if not RollOverrides.path_for(key, overrides_dir).exists() and s.get("overrides"):
        from filmgeo.align.overrides import FrameOverride

        ro = RollOverrides(key, {int(n): FrameOverride(**v) for n, v in s["overrides"].items()})
        ro.save(overrides_dir)
        made.append("overrides")
    return made
