"""Vector cache keyed by (model variant, asset key).

Embedding the candidate pool dominates M1's runtime, and the same phone photos recur across
rolls in the same window, so nothing should ever be embedded twice.
"""

from __future__ import annotations

import numpy as np

from filmgeo.config import DATA_DIR

VECTORS = DATA_DIR / "vectors"


class VectorCache:
    def __init__(self, variant: str):
        self.dir = VECTORS / variant
        self.dir.mkdir(parents=True, exist_ok=True)
        self.keys_path = self.dir / "keys.npy"
        self.vecs_path = self.dir / "vecs.npy"
        if self.keys_path.exists():
            self.keys = list(np.load(self.keys_path, allow_pickle=True))
            self.vecs = np.load(self.vecs_path)
        else:
            self.keys, self.vecs = [], None
        self.index = {k: i for i, k in enumerate(self.keys)}

    def missing(self, keys: list[str]) -> list[str]:
        return [k for k in keys if k not in self.index]

    def add(self, keys: list[str], vecs: np.ndarray) -> None:
        if not keys:
            return
        self.vecs = vecs if self.vecs is None else np.concatenate([self.vecs, vecs])
        start = len(self.keys)
        self.keys.extend(keys)
        self.index.update({k: start + i for i, k in enumerate(keys)})
        np.save(self.keys_path, np.array(self.keys, dtype=object))
        np.save(self.vecs_path, self.vecs)

    def get(self, keys: list[str]) -> np.ndarray:
        return np.stack([self.vecs[self.index[k]] for k in keys])


def embed_cached(embedder, keys: list[str], paths: list[str], variant: str, batch_size: int = 16) -> np.ndarray:
    """Embed only what the cache lacks, then return vectors for every requested key."""
    cache = VectorCache(variant)
    todo = cache.missing(keys)
    if todo:
        wanted = dict(zip(keys, paths))
        vecs = embedder.encode([wanted[k] for k in todo], batch_size=batch_size)
        cache.add(todo, vecs)
    return cache.get(keys)
