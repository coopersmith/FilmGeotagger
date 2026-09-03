"""Candidate retrieval: for each frame, the phone photos most likely to show the same moment."""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from filmgeo.config import MAX_PER_EVENT, TOP_K
from filmgeo.photos.library import Asset


@dataclass
class Candidate:
    asset: Asset
    score: float
    per_model: dict[str, float]


def zscore(x: np.ndarray) -> np.ndarray:
    """Similarities from different models live on different scales; z-scoring makes them addable.

    Standardised within the candidate pool, so a score answers "how much more like this frame
    than the rest of the window" rather than an absolute the models disagree about.
    """
    mu, sd = x.mean(), x.std()
    return (x - mu) / sd if sd > 1e-9 else np.zeros_like(x)


def fuse(sims: dict[str, np.ndarray], weights: dict[str, float] | None = None) -> np.ndarray:
    """Weighted sum of per-model z-scores. Equal weights until M1 measures otherwise."""
    weights = weights or {k: 1.0 for k in sims}
    total = sum(weights[k] for k in sims)
    return sum(zscore(v) * weights[k] for k, v in sims.items()) / total


def top_k(
    frame_vecs: dict[str, np.ndarray],
    pool_vecs: dict[str, np.ndarray],
    pool: list[Asset],
    events: list[int] | None = None,
    k: int = TOP_K,
    max_per_event: int = MAX_PER_EVENT,
    weights: dict[str, float] | None = None,
) -> list[Candidate]:
    """Fused top-k for one frame, capped per event.

    The cap exists because one heavily photographed scene would otherwise fill every slot and
    crowd out the other days a frame might belong to (PLAN.md).
    """
    sims = {name: pool_vecs[name] @ frame_vecs[name] for name in frame_vecs}
    fused = fuse(sims, weights)
    order = np.argsort(-fused)

    out: list[Candidate] = []
    seen: dict[int, int] = {}
    for i in order:
        if events is not None and max_per_event:
            e = events[i]
            if seen.get(e, 0) >= max_per_event:
                continue
            seen[e] = seen.get(e, 0) + 1
        out.append(Candidate(pool[i], float(fused[i]), {n: float(s[i]) for n, s in sims.items()}))
        if len(out) >= k:
            break
    return out
