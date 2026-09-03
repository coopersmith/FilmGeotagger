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


def zfuse(sims: dict[str, np.ndarray], weights: dict[str, float] | None = None) -> np.ndarray:
    """Weighted sum of per-model z-scores. Kept for comparison; `rrf` is the default."""
    weights = weights or {k: 1.0 for k in sims}
    total = sum(weights[k] for k in sims)
    return sum(zscore(v) * weights[k] for k, v in sims.items()) / total


def rrf(sims: dict[str, np.ndarray], k: float = 60.0) -> np.ndarray:
    """Reciprocal rank fusion — the default, measured in M1.

    z-scoring equalises variance but not tail shape. SigLIP and DINOv2 similarity distributions
    are peaked differently, so whichever model's top candidate sits further out in sigma
    dominates the sum regardless of which is actually right — which is why z-fusion scored at or
    below the better single model on every roll (77.0 SigLIP / 74.8 DINOv2 / 74.8 z-fused).
    RRF discards magnitude and combines positions, and beat both single models: 79.1% recall@8.
    """
    total = np.zeros(len(next(iter(sims.values()))))
    for s in sims.values():
        ranks = np.empty(len(s), dtype=np.int64)
        ranks[np.argsort(-s)] = np.arange(len(s))
        total += 1.0 / (k + ranks + 1)
    return total


def fuse(sims: dict[str, np.ndarray], weights: dict[str, float] | None = None) -> np.ndarray:
    return rrf(sims) if len(sims) > 1 else next(iter(sims.values()))


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
    crowd out the other days a frame might belong to (PLAN.md). Measured in M1, it is worth more
    than the choice of model: recall@32 is 92.8% with the cap and 83.5% without, and every method
    gains from it at every K.
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
