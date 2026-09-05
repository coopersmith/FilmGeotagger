"""What the local API holds between requests: the library, and one solved run per roll.

A `RollRun` is expensive to build (library cache, pool vectors, similarities) and cheap to
re-solve (milliseconds), so the store keeps each roll's run in memory and re-solves it in
place when an override or a fact arrives. The pool only has to be rebuilt when the window
moves, and `pipeline.resolve` says so by raising `WindowChanged`.

Persistence is the same files the CLI uses — facts, overrides, verdicts, assignments under
`.filmgeo/` — so a roll reviewed here is the roll `filmgeo align` reports on, and M4's write
step reads the assignments file either way. `data_dir` is a parameter so tests run against a
scratch directory with a fake loader and never touch the real caches.
"""

from __future__ import annotations

import copy
import dataclasses
import json
import threading
from datetime import timedelta
from pathlib import Path
from typing import Callable

from filmgeo.align import pipeline
from filmgeo.align.overrides import RollOverrides
from filmgeo.align.pipeline import RollRun, WindowChanged
from filmgeo.config import DATA_DIR
from filmgeo.photos.library import Asset
from filmgeo.signals.user_facts import RollFacts

Loader = Callable[..., RollRun]


class Store:
    def __init__(self, data_dir: Path = DATA_DIR, loader: Loader | None = None,
                 assets_loader: Callable[[], list[Asset]] | None = None, origins: dict[str, str] | None = None):
        self.data_dir = Path(data_dir)
        self.facts_dir = self.data_dir / "facts"
        self.overrides_dir = self.data_dir / "overrides"
        self.assignments_dir = self.data_dir / "assignments"
        self.thumbs_dir = self.data_dir / "thumbs"
        self.loader = loader or pipeline.run
        self.assets_loader = assets_loader
        self.origins: dict[str, str] = dict(origins or {})
        self.runs: dict[str, RollRun] = {}
        self.lock = threading.RLock()
        self._assets: list[Asset] | None = None

    # -- library ---------------------------------------------------------------------------

    @property
    def assets(self) -> list[Asset]:
        if self._assets is None:
            if self.assets_loader is not None:
                self._assets = self.assets_loader()
            else:
                from filmgeo.photos import library

                self._assets = library.load()
        return self._assets

    def asset(self, uuid: str) -> Asset | None:
        return next((a for a in self.assets if a.uuid == uuid), None)

    # -- roll registry ---------------------------------------------------------------------

    def _assignment(self, key: str) -> dict | None:
        p = self.assignments_dir / f"{key}.json"
        if not p.exists():
            return None
        try:
            return json.loads(p.read_text())
        except json.JSONDecodeError:
            return None

    def eval_keys(self) -> set[str]:
        from filmgeo import eval_set

        return {r.key for r in eval_set.rolls(self.assets)}

    def keys(self) -> list[str]:
        """Every roll the store knows about: registered, on disk, or hand-tagged."""
        found: set[str] = set(self.origins) | set(self.runs)
        for d in (self.assignments_dir, self.facts_dir, self.overrides_dir):
            if d.exists():
                found |= {p.stem for p in d.glob("*.json")}
        try:
            found |= self.eval_keys()
        except FileNotFoundError:
            pass  # no library cache: only what is on disk
        return sorted(found)

    def origin_for(self, key: str) -> str:
        """What `pipeline.run` should be given for this key."""
        if key in self.origins:
            return self.origins[key]
        if key in self.runs:
            return self.runs[key].origin
        a = self._assignment(key)
        if a and a.get("origin"):
            return a["origin"]
        try:
            evals = self.eval_keys()
        except FileNotFoundError:
            evals = set()
        if key in evals:
            return key
        base, _, suffix = key.rpartition("-")   # an alias such as 00007044-k12
        if suffix and base in evals:
            return base
        raise KeyError(key)

    def summary(self, key: str) -> dict:
        """The roll list entry, read from disk without loading the roll."""
        facts = RollFacts.load(key, self.facts_dir)
        out = {
            "key": key,
            "loaded": key in self.runs,
            "aligned": False,
            "facts": {"window_from": facts.window_from, "window_to": facts.window_to, "camera": facts.camera,
                      "film": facts.film, "lab": facts.lab},
        }
        try:
            out["origin"] = self.origin_for(key)
        except KeyError:
            out["origin"] = None
        a = self._assignment(key)
        if a:
            frames = a.get("frames", [])
            out.update({
                "aligned": True,
                "n_frames": len(frames),
                "window": a.get("window"),
                "anchored": a.get("anchored"),
                "verified_frames": a.get("verified_frames"),
                "confirmed": sum(1 for f in frames if f.get("status") == "confirmed"),
                "doubtful": (a.get("window_check") or {}).get("doubtful"),
            })
        return out

    # -- runs ------------------------------------------------------------------------------

    def _load(self, key: str, facts: RollFacts | None = None, overrides: RollOverrides | None = None,
              widen: bool = False) -> RollRun:
        origin = self.origin_for(key)
        folder = Path(origin).expanduser()
        if folder.is_dir():
            from filmgeo.write import sidecar

            sidecar.adopt(key, folder, self.facts_dir, self.overrides_dir)   # a roll written elsewhere: its decisions come along
        facts = facts or RollFacts.load(key, self.facts_dir)
        overrides = overrides or RollOverrides.load(key, self.overrides_dir)
        run = self.loader(origin, alias=key, assets=self.assets, facts=facts, overrides=overrides, widen=widen)
        self.runs[key] = run
        pipeline.save(run, self.assignments_dir)
        return run

    def get(self, key: str) -> RollRun:
        with self.lock:
            if key not in self.runs:
                self._load(key)
            return self.runs[key]

    def reload(self, key: str) -> RollRun:
        """From disk again: new verdicts from a `filmgeo verify` run in the terminal, say."""
        with self.lock:
            return self._load(key)

    def update(self, key: str, facts: RollFacts | None = None, overrides: RollOverrides | None = None) -> RollRun:
        """Re-solve under edited facts and/or overrides; persist all three files only on success.

        The solver can refuse — a locked photo before an earlier locked photo, a fact that
        leaves a frame no state — and then nothing is written, so the roll stays consistent
        on disk with what the user last saw.
        """
        with self.lock:
            run = self.get(key)
            facts = facts if facts is not None else run.facts
            overrides = overrides if overrides is not None else run.overrides
            try:
                new = pipeline.resolve(run, facts=facts, overrides=overrides)
            except WindowChanged:
                facts.save(self.facts_dir)
                if overrides is not None:
                    overrides.save(self.overrides_dir)
                return self._load(key, facts=facts, overrides=overrides)
            facts.save(self.facts_dir)
            if overrides is not None:
                overrides.save(self.overrides_dir)
            self.runs[key] = new
            pipeline.save(new, self.assignments_dir)
            return new

    def written(self, key: str) -> dict[int, dict]:
        """The sidecar's per-frame record for a roll aligned from a scan folder; empty otherwise."""
        try:
            folder = Path(self.origin_for(key)).expanduser()
        except KeyError:
            return {}
        if not folder.is_dir():
            return {}
        from filmgeo.write import sidecar

        return sidecar.written_frames(folder)

    # -- writing -----------------------------------------------------------------------------

    def folder_for(self, key: str) -> Path | None:
        try:
            folder = Path(self.origin_for(key)).expanduser()
        except KeyError:
            return None
        return folder if folder.is_dir() else None

    def write_plan(self, key: str, force: bool = False):
        """The write plan for a roll's current assignments, against the files as they are now."""
        from filmgeo.write import exiftool as w, sidecar

        run = self.get(key)
        folder = self.folder_for(key)
        if folder is None:
            raise w.WriteError(f"{key} was aligned from {run.origin!r}, which is not a scan folder — its frames live in the Photos library.")
        files = w.scan_files(folder)
        return w.plan(key, folder, pipeline.to_json(run), run.facts, files=files, current=w.current_tags(files),
                      written=sidecar.written_frames(folder), force=force)

    def write(self, key: str, force: bool = False):
        """Backup, write, verify, record, sidecar — the same chain as `filmgeo write --write`."""
        from filmgeo.write import ops

        with self.lock:
            run = self.get(key)
            p = self.write_plan(key, force)
            verdicts = {n: dataclasses.asdict(v) for n, v in run.verdicts.items()}
            return p, ops.write_roll(p, self.data_dir / "writes", assignments=pipeline.to_json(run), verdicts=verdicts,
                                     facts=run.facts, overrides=run.overrides)

    def restore(self, key: str):
        from filmgeo.write import exiftool as w, ops

        folder = self.folder_for(key)
        if folder is None:
            raise w.WriteError(f"{key} has no scan folder to restore")
        with self.lock:
            return ops.restore(folder)

    def edit(self, key: str) -> tuple[RollFacts, RollOverrides]:
        """Deep copies to edit; hand them back to `update`, which discards them if the solve fails."""
        run = self.get(key)
        return copy.deepcopy(run.facts), copy.deepcopy(run.overrides or RollOverrides(key))

    def widen(self, key: str, months: int = 1) -> RollRun:
        """Persist a month more on each side into the facts window, then rebuild the pool.

        Written as calendar days in the roll's zone, because that is what the facts file
        speaks; `filmgeo verify --widen --only-new` then verifies just what the wider window
        surfaced.
        """
        with self.lock:
            run = self.get(key)
            facts = copy.deepcopy(run.facts)
            pad = timedelta(days=31 * months)
            zone = facts.zone
            lo = (run.window.start - pad).astimezone(zone)
            hi = (run.window.end + pad - timedelta(seconds=1)).astimezone(zone)
            facts.window_from = lo.strftime("%Y-%m-%d")
            facts.window_to = hi.strftime("%Y-%m-%d")
            facts.save(self.facts_dir)
            return self._load(key, facts=facts, overrides=run.overrides)
