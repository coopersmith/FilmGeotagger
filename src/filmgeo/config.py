"""Paths and tunables shared across the engine."""

from __future__ import annotations

import os
from pathlib import Path

# Everything derived (metadata cache, embedding cache, eval output) lives here. Gitignored.
DATA_DIR = Path(os.environ.get("FILMGEO_DATA", Path(__file__).resolve().parents[2] / ".filmgeo"))

# A frame and a phone photo belong to the same moment if they are this close in time. Used to
# decide whether a frame *has* a phone counterpart at all, and to score a retrieved candidate.
# The hand-tagged ground truth is group-level, not per-second, so this cannot be tight.
SAME_MOMENT = 30 * 60  # seconds

# Event segmentation (PLAN.md): a new event starts on a big enough gap or move.
EVENT_GAP_SECONDS = 45 * 60
EVENT_MOVE_METRES = 500

# Retrieval.
# SigLIP alone, measured in M1 on 113 hand-anchored frames: it beat DINOv2 and both fusion
# methods at every K (91.2% recall@8 vs 90.3 RRF / 89.4 z-fused / 85.8 DINOv2), and DINOv2 is
# 2-4x slower to embed. DINOv2 stays available behind `--variants` for the cases PLAN expects it
# to help with; nothing measured so far needs it.
DEFAULT_VARIANTS = ("siglip",)
# K and the per-event cap: on the honest ground truth (COO-146) occasion-level recall is 74%
# at K=12 / cap 1 against 63% at K=8 / cap 3, and re-verifying two rolls at K=12 / cap 1 cut
# wrong-day accepts from 6/28 to 3/28 for $2.52 a roll instead of $1.68. SigLIP finds the
# scene, not the shot, so a second photo from the same event is a wasted slot.
TOP_K = 12
MAX_PER_EVENT = 1

# The user's camera bodies (CLAUDE.md). Roll facts warn on any other name rather than refuse,
# because a new body is not an error — but a typo would split a keyword in the library.
KNOWN_CAMERAS = ("Contax T2", "Leica M7", "Mamiya 7II")

# Machine provenance keywords (`filmgeo:anchored`, `filmgeo:conf:high`, ...) in the scan files.
# Off by default since the first end-to-end roll (COO-131): they showed up in Apple Photos'
# keyword list beside the user's own, and the same facts live in `<roll>/filmgeo.json`.
# `filmgeo write --provenance` or FILMGEO_PROVENANCE=1 writes them.
PROVENANCE_KEYWORDS = os.environ.get("FILMGEO_PROVENANCE", "0") in ("1", "true", "yes")

# The alignment engine. "v2" (COO-177) reads the camera roll as an index of *places* first —
# nearest photos in the window and in the all-time atlas vote for where a frame was, names
# read off the frame are looked up, recorded visits join the timeline — and the monotone
# solve then decides when. "v1" is the first engine: similarity to one event's best photo,
# and a location only where verification anchored the frame. Kept for comparison.
ENGINE = os.environ.get("FILMGEO_ENGINE", "v2")

# The all-time place atlas (`filmgeo embed --atlas`): photos from every place in the library as
# extra voters for *where*. Measured OFF (docs/v2-findings.md): every place the reviewed rolls
# missed had library photos from other years, but the frames that lack a photo in the window
# are the generic ones — water, ferns, a sign — and SigLIP ties those to a look-alike 60 km
# away as readily as to the place; at every reach the score was unchanged or worse.
ATLAS = os.environ.get("FILMGEO_ATLAS", "0") in ("1", "true", "yes")
