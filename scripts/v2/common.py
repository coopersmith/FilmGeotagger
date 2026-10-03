"""Shared loading for the v2 evaluation: each roll's solved run, pickled once."""
from __future__ import annotations

import json
import pickle
from pathlib import Path

from filmgeo.config import DATA_DIR

OUT = DATA_DIR / "eval_v2"
RUNS = OUT / "runs"
ROLLS = ["874466", "874472", "874477", "00000120", "00000121", "00000122", "00000123", "00000124",
         "00000125", "00000126", "00000127"]


def origin_of(key: str) -> str:
    return json.loads((DATA_DIR / "assignments" / f"{key}.json").read_text())["origin"]


def load_run(key: str, assets=None, fresh: bool = False):
    RUNS.mkdir(parents=True, exist_ok=True)
    p = RUNS / f"{key}.pkl"
    if p.exists() and not fresh:
        return pickle.loads(p.read_bytes())
    from filmgeo.align import pipeline
    from filmgeo.photos import library

    r = pipeline.run(origin_of(key), assets=assets or library.load())
    p.write_bytes(pickle.dumps(r))
    return r


def truth() -> list[dict]:
    return json.loads((OUT / "truth.json").read_text())
