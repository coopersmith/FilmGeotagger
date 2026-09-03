#!/usr/bin/env python3
"""M0: build a 16-bit RGB TIFF fixture from a scan, so the write path can be tested on one.

Usage:
    uv run --with numpy --with tifffile python scripts/m0_make_tiff_fixture.py scan.jpg [out.tif]

The lab currently delivers JPG only, but PLAN.md wants the exiftool write path verified
against a 16-bit TIFF before M4 trusts it. This promotes a scan to full-resolution 16-bit
RGB (8-bit values scaled by 257 to span the range) and writes a deflate-compressed TIFF.
Pillow cannot write 16-bit RGB, hence tifffile.
"""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import tifffile
from PIL import Image

Image.MAX_IMAGE_PIXELS = None  # scans are ~28 MP, well past Pillow's decompression-bomb guard


def main(argv: list[str]) -> int:
    if not argv:
        print(__doc__)
        return 2
    src = Path(argv[0])
    dst = Path(argv[1]) if len(argv) > 1 else src.with_name(f"{src.stem}_16bit.tif")
    with Image.open(src) as im:
        rgb = np.asarray(im.convert("RGB"), dtype=np.uint16) * 257
    tifffile.imwrite(dst, rgb, photometric="rgb", compression="deflate")
    print(f"{dst}  {rgb.shape[1]}x{rgb.shape[0]}  16-bit  {dst.stat().st_size / 1e6:.0f} MB")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
