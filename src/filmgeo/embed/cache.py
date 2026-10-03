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
        # Write beside, then rename: a reader (the review server, an eval script) never sees a
        # half-written array, and keys and vectors cannot be left at different lengths for long.
        tmp_k, tmp_v = self.dir / "keys.tmp.npy", self.dir / "vecs.tmp.npy"
        np.save(tmp_v, self.vecs)
        np.save(tmp_k, np.array(self.keys, dtype=object))
        tmp_v.replace(self.vecs_path)
        tmp_k.replace(self.keys_path)

    def get(self, keys: list[str]) -> np.ndarray:
        return np.stack([self.vecs[self.index[k]] for k in keys])


def embed_cached(embedder, keys: list[str], paths: list[str], variant: str, batch_size: int = 16,
                 chunk: int = 512, progress=None) -> np.ndarray:
    """Embed only what the cache lacks, then return vectors for every requested key.

    Saved every `chunk` images, so a long run (the place atlas is tens of thousands of photos)
    that is interrupted keeps what it did. `progress(done, total)` is called after each chunk.
    """
    cache = VectorCache(variant)
    todo = cache.missing(keys)
    if todo:
        wanted = dict(zip(keys, paths))
        for i in range(0, len(todo), chunk):
            part = todo[i : i + chunk]
            vecs = embedder.encode([wanted[k] for k in part], batch_size=batch_size)
            if cache.vecs is not None and vecs.shape[1] != cache.vecs.shape[1]:
                # Not one image of the chunk could be read (`encode` then has no dimension to
                # report): cache them as zero vectors, which match nothing, and carry on.
                vecs = np.zeros((len(part), cache.vecs.shape[1]), dtype=cache.vecs.dtype)
            cache.add(part, vecs)
            if progress:
                progress(min(i + chunk, len(todo)), len(todo))
    return cache.get(keys)
