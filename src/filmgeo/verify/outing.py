"""Roll-level outing pass (COO-119): one Claude call groups a roll's frames into outings.

Frames sharing clothing, light, weather and scene continuity were shot on the same outing.
Knowing that lets the alignment map a few outings onto days instead of 36 frames onto weeks,
and gives consecutive frames in one group a "stay together" bonus in the solver
(`AlignParams.outing_bonus`). It is also the tool against the domestic-repetition failure
(docs/m1-findings.md): a wrong-day accept that splits an outing contradicts the group.

One call per roll: a tiled contact sheet with frame numbers burnt in, plus a text summary of
the window's phone-photo events so the model knows what days exist. The answer is a list of
groups (frame numbers, a one-line description, confidence) and any frames that look out of
sequence. Stored under `.filmgeo/outings/<roll>.json`; the pipeline turns each group into
same-outing pairs for consecutive frames.
"""

from __future__ import annotations

import json
import math
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path

from PIL import Image, ImageDraw
from pydantic import BaseModel, Field

from filmgeo.config import DATA_DIR

OUTINGS_DIR = DATA_DIR / "outings"
TILE = 320          # px per tile edge on the sheet
COLUMNS = 6
DEFAULT_MODEL = "claude-opus-5"


class OutingGroup(BaseModel):
    frames: list[int] = Field(description="Frame numbers in this outing, in scan order")
    description: str = Field(description="What ties them: people, clothing, place, light, weather")
    confidence: float = Field(description="0.0 to 1.0 that these frames are one outing")


class OutingAnswer(BaseModel):
    groups: list[OutingGroup] = Field(description="Every frame belongs to exactly one group; a lone frame is a group of one")
    out_of_sequence: list[int] = Field(description="Frame numbers that appear to belong elsewhere in the roll's order")
    notes: str = Field(description="One or two sentences on anything the alignment should know")


SYSTEM = """You look at a contact sheet of one roll of film, frames numbered in the order they \
were shot, and group the frames into OUTINGS: stretches shot on the same occasion.

The same outing means the same day and the same excursion — the same people in the same \
clothes, the same light and weather, a continuous place or a journey between places. A change \
of clothes on the same person, a change of season or weather, or an obviously different day \
starts a new outing. Indoor frames of the same home on different days are different outings \
if anything visible changes (clothing, decorations, light, what is on the table).

Frames are in scan order, which is shooting order, so an outing is normally a contiguous run. \
Say when it is not: a frame that looks like it belongs with an earlier or later group is \
"out of sequence" and worth flagging rather than forcing.

Be conservative about merging: two outings wrongly joined will misplace every frame in one of \
them; two outings wrongly split cost nothing. Every frame belongs to exactly one group. \
Describe each group concretely — who, what they wear, where, what the light is like — because \
the description is shown to the photographer to confirm.

The events list tells you which days the phone library has photos for inside the window. \
Do not assign dates; just group."""


def contact_sheet(paths: list[str | None], numbers: list[int], out: Path, tile: int = TILE, columns: int = COLUMNS) -> Path:
    """Tile the frames with their numbers burnt into a corner. Missing images become grey tiles."""
    rows = math.ceil(len(paths) / columns)
    sheet = Image.new("RGB", (columns * tile, rows * tile), (24, 24, 24))
    draw = ImageDraw.Draw(sheet)
    for i, (p, n) in enumerate(zip(paths, numbers)):
        x, y = (i % columns) * tile, (i // columns) * tile
        try:
            with Image.open(p) as im:
                im = im.convert("RGB")
                im.thumbnail((tile - 8, tile - 8))
                sheet.paste(im, (x + 4 + (tile - 8 - im.width) // 2, y + 4 + (tile - 8 - im.height) // 2))
        except (OSError, ValueError, TypeError):
            draw.rectangle([x + 4, y + 4, x + tile - 4, y + tile - 4], fill=(60, 60, 60))
        label = f"#{n}"
        draw.rectangle([x + 6, y + 6, x + 6 + 12 * len(label) + 10, y + 34], fill=(0, 0, 0))
        draw.text((x + 11, y + 10), label, fill=(255, 220, 0))
    out.parent.mkdir(parents=True, exist_ok=True)
    sheet.save(out, quality=85)
    return out


def events_summary(events: list, limit: int = 40) -> str:
    """Days the phone library covers inside the window, as one line each."""
    by_day: dict[str, int] = {}
    for e in events:
        by_day[e.start.strftime("%a %-d %b")] = by_day.get(e.start.strftime("%a %-d %b"), 0) + e.count
    lines = [f"{d}: {n} phone photos" for d, n in list(by_day.items())[:limit]]
    if len(by_day) > limit:
        lines.append(f"... and {len(by_day) - limit} more days")
    return "\n".join(lines) if lines else "no phone photos in the window"


def request_params(sheet_path: Path, n_frames: int, events_text: str, camera: str | None = None, model: str = DEFAULT_MODEL) -> dict:
    import base64

    data = base64.standard_b64encode(sheet_path.read_bytes()).decode()
    intro = (f"Contact sheet of a {n_frames}-frame roll" + (f" shot on a {camera}" if camera else "")
             + ", frames numbered #1..#" + str(n_frames) + " in shooting order, left to right then top to bottom.\n\n"
             f"Phone-photo days inside the window:\n{events_text}\n\n"
             "Group the frames into outings and flag anything out of sequence.")
    return {
        "model": model,
        "max_tokens": 4096,
        "system": SYSTEM,
        "messages": [{"role": "user", "content": [
            {"type": "text", "text": intro},
            {"type": "image", "source": {"type": "base64", "media_type": "image/jpeg", "data": data}},
        ]}],
    }


def ask(client, sheet_path: Path, n_frames: int, events_text: str, camera: str | None = None, model: str = DEFAULT_MODEL) -> OutingAnswer | None:
    response = client.messages.parse(output_format=OutingAnswer, **request_params(sheet_path, n_frames, events_text, camera, model))
    if response.stop_reason == "refusal":
        return None
    return response.parsed_output


# --- store and consumption -------------------------------------------------------------


@dataclass
class Outings:
    roll: str
    groups: list[dict]                 # OutingGroup dicts
    out_of_sequence: list[int] = field(default_factory=list)
    notes: str = ""
    model: str | None = None
    asked_at: str | None = None

    @staticmethod
    def path_for(roll: str, directory: Path = OUTINGS_DIR) -> Path:
        return directory / f"{roll}.json"

    @classmethod
    def load(cls, roll: str, directory: Path = OUTINGS_DIR) -> "Outings | None":
        p = cls.path_for(roll, directory)
        return cls(**json.loads(p.read_text())) if p.exists() else None

    def save(self, directory: Path = OUTINGS_DIR) -> Path:
        p = self.path_for(self.roll, directory)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps(asdict(self), indent=1) + "\n")
        return p

    @classmethod
    def from_answer(cls, roll: str, answer: OutingAnswer, model: str) -> "Outings":
        return cls(roll, [g.model_dump() for g in answer.groups], answer.out_of_sequence, answer.notes,
                   model, datetime.now().astimezone().isoformat(timespec="seconds"))

    def same_outing_pairs(self, n_frames: int, min_confidence: float = 0.6) -> set[tuple[int, int]]:
        """Consecutive 0-based frame pairs inside one group — what `RollInputs.same_outing` takes.

        Only adjacent frames pair, because the solver's bonus is on transitions; a group of
        frames 3-7 yields (2,3), (3,4), (4,5), (5,6). Out-of-sequence frames never pair.
        """
        skip = set(self.out_of_sequence)
        pairs: set[tuple[int, int]] = set()
        for g in self.groups:
            if g.get("confidence", 1.0) < min_confidence:
                continue
            frames = sorted(n for n in g["frames"] if 1 <= n <= n_frames and n not in skip)
            for a, b in zip(frames, frames[1:]):
                if b == a + 1:
                    pairs.add((a - 1, b - 1))
        return pairs
