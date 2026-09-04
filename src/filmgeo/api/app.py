"""The local review API (COO-121). The browser UI (COO-122+) and any future native shell are clients.

Bound to 127.0.0.1 by `filmgeo serve`; nothing here authenticates, because nothing here is
reachable off the machine. Routes live under `/api` so the web build can own `/`.

    GET  /api/rolls                                 every roll known: on disk, registered, hand-tagged
    GET  /api/rolls/{key}                           header: window, checks, events, facts, outings
    GET  /api/rolls/{key}/frames                    every frame: assignment, candidates, verdict, override
    GET  /api/rolls/{key}/frames/{n}
    GET  /api/rolls/{key}/photos?event=N | ?start=&end=   the pool's photos of one event or an instant range
    GET  /api/rolls/{key}/frames/{n}/trail?pad_minutes=   trail points with GPS inside the frame's interval
    PUT  /api/rolls/{key}/frames/{n}/assign         an override or a frame fact; re-solves, returns all frames
    GET  /api/rolls/{key}/facts
    PUT  /api/rolls/{key}/facts                     the whole facts file; re-solves (rebuilds the pool if the window moved)
    POST /api/rolls/{key}/realign                   {"widen": bool}: from disk again, optionally a month wider each side
    GET  /api/rolls/{key}/frames/{n}/image?size=    small | large
    GET  /api/photos/{uuid}/image?size=

Errors: 404 for an unknown roll, frame or photo; 409 when the solver refuses (a lock that
contradicts scan order, a fact that leaves no state) — nothing is persisted then; 422 for a
malformed body or facts that fail `RollFacts.validate`; 403 when an image is unreadable from
this process (Photos derivatives outside Terminal.app).
"""

from __future__ import annotations

import dataclasses
import threading
import webbrowser
from datetime import datetime, timedelta
from pathlib import Path

from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import FileResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from filmgeo.align import pipeline
from filmgeo.align.overrides import RollOverrides
from filmgeo.align.pipeline import RollRun
from filmgeo.align.report import interval_text
from filmgeo.api.state import Store
from filmgeo.api.thumbs import thumbnail
from filmgeo.signals.user_facts import FrameFact, RollFacts, parse_period

WEB_DIST = Path(__file__).resolve().parents[3] / "web" / "dist"


# -- bodies --------------------------------------------------------------------------------


class AssignBody(BaseModel):
    """One user decision about a frame. Fields combine; `unlock` runs first."""

    anchor: str | None = Field(None, description="uuid of the phone photo this frame shows: a locked anchor")
    reject: list[str] = Field(default_factory=list, description="'not a match': verdict anchors on these uuids are dropped")
    no_reference: bool | None = Field(None, description="'no reference': no phone photo shows this frame")
    when: str | None = Field(None, description="a period in the roll's zone ('2026-04-12 14:05', '2026-04-12'), or ISO-8601 with offset")
    lat: float | None = None
    lon: float | None = None
    radius_m: float | None = None
    place_name: str | None = None
    same_day_as: int | None = None
    skip: bool | None = None
    note: str | None = None
    confirmed: bool | None = None
    unlock: bool = Field(False, description="drop the override and the frame's facts before applying the rest")


class RealignBody(BaseModel):
    widen: bool = False


class FactsBody(BaseModel):
    window_from: str | None = None
    window_to: str | None = None
    tz: str | None = None
    camera: str | None = None
    film: str | None = None
    lab: str | None = None
    notes: str | None = None
    reverse: bool = False
    frames: dict[int, dict] = Field(default_factory=dict)


# -- serialisation -------------------------------------------------------------------------


def _t(x: datetime | None) -> str | None:
    return x.isoformat() if x else None


def _photo(a, r: RollRun) -> dict:
    return {"uuid": a.uuid, "time": _t(a.date), "tzoffset": a.tzoffset, "lat": a.lat, "lon": a.lon,
            "filename": a.filename, "image": f"/api/photos/{a.uuid}/image"}


def roll_json(r: RollRun) -> dict:
    base = pipeline.to_json(r)
    base.pop("frames")
    base["n_frames"] = r.n_frames
    base["facts"] = facts_json(r.facts)
    base["events"] = [
        {"index": e.index, "start": _t(e.start), "end": _t(e.end), "lat": e.lat, "lon": e.lon,
         "spread_m": e.spread_m, "count": e.count}
        for e in r.events
    ]
    base["confirmed"] = sum(1 for o in (r.overrides.frames.values() if r.overrides else ()) if o.confirmed)
    return base


def frame_json(r: RollRun, i: int) -> dict:
    f, a = r.frames[i], r.solution.assignments[i]
    by_uuid = {p.uuid: p for p in r.pool}
    base = pipeline.to_json(r)["frames"][i]
    v = r.verdicts.get(f.number)
    shown = set(v.candidates) if v else set()
    o = (r.overrides or RollOverrides(r.key)).get(f.number)
    ff = r.facts.frames.get(f.number)
    event_by_uuid = {p.uuid: e for p, e in zip(r.pool, r.event_ids)}
    cands = []
    for c in r.candidates.get(f.number, []):
        cands.append(_photo(c.asset, r) | {
            "score": round(c.score, 4), "event": event_by_uuid.get(c.asset.uuid),
            "shown": c.asset.uuid in shown,
            "verdict": "match" if v and v.match == c.asset.uuid else ("no" if c.asset.uuid in shown else None),
            "rejected": bool(o and c.asset.uuid in o.rejected),
        })
    base.update({
        "image": f"/api/rolls/{r.key}/frames/{f.number}/image",
        "interval_text": interval_text(a),
        "candidates": cands,
        "anchor": _photo(by_uuid[a.anchor_uuid], r) if a.anchor_uuid in by_uuid else None,
        "verdict": None if v is None else {"match": v.match, "confidence": v.confidence, "evidence": v.evidence,
                                           "clues": v.clues, "shown": v.candidates},
        "override": None if o is None else dataclasses.asdict(o),
        "fact": None if ff is None or ff.is_empty else dataclasses.asdict(ff),
        "outing": next((k for k, g in enumerate(r.outings.groups, 1) if f.number in g["frames"]), None) if r.outings else None,
    })
    return base


def frames_json(r: RollRun) -> list[dict]:
    return [frame_json(r, i) for i in range(r.n_frames)]


def facts_json(facts: RollFacts) -> dict:
    d = dataclasses.asdict(facts)
    d["frames"] = {str(n): dataclasses.asdict(f) for n, f in sorted(facts.frames.items()) if not f.is_empty}
    return d


# -- app -----------------------------------------------------------------------------------


def create_app(store: Store | None = None) -> FastAPI:
    store = store or Store()
    app = FastAPI(title="filmgeo", version="0.1")
    app.state.store = store

    def run_for(key: str) -> RollRun:
        try:
            return store.get(key)
        except KeyError:
            raise HTTPException(404, f"no roll {key!r}")
        except FileNotFoundError as e:
            raise HTTPException(404, str(e))
        except (ValueError, RuntimeError) as e:
            raise HTTPException(409, str(e))

    def frame_index(r: RollRun, n: int) -> int:
        i = next((i for i, f in enumerate(r.frames) if f.number == n), None)
        if i is None:
            raise HTTPException(404, f"roll {r.key} has no frame {n}")
        return i

    def solved(fn):
        try:
            return fn()
        except pipeline.WindowChanged as e:
            raise HTTPException(409, str(e))
        except (ValueError, RuntimeError) as e:
            raise HTTPException(409, str(e))

    @app.get("/api/rolls")
    def list_rolls() -> list[dict]:
        return [store.summary(k) for k in store.keys()]

    @app.get("/api/rolls/{key}")
    def get_roll(key: str) -> dict:
        return roll_json(run_for(key))

    @app.get("/api/rolls/{key}/frames")
    def get_frames(key: str) -> list[dict]:
        return frames_json(run_for(key))

    @app.get("/api/rolls/{key}/frames/{n}")
    def get_frame(key: str, n: int) -> dict:
        r = run_for(key)
        return frame_json(r, frame_index(r, n))

    @app.get("/api/rolls/{key}/photos")
    def roll_photos(key: str, event: int | None = Query(None), start: str | None = Query(None), end: str | None = Query(None),
                    limit: int = Query(200, ge=1, le=1000)) -> list[dict]:
        """Phone photos in the roll's pool: one event, or an instant range — for picking any photo as the anchor."""
        r = run_for(key)
        if event is None and not (start and end):
            raise HTTPException(422, "give ?event=N or ?start=ISO&end=ISO")
        # A "+" in an offset arrives as a space unless the client encoded it; take both.
        lo = datetime.fromisoformat(start.replace(" ", "+")) if start else None
        hi = datetime.fromisoformat(end.replace(" ", "+")) if end else None
        out = []
        for a, e in zip(r.pool, r.event_ids):
            if event is not None and e != event:
                continue
            if lo is not None and hi is not None and not (lo <= a.date <= hi):
                continue
            out.append(_photo(a, r) | {"event": e})
            if len(out) >= limit:
                break
        return out

    @app.get("/api/rolls/{key}/frames/{n}/trail")
    def frame_trail(key: str, n: int, pad_minutes: int = Query(0, ge=0, le=1440)) -> list[dict]:
        """Trail points with a location inside the frame's interval (padded), for the map."""
        r = run_for(key)
        a = r.solution.assignments[frame_index(r, n)]
        pad = timedelta(minutes=pad_minutes)
        lo, hi = a.t_lo - pad, a.t_hi + pad
        return [
            {"time": _t(p.time), "lat": p.lat, "lon": p.lon, "source": p.source, "tzoffset": p.tzoffset,
             "label": p.label, "ref": p.ref, "camera": p.camera}
            for p in r.trail if p.has_location and lo <= p.time <= hi
        ]

    @app.put("/api/rolls/{key}/frames/{n}/assign")
    def assign(key: str, n: int, body: AssignBody) -> list[dict]:
        r = run_for(key)
        frame_index(r, n)
        facts, overrides = store.edit(key)
        if body.unlock:
            overrides.frames.pop(n, None)
            facts.frames.pop(n, None)
        o = overrides.frame(n)
        if body.anchor is not None:
            if body.anchor not in {p.uuid for p in r.pool}:
                raise HTTPException(404, f"photo {body.anchor} is not in this roll's window")
            o.anchor, o.no_reference = body.anchor, False
        if body.reject:
            o.rejected = sorted(set(o.rejected) | set(body.reject))
            if o.anchor in body.reject:
                o.anchor = None
        if body.no_reference is not None:
            o.no_reference = body.no_reference
            if body.no_reference:
                o.anchor = None
        fact_fields = (body.when, body.lat, body.lon, body.radius_m, body.place_name, body.same_day_as, body.skip, body.note)
        changed = body.unlock or body.anchor is not None or bool(body.reject) or body.no_reference is not None \
            or any(x is not None for x in fact_fields)
        if body.confirmed is not None:
            o.confirmed = body.confirmed
        elif changed:
            o.confirmed = False          # a confirmation is of an assignment; a new assignment needs a new one
        if body.skip:
            o.anchor, o.no_reference = None, False   # "unknown" means no photo, not a locked one
        if any(x is not None for x in fact_fields):
            ff = facts.frame(n)
            if body.when is not None:
                ff.when = _as_period(body.when, facts)
            if (body.lat is None) != (body.lon is None):
                raise HTTPException(422, "a place needs both lat and lon")
            if body.lat is not None:
                ff.lat, ff.lon = body.lat, body.lon
            for k in ("radius_m", "place_name", "same_day_as", "skip", "note"):
                v = getattr(body, k)
                if v is not None:
                    setattr(ff, k, v)
        problems = facts.validate(r.n_frames)
        if problems:
            raise HTTPException(422, "; ".join(problems))
        new = solved(lambda: store.update(key, facts=facts, overrides=overrides))
        return frames_json(new)

    @app.get("/api/rolls/{key}/facts")
    def get_facts(key: str) -> dict:
        if key in store.runs:
            return facts_json(store.runs[key].facts)
        return facts_json(RollFacts.load(key, store.facts_dir))

    @app.put("/api/rolls/{key}/facts")
    def put_facts(key: str, body: FactsBody) -> dict:
        try:
            store.origin_for(key)
        except KeyError:
            raise HTTPException(404, f"no roll {key!r}")
        data = body.model_dump()
        frames = {int(k): FrameFact(**({"number": int(k)} | v)) for k, v in data.pop("frames").items()}
        facts = RollFacts(roll=key, **data, frames=frames)
        n_frames = store.runs[key].n_frames if key in store.runs else None
        problems = facts.validate(n_frames)
        if problems:
            raise HTTPException(422, "; ".join(problems))
        if key in store.runs:
            new = solved(lambda: store.update(key, facts=facts))
            return {"facts": facts_json(new.facts), "solved": True, "error": None, "roll": roll_json(new)}
        facts.save(store.facts_dir)
        try:
            new = store.get(key)
        except (ValueError, RuntimeError, FileNotFoundError) as e:
            return {"facts": facts_json(facts), "solved": False, "error": str(e), "roll": None}
        return {"facts": facts_json(new.facts), "solved": True, "error": None, "roll": roll_json(new)}

    @app.post("/api/rolls/{key}/realign")
    def realign(key: str, body: RealignBody | None = None) -> dict:
        run_for(key)
        body = body or RealignBody()
        new = solved(lambda: store.widen(key) if body.widen else store.reload(key))
        return roll_json(new) | {"frames": frames_json(new)}

    @app.get("/api/rolls/{key}/frames/{n}/image")
    def frame_image(key: str, n: int, size: str = Query("small", pattern="^(small|large)$")):
        r = run_for(key)
        f = r.frames[frame_index(r, n)]
        if not f.path:
            raise HTTPException(404, f"frame {n} has no image")
        return _image(f.path, f.key, size)

    @app.get("/api/photos/{uuid}/image")
    def photo_image(uuid: str, size: str = Query("small", pattern="^(small|large)$")):
        a = store.asset(uuid)
        if a is None:
            raise HTTPException(404, f"no photo {uuid}")
        if not a.derivative:
            raise HTTPException(404, f"photo {uuid} has no local derivative")
        return _image(a.derivative, uuid, size)

    def _image(src: str, cache_key: str, size: str) -> FileResponse:
        try:
            p = thumbnail(src, cache_key, size, store.thumbs_dir)
        except PermissionError:
            raise HTTPException(403, f"cannot read {src}: macOS denies this process access to the Photos library. "
                                     "Run `filmgeo serve` from Terminal.app, which has Full Disk Access.")
        except FileNotFoundError:
            raise HTTPException(404, f"missing file {src}")
        except OSError as e:
            raise HTTPException(415, f"cannot decode {src}: {e}")
        return FileResponse(p, media_type="image/jpeg", headers={"Cache-Control": "max-age=31536000, immutable"})

    if WEB_DIST.is_dir():
        app.mount("/", StaticFiles(directory=WEB_DIST, html=True), name="web")
    else:
        @app.get("/", include_in_schema=False)
        def index():
            return RedirectResponse("/docs")

    return app


def _as_period(text: str, facts: RollFacts) -> str:
    """A facts period as given, or an ISO instant with offset rendered to the minute in the roll's zone."""
    try:
        parse_period(text, facts.zone)
        return text.strip()
    except ValueError:
        pass
    try:
        t = datetime.fromisoformat(text)
    except ValueError:
        raise HTTPException(422, f"cannot read {text!r} as a period or an ISO-8601 time")
    if t.tzinfo is None:
        raise HTTPException(422, f"{text!r} needs a UTC offset, or use a period in the roll's zone")
    return t.astimezone(facts.zone).strftime("%Y-%m-%d %H:%M")


def serve(store: Store, host: str = "127.0.0.1", port: int = 8765, open_browser: bool = True) -> None:
    import uvicorn

    app = create_app(store)
    if open_browser:
        threading.Timer(1.0, lambda: webbrowser.open(f"http://{host}:{port}/")).start()
    uvicorn.run(app, host=host, port=port, log_level="info")
