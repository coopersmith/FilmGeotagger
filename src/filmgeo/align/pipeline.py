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
from filmgeo.align.overrides import RollOverrides
from filmgeo.align.solve import Solution, solve
from filmgeo.config import DATA_DIR, MAX_PER_EVENT, TOP_K
from filmgeo.embed.cache import VectorCache
from filmgeo.geo import place
from filmgeo.photos import library
from filmgeo.photos.library import Asset
from filmgeo.signals.base import TrailPoint, Window, collect, effective_window
from filmgeo.signals.health_routes import HEALTH_DIR, HealthRoutes
from filmgeo.signals.nfc_log import CACHE as NFC_CACHE, NfcLog
from filmgeo.signals.photos_trail import PhotosTrail
from filmgeo.signals.user_facts import SOURCE as USER_SOURCE, RollFacts, UserFacts
from filmgeo.verify.outing import Outings

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


def verdicts_meta(key: str, directory: Path = VERDICTS_DIR) -> dict:
    """The run settings `filmgeo verify` recorded beside the verdicts: model, k, cap."""
    p = directory / f"{key}.json"
    if not p.exists():
        return {}
    raw = json.loads(p.read_text())
    return {k: v for k, v in raw.items() if k != "frames"}


# $0.035 a frame at K = 6 on claude-opus-5 (M1), linear in images shown; the outing pass is
# one call with a contact sheet, about $0.15 (COO-119). Estimates, not a meter — COO-140
# builds the real dashboard from logged tokens.
VERIFY_USD_PER_FRAME_AT_K6 = 0.035
OUTING_USD = 0.15


def cost_estimate(n_verified: int, k: int | None, has_outings: bool) -> dict:
    k = k or TOP_K
    verify = VERIFY_USD_PER_FRAME_AT_K6 * n_verified * k / 6
    return {"verified_frames": n_verified, "k": k, "verify_usd": round(verify, 2),
            "outing_usd": OUTING_USD if has_outings else 0.0, "usd": round(verify + (OUTING_USD if has_outings else 0.0), 2)}


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
    outings: Outings | None = None
    origin: str = ""                                    # what `resolve_frames` was given: folder or eval key
    trail: list[TrailPoint] = field(default_factory=list)
    overrides: RollOverrides | None = None
    possible: dict[int, list[retrieve.Candidate]] = field(default_factory=dict)   # by frame number: inside the interval
    exact_variant: str = "siglip"      # how anchored frames' occasion photos are ranked: siglip_gray when cached

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

        name, gray = {"siglip": ("SigLIP", False), "siglip_gray": ("SigLIP", True), "dinov2": ("DINOv2", False)}[variant]
        embedder = getattr(models, name)(grayscale=gray)
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
    if HEALTH_DIR.is_dir():
        signals.append(HealthRoutes(HEALTH_DIR, offset_at=photos.offset_at))
    evidence = collect(signals, window)
    return evidence.trail, _trail_counts(evidence.trail)


def _trail_counts(trail: list[TrailPoint]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for p in trail:
        counts[p.source] = counts.get(p.source, 0) + 1
    return counts


def window_for(facts: RollFacts, truth: eval_set.Roll | None, pad_days: int = 2, widen: bool = False) -> tuple[Window, str]:
    """The facts window if the user gave one, else a hand-tagged roll's true range padded."""
    constraints = UserFacts(facts).constraints()
    f_lo, f_hi = facts.window()
    if f_lo and f_hi:
        window, source = effective_window(constraints, Window(f_lo, f_hi)), "facts"
    elif truth is not None:
        window, source = Window.around(truth.start, truth.end, pad_days), f"hand-tagged range +/-{pad_days}d (no facts)"
    else:
        raise ValueError(f"no window for {facts.roll}: set one with `filmgeo facts {facts.roll} --from ... --to ...`")
    if widen:
        window, source = widen_window(window), source + ", widened +/-1 month"
    return window, source


def solve_run(key: str, origin: str, frames: list[FrameRef], facts: RollFacts, window: Window, window_source: str,
              pool: list[Asset], events: list, event_ids: list[int], sims: np.ndarray,
              candidates: dict[int, list[retrieve.Candidate]], verdicts: dict[int, Verdict],
              trail: list[TrailPoint], outings: Outings | None = None,
              overrides: RollOverrides | None = None) -> RollRun:
    """Everything after the caches: anchors, constraints, solve, place, checks.

    Pure over its arguments, so the review API can re-solve a roll in milliseconds after an
    override or a fact without touching the library or the vectors. `trail` may carry user
    points from an earlier facts state; they are replaced by the current facts' own.
    """
    n = len(frames)
    overrides = overrides or RollOverrides(key)
    constraints = UserFacts(facts).constraints()
    anchors = anchors_from_verdicts(verdicts, pool, event_ids, sims)
    anchors = overrides.apply(anchors, pool, event_ids, sims)
    clues = clues_from_verdicts(verdicts, n)
    same_outing = outings.same_outing_pairs(n) if outings else set()
    inputs = RollInputs(window, events, n, anchors, sims, event_ids, clues, constraints, same_outing)
    model = inputs.build()
    solution = solve(model)
    trail = sorted([p for p in trail if p.source != USER_SOURCE] + UserFacts(facts).trail_points(window), key=lambda p: p.time)
    pins = {k - 1: (f.lat, f.lon) for k, f in facts.frames.items() if f.lat is not None and f.lon is not None and 1 <= k <= n}
    place(solution, trail, pins)
    rev = reverse_test(inputs, solution)
    check = window_check(model, solution, n_verified=len(verdicts) or None)
    possible = possible_candidates(frames, solution, pool, event_ids, sims)
    exact_variant = exact_ranking(frames, solution, pool, event_ids, possible)
    return RollRun(key, frames, facts, window, window_source, pool, events, event_ids, sims, candidates,
                   verdicts, inputs, solution, rev, check, _trail_counts(trail), outings,
                   origin=origin, trail=trail, overrides=overrides, possible=possible, exact_variant=exact_variant)


POSSIBLE_K = 8
EXACT_K = 12
EXACT_VARIANT = "siglip_gray"


def exact_ranking(frames: list[FrameRef], solution: Solution, pool: list[Asset], event_ids: list[int],
                  possible: dict[int, list[retrieve.Candidate]], variant: str = EXACT_VARIANT, k: int = EXACT_K) -> str:
    """The grayscale second stage (COO-148): rank an anchored frame's occasion for the exact shot.

    SigLIP in colour finds the scene, not the shot — the exact anchor photo sits median 10th
    inside its own event. In grayscale it sits median 5th (COO-146), because film and phone
    disagree on colour more than on form. So once verification has named the occasion, the
    photos of that occasion are re-ranked by grayscale similarity and offered as "the exact
    photo, if it exists". Only when every vector needed is cached: this never embeds Photos
    derivatives (unreadable from most shells) and falls back to the colour ranking, saying so.
    Returns the variant actually used. Edits `possible` in place for anchored frames.
    """
    anchored = [(i, a) for i, a in enumerate(solution.assignments) if a.source in ("anchored", "locked") and a.event is not None]
    if not anchored:
        return "siglip"
    cache = VectorCache(variant)
    frame_keys = [frames[i].key for i, _ in anchored]
    members = {e: [j for j, ev in enumerate(event_ids) if ev == e] for e in {a.event for _, a in anchored}}
    pool_keys = [pool[j].uuid for js in members.values() for j in js]
    if cache.missing(frame_keys) or cache.missing(pool_keys):
        for i, a in anchored:                      # colour, but the whole occasion, most similar first
            possible[frames[i].number] = possible.get(frames[i].number, [])[:k]
        return "siglip"
    for i, a in enumerate(solution.assignments):
        if a.source not in ("anchored", "locked") or a.event is None:
            continue
        js = members[a.event]
        fv = cache.get([frames[i].key])[0]
        pv = cache.get([pool[j].uuid for j in js])
        scores = pv @ fv
        order = sorted(range(len(js)), key=lambda x: -scores[x])[:k]
        possible[frames[i].number] = [retrieve.Candidate(pool[js[x]], float(scores[x]), {variant: float(scores[x])}) for x in order]
    return variant


def possible_candidates(frames: list[FrameRef], solution: Solution, pool: list[Asset], event_ids: list[int],
                        sims: np.ndarray, k: int = POSSIBLE_K, cap: int = 1) -> dict[int, list[retrieve.Candidate]]:
    """Per frame, the most similar photos *inside its interval* — the ones its neighbours allow.

    The shortlist retrieval builds before the solve ranks the whole window by similarity, so
    a frame between two anchored days is offered photos from three weeks away (review
    feedback on `00007044`, frame 4). Once the roll is solved the interval is known and the
    same cached similarities, masked to it, give the list the user can actually act on — and
    the list a second verification round should see. Cap 1 per event, like the shortlist.
    """
    out: dict[int, list[retrieve.Candidate]] = {}
    dates = [a.date for a in pool]
    for i, (f, a) in enumerate(zip(frames, solution.assignments)):
        row = sims[i]
        allowed = [j for j, d in enumerate(dates) if a.t_lo <= d <= a.t_hi]
        # An anchored frame's interval *is* one occasion: offer every photo of it, most
        # similar first, so the exact shot can be picked by hand.
        frame_cap = 0 if a.source in ("anchored", "locked") else cap
        seen: dict[int, int] = {}
        picks: list[retrieve.Candidate] = []
        for j in sorted(allowed, key=lambda j: -row[j]):
            e = event_ids[j]
            if frame_cap and seen.get(e, 0) >= frame_cap:
                continue
            seen[e] = seen.get(e, 0) + 1
            picks.append(retrieve.Candidate(pool[j], float(row[j]), {"siglip": float(row[j])}))
            if len(picks) >= k:
                break
        out[f.number] = picks
    return out


def run(roll: str, pad_days: int = 2, k: int = TOP_K, widen: bool = False, assets: list[Asset] | None = None,
        alias: str | None = None, cap: int | None = MAX_PER_EVENT, facts: RollFacts | None = None,
        overrides: RollOverrides | None = None) -> RollRun:
    """`alias` names the facts, verdicts and assignments files instead of the roll key — for
    running one roll under a second window (the wrong-month validation) without clobbering.
    `facts` and `overrides` default to the files on disk; the API passes its edited copies."""
    assets = assets or library.load()
    key, frames, truth = resolve_frames(roll, assets)
    if alias:
        key = alias
    facts = facts or RollFacts.load(key)
    window, source = window_for(facts, truth, pad_days, widen)

    pool = library.candidates(assets, window.start, window.end)
    if not pool:
        raise ValueError(f"no phone photos in the window {window.start:%Y-%m-%d} .. {window.end:%Y-%m-%d}")
    event_ids, events = ev.segment(pool)
    fv = frame_vectors(frames)
    pv = pool_vectors(pool)
    sims = fv @ pv.T
    candidates = {
        f.number: retrieve.top_k({"siglip": fv[i]}, {"siglip": pv}, pool, events=event_ids, k=k, max_per_event=cap or 0)
        for i, f in enumerate(frames)
    }
    verdicts = load_verdicts(key)
    outings = Outings.load(key)
    trail, _ = trail_for(assets, window, facts)
    overrides = overrides or RollOverrides.load(key)
    return solve_run(key, roll, frames, facts, window, source, pool, events, event_ids, sims, candidates,
                     verdicts, trail, outings, overrides)


def resolve(r: RollRun, facts: RollFacts | None = None, overrides: RollOverrides | None = None,
            verdicts: dict[int, Verdict] | None = None) -> RollRun:
    """Re-solve from a run's cached pool, events and similarities under new facts or overrides.

    Only valid while the window is unchanged — a new window means a new pool, which is
    `run()` again. The caller (the API's store) decides which; this raises rather than
    re-solving silently against a stale pool.
    """
    facts = facts or r.facts
    window, source = r.window, r.window_source
    f_lo, f_hi = facts.window()
    if f_lo and f_hi:
        window, source = window_for(facts, None)
    if window != r.window:
        raise WindowChanged(f"window moved to {window.start:%Y-%m-%d} .. {window.end:%Y-%m-%d}: reload the roll")
    return solve_run(r.key, r.origin, r.frames, facts, r.window, source, r.pool, r.events, r.event_ids, r.sims,
                     r.candidates, r.verdicts if verdicts is None else verdicts, r.trail, r.outings,
                     overrides if overrides is not None else r.overrides)


class WindowChanged(ValueError):
    """`resolve()` was asked to re-solve under a different window; the pool has to be rebuilt."""


def to_json(r: RollRun) -> dict:
    def t(x: datetime | None):
        return x.isoformat() if x else None

    overrides = r.overrides or RollOverrides(r.key)
    return {
        "roll": r.key,
        "origin": r.origin,
        "window": {"start": t(r.window.start), "end": t(r.window.end), "source": r.window_source},
        "pool": len(r.pool),
        "events": len(r.events),
        "trail": r.trail_counts,
        "verified_frames": len(r.verdicts),
        "outings": None if r.outings is None else {"groups": r.outings.groups, "out_of_sequence": r.outings.out_of_sequence, "notes": r.outings.notes},
        "same_outing_pairs": len(r.inputs.same_outing),
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
                "locked": _locked(f.number, a, r.facts, overrides),
                "status": "confirmed" if (o := overrides.get(f.number)) and o.confirmed else "proposed",
            }
            for f, a in zip(r.frames, r.solution.assignments)
        ],
    }


def _locked(number: int, a, facts: RollFacts, overrides: RollOverrides) -> bool:
    """A frame is locked when the user decided its matching or dated, placed or skipped it."""
    if a.source == "locked":
        return True
    o = overrides.get(number)
    if o and o.locks:
        return True
    ff = facts.frames.get(number)
    return bool(ff and (ff.when or ff.lat is not None or ff.skip))


def save(r: RollRun, directory: Path = ASSIGNMENTS_DIR) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    p = directory / f"{r.key}.json"
    p.write_text(json.dumps(to_json(r), indent=1) + "\n")
    return p
