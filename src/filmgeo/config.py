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

# Retrieval
TOP_K = 8
MAX_PER_EVENT = 3
