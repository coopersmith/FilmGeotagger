#!/usr/bin/env python3
"""Embed every candidate photo in a window so later runs are cache hits.

    uv run --extra embed python scripts/embed_window.py 2026-05-01 2026-05-31 [--variant siglip]

Slow (GPU minutes per thousand photos); run it in the background.
"""

from __future__ import annotations

import argparse
import time
from datetime import datetime

from filmgeo.embed.cache import VectorCache, embed_cached
from filmgeo.photos import library


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("start")
    ap.add_argument("end")
    ap.add_argument("--variant", default="siglip")
    args = ap.parse_args()
    assets = library.load()
    lo = datetime.fromisoformat(args.start).astimezone()
    hi = datetime.fromisoformat(args.end).astimezone()
    pool = library.candidates(assets, lo, hi)
    missing = VectorCache(args.variant).missing([a.uuid for a in pool])
    print(f"{len(pool)} photos in window, {len(missing)} not yet embedded", flush=True)
    if not missing:
        return 0
    from filmgeo.embed import models

    embedder = getattr(models, {"siglip": "SigLIP", "dinov2": "DINOv2"}[args.variant])()
    t0 = time.time()
    by_uuid = {a.uuid: a.derivative for a in pool}
    embed_cached(embedder, missing, [by_uuid[u] for u in missing], args.variant)
    print(f"embedded {len(missing)} in {time.time() - t0:.0f}s", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
