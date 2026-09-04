"""Thumbnails for scan frames and phone photos, cached by content key.

Keys are what the vector cache already uses — a scan's content hash, a photo's uuid — so a
thumbnail made for one roll serves every roll that shows the same photo, and survives a
re-run. Two sizes: `small` for strips, `large` for the frame beside its photo (Photos
derivatives top out at 1024 px under Optimize Mac Storage, so larger buys nothing).

Reading a Photos derivative needs Full Disk Access; from a sandboxed shell it fails with
`PermissionError`, which the API turns into a 403 that says to run `filmgeo serve` from
Terminal.app rather than a bare 500.
"""

from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageOps

Image.MAX_IMAGE_PIXELS = None

SIZES = {"small": 240, "large": 1024}


def thumbnail(src: str | Path, key: str, size: str, cache_dir: Path) -> Path:
    """Path of the cached JPEG for `src`, making it if needed. Raises the file's own OSError."""
    px = SIZES[size]
    dst = cache_dir / size / f"{key}.jpg"
    if dst.exists():
        return dst
    with Image.open(src) as im:
        im = ImageOps.exif_transpose(im)
        im = im.convert("RGB")
        im.thumbnail((px, px))
        dst.parent.mkdir(parents=True, exist_ok=True)
        tmp = dst.with_suffix(".part")
        im.save(tmp, format="JPEG", quality=85)
        tmp.replace(dst)
    return dst
