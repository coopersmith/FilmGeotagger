"""Assemble one roll end to end: frames, window, pool, retrieval, verdicts, evidence, solve.

This is what `filmgeo align` runs and what the review UI (M3) will call to re-solve. Every
input is resolved from the caches and the facts file; the only step that talks to the network
is verification, which lives behind `filmgeo verify` and writes its verdicts to
`.filmgeo/verdicts/<roll>.json` for this module to read.

A roll is either a scan folder (the real case) or a hand-tagged eval key (the measurement
case). The eval case also carries the ground truth so the report can show it.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path

import numpy as np

from filmgeo import eval_set, events as ev, retrieve
from filmgeo.align.checks import ReverseTest, RollInputs, WindowCheck, reverse_test, widen as widen_window, window_check
from filmgeo.align.model import Anchor, FrameClues
from filmgeo.align.solve import Solution, solve
from filmgeo.config import DATA_DIR, TOP_K
from filmgeo.embed.cache import VectorCache
from filmgeo.geo import place
from filmgeo.photos import library
from filmgeo.photos.library import Asset
from filmgeo.signals.base import Window, collect, effective_window
from filmgeo.signals.nfc_log import CACHE as NFC_CACHE, NfcLog
from filmgeo.signals.photos_trail import PhotosTrail
from filmgeo.signals.user_facts import RollFacts, UserFacts

VERDICTS_DIR = DATA_DIR / "verdicts"
ASSIGNMENTS_DIR = DATA_DIR / "assignments"


@dataclass
class FrameRef:
    number: int
    key: str                       # cache key: asset uuid (eval) or content hash (scan)
    path: str | None               # image to show / embed
    truth: datetime | None = None  # hand-tagged date, eval rolls only
    truth_lat: float | None = None
    truth_lon: float | None = None


@dataclass
class Verdict:
    candidates: list[str]          # uuids shown, in order
    match: str | None
    confidence: float
    evidence: str = ""
    clues: dict = field(default_factory=dict)


def load_verdicts(key: str, directory: Path = VERDICTS_DIR) -> dict[int, Verdict]:
    p = directory / f"{key}.json"
    if not p.exists():
        return {}
    raw = json.loads(p.read_text())
    return {int(n): Verdict(**v) for n, v in raw.get("frames", {}).items()}


def save_verdicts(key: str, verdicts: dict[int, Verdict], meta: dict, directory: Path = VERDICTS_DIR) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    p = directory / f"{key}.json"
    existing = json.loads(p.read_text()) if p.exists() else {"frames": {}}
    existing.update(meta)
    existing["frames"].update({str(n): asdict(v) for n, v in verdicts.items()})
    p.write_text(json.dumps(existing, indent=1, default=str) + "\n")
    return p


def anchors_from_verdicts(verdicts: dict[int, Verdict], pool: list[Asset], event_ids: list[int],
                          sims: np.ndarray | None) -> list[Anchor]:
    """A verdict naming a candidate becomes an anchor at that photo's instant."""
    index = {a.uuid: j for j, a in enumerate(pool)}
    out = []
    for n, v in sorted(verdicts.items()):
        if v.match is None or v.match not in index:
            continue
        j = index[v.match]
        a = pool[j]
        i = n - 1
        out.append(Anchor(i, a.uuid, a.date, event_ids[j], v.confidence,
                          similarity=float(sims[i, j]) if sims is not None else 0.0,
                          lat=a.lat, lon=a.lon, tzoffset=a.tzoffset))
    return out


def clues_from_verdicts(verdicts: dict[int, Verdict], n_frames: int) -> list[FrameClues | None]:
    out: list[FrameClues | None] = [None] * n_frames
    for n, v in verdicts.items():
        if 1 <= n <= n_frames and v.clues:
            out[n - 1] = FrameClues(time_of_day=v.clues.get("time_of_day"), indoor=v.clues.get("indoor"))
    return out


@dataclass
class RollRun:
    key: str
    frames: list[FrameRef]
    facts: RollFacts
    window: Window
    window_source: str
    pool: list[Asset]
    events: list
    event_ids: list[int]
    sims: np.ndarray
    candidates: dict[int, list[retrieve.Candidate]]     # by frame number
    verdicts: dict[int, Verdict]
    inputs: RollInputs
    solution: Solution
    reverse: ReverseTest
    check: WindowCheck
    trail_counts: dict[str, int]

    @property
    def n_frames(self) -> int:
        return len(self.frames)


def resolve_frames(roll: str, assets: list[Asset]) -> tuple[str, list[FrameRef], eval_set.Roll | None]:
    """A scan folder, or a hand-tagged roll key."""
    p = Path(roll).expanduser()
    if p.is_dir():
        from filmgeo.scans.ingest import ingest

        scan = ingest(p)
        return p.name, [FrameRef(f.number, f.sha, str(f.path)) for f in scan.frames], None
    truth = next((r.clean() for r in eval_set.rolls(assets) if r.key == roll), None)
    if truth is None:
        raise FileNotFoundError(f"{roll} is neither a folder nor a hand-tagged roll key")
    frames = [FrameRef(n, a.uuid, a.derivative, a.date, a.lat, a.lon) for n, a in zip(truth.numbers, truth.frames)]
    return truth.key, frames, truth


def frame_vectors(frames: list[FrameRef], variant: str = "siglip") -> np.ndarray:
    cache = VectorCache(variant)
    missing = cache.missing([f.key for f in frames])
    if missing:
        from filmgeo.embed import models

        embedder = getattr(models, {"siglip": "SigLIP", "dinov2": "DINOv2"}[variant])()
        paths = {f.key: f.path for f in frames}
        from filmgeo.embed.cache import embed_cached

        embed_cached(embedder, missing, [paths[k] for k in missing], variant)
        cache = VectorCache(variant)
    return cache.get([f.key for f in frames])


def pool_vectors(pool: list[Asset], variant: str = "siglip") -> np.ndarray:
    cache = VectorCache(variant)
    missing = cache.missing([a.uuid for a in pool])
    if missing:
        raise RuntimeError(
            f"{len(missing)} of {len(pool)} pool photos have no cached embedding. Run, in Terminal.app "
            f"(Photos derivatives are unreadable from here):\n  uv run --extra embed python scripts/embed_window.py "
            f"{min(a.date for a in pool):%Y-%m-%d} {max(a.date for a in pool):%Y-%m-%d}"
        )
    return cache.get([a.uuid for a in pool])


def trail_for(assets: list[Asset], window: Window, facts: RollFacts) -> tuple[list, dict[str, int]]:
    photos = PhotosTrail(assets)
    signals = [UserFacts(facts), photos]
    if NFC_CACHE.exists():
        signals.append(NfcLog.from_notes(offset_for=photos.offset_for))
    evidence = collect(signals, window)
    counts: dict[str, int] = {}
    for p in evidence.trail:
        counts[p.source] = counts.get(p.source, 0) + 1
    return evidence.trail, counts


def run(roll: str, pad_days: int = 2, k: int = TOP_K, widen: bool = False, assets: list[Asset] | None = None,
        alias: str | None = None) -> RollRun:
    """`alias` names the facts, verdicts and assignments files instead of the roll key — for
    running one roll under a second window (the wrong-month validation) without clobbering."""
    assets = assets or library.load()
    key, frames, truth = resolve_frames(roll, assets)
    if alias:
        key = alias
    n = len(frames)
    facts = RollFacts.load(key)
    constraints = UserFacts(facts).constraints()
    f_lo, f_hi = facts.window()
    if f_lo and f_hi:
        window, source = effective_window(constraints, Window(f_lo, f_hi)), "facts"
    elif truth is not None:
        window, source = Window.around(truth.start, truth.end, pad_days), f"hand-tagged range +/-{pad_days}d (no facts)"
    else:
        raise ValueError(f"no window for {key}: set one with `filmgeo facts {roll} --from ... --to ...`")
    if widen:
        window, source = widen_window(window), source + ", widened +/-1 month"

    pool = library.candidates(assets, window.start, window.end)
    if not pool:
        raise ValueError(f"no phone photos in the window {window.start:%Y-%m-%d} .. {window.end:%Y-%m-%d}")
    event_ids, events = ev.segment(pool)
    fv = frame_vectors(frames)
    pv = pool_vectors(pool)
    sims = fv @ pv.T
    candidates = {
        f.number: retrieve.top_k({"siglip": fv[i]}, {"siglip": pv}, pool, events=event_ids, k=k)
        for i, f in enumerate(frames)
    }
    verdicts = load_verdicts(key)
    anchors = anchors_from_verdicts(verdicts, pool, event_ids, sims)
    clues = clues_from_verdicts(verdicts, n)
    inputs = RollInputs(window, events, n, anchors, sims, event_ids, clues, constraints)
    model = inputs.build()
    solution = solve(model)
    trail, counts = trail_for(assets, window, facts)
    place(solution, trail)
    rev = reverse_test(inputs, solution)
    check = window_check(model, solution, n_verified=len(verdicts) or None)
    return RollRun(key, frames, facts, window, source, pool, events, event_ids, sims, candidates,
                   verdicts, inputs, solution, rev, check, counts)


def to_json(r: RollRun) -> dict:
    def t(x: datetime | None):
        return x.isoformat() if x else None

    return {
        "roll": r.key,
        "window": {"start": t(r.window.start), "end": t(r.window.end), "source": r.window_source},
        "pool": len(r.pool),
        "events": len(r.events),
        "trail": r.trail_counts,
        "verified_frames": len(r.verdicts),
        "anchored": r.solution.anchored,
        "log_score": r.solution.log_score,
        "reverse": asdict(r.reverse),
        "window_check": {**asdict(r.check), "best_days": [(d.isoformat(), round(m, 2)) for d, m in r.check.best_days]},
        "frames": [
            {
                "number": f.number,
                "source": a.source,
                "time": t(a.time),
                "tzoffset": a.tzoffset,
                "offset_disputed": a.offset_disputed,
                "t_lo": t(a.t_lo),
                "t_hi": t(a.t_hi),
                "confidence": round(a.confidence, 4),
                "outside_mass": round(a.outside_mass, 4),
                "anchor_uuid": a.anchor_uuid,
                "event": a.event,
                "lat": a.lat,
                "lon": a.lon,
                "location": a.location,
                "location_source": a.location_source,
                "clusters": [asdict(c) | {"first": t(c.first), "last": t(c.last)} for c in a.clusters],
                "truth": t(f.truth),
            }
            for f, a in zip(r.frames, r.solution.assignments)
        ],
    }


def save(r: RollRun, directory: Path = ASSIGNMENTS_DIR) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    p = directory / f"{r.key}.json"
    p.write_text(json.dumps(to_json(r), indent=1) + "\n")
    return p
