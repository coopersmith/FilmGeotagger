"""Claude verification: does this film frame actually show the same moment as a candidate?

Embeddings rank candidates by similarity; similarity cannot tell "the same visit" from "the same
place, another day" (PLAN.md risk 2). That judgement needs a model that can reason about
clothing, light, weather and who is present. This stage takes a frame plus its top candidates
and returns a verdict, a confidence, and the clues behind it — the clues then feed the alignment
emissions in M2 and the explanation shown in the review UI.

Two calls per PLAN.md, both here:
  * `verify_frame` — one frame against up to `MAX_CANDIDATES` candidates.
  * `submit_batch` / `collect_batch` — the same prompt through the Batch API at half price, for
    the bulk pass over a whole roll. Interactive re-verification uses the direct call.
"""

from __future__ import annotations

import base64
import io
import json
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from PIL import Image
from pydantic import BaseModel, Field

Image.MAX_IMAGE_PIXELS = None

# PLAN.md M1 chooses the tier by measuring accuracy against cost. Opus 5 is the default; the
# harness sweeps others by passing `model=`.
DEFAULT_MODEL = "claude-opus-5"
MAX_CANDIDATES = 6
IMAGE_EDGE = 768  # candidates are 1024 px derivatives; 768 keeps detail while capping tokens


class Clues(BaseModel):
    """Scene facts readable from the frame alone, independent of any candidate.

    These stay useful even when every candidate is rejected: they score candidate *days* in the
    alignment (a night scene cannot sit at a midday event) and they explain confidence in the UI.
    """

    indoor: bool | None = Field(description="True indoors, False outdoors, null if unclear")
    time_of_day: str | None = Field(description="dawn|morning|midday|afternoon|dusk|night|null")
    weather: str | None = Field(description="clear|overcast|rain|snow|fog|null")
    season: str | None = Field(description="winter|spring|summer|autumn|null")
    signage_text: list[str] = Field(description="Legible text on signs, menus, shopfronts")
    place_guess: str | None = Field(description="Specific place or landmark, null if unsure")
    people_descriptors: list[str] = Field(
        description="Clothing and appearance of people, e.g. 'baby in striped trousers'"
    )


class Verdict(BaseModel):
    match: int | None = Field(
        description="1-based index of the candidate showing the SAME occasion, or null for none"
    )
    confidence: float = Field(description="0.0 to 1.0 confidence in the match decision")
    evidence: str = Field(description="One or two sentences of concrete visual reasoning")
    clues: Clues


SYSTEM = """You compare a scanned film photograph against candidate digital photos taken from \
the same phone library, to decide whether any candidate was taken on the SAME OCCASION as the \
film frame — the same outing, within an hour or so.

The film frame has no date. The candidates do. Your judgement is what turns a visual similarity \
into a timestamp, so a wrong "yes" is worse than a "none": it will place the frame on the wrong \
day and drag its neighbours with it.

Decisive evidence, roughly in order of strength:
- The same people wearing the same clothes.
- The same transient state of a place: food on a table, weather, light direction, a parked car, \
decorations, crowds.
- The same event with matching participants and setting.

NOT evidence of the same occasion:
- The same building, street, room or landmark on its own. People revisit places constantly. \
A recognisable location with different clothing, light or weather is a DIFFERENT day — say none.
- General resemblance of subject matter, palette or composition.

Film and phone images differ heavily in colour, grain and dynamic range. Judge content, not \
rendering; a colour cast is not evidence either way. Film frames are often older-looking than \
they are.

Answering "none" is correct and expected whenever nothing matches — most frames have no \
counterpart. Never guess to be helpful.

If a photo shows people you cannot or should not identify, still describe clothing and setting \
factually and judge the occasion; describing what is visible is the task."""


@dataclass
class CandidateRef:
    """One candidate as presented to the model."""

    uuid: str
    path: str
    local_time: datetime
    place: str | None = None


def _encode(path: str, edge: int = IMAGE_EDGE) -> tuple[str, str] | None:
    try:
        with Image.open(path) as im:
            im = im.convert("RGB")
            im.thumbnail((edge, edge))
            buf = io.BytesIO()
            im.save(buf, format="JPEG", quality=85)
    except (OSError, ValueError):
        return None
    return "image/jpeg", base64.standard_b64encode(buf.getvalue()).decode()


def _image_block(path: str) -> dict | None:
    enc = _encode(path)
    if enc is None:
        return None
    media_type, data = enc
    return {"type": "image", "source": {"type": "base64", "media_type": media_type, "data": data}}


def build_content(frame_path: str, candidates: list[CandidateRef]) -> list[dict] | None:
    """Frame first, then each candidate labelled with its local time and place."""
    frame = _image_block(frame_path)
    if frame is None:
        return None
    content: list[dict] = [{"type": "text", "text": "FILM FRAME (date unknown):"}, frame]
    shown = 0
    for i, c in enumerate(candidates[:MAX_CANDIDATES], 1):
        block = _image_block(c.path)
        if block is None:
            continue
        where = f", {c.place}" if c.place else ""
        content.append(
            {"type": "text", "text": f"CANDIDATE {i} — {c.local_time:%A %-d %B %Y, %H:%M}{where}:"}
        )
        content.append(block)
        shown += 1
    if not shown:
        return None
    content.append(
        {
            "type": "text",
            "text": (
                f"Which candidate, if any, was taken on the same occasion as the film frame? "
                f"Answer with the candidate number (1-{shown}) or null. Then record the clues "
                f"you can read from the film frame itself."
            ),
        }
    )
    return content


def request_params(frame_path: str, candidates: list[CandidateRef], model: str = DEFAULT_MODEL) -> dict | None:
    content = build_content(frame_path, candidates)
    if content is None:
        return None
    return {
        "model": model,
        "max_tokens": 4096,
        "system": SYSTEM,
        "messages": [{"role": "user", "content": content}],
    }


def verify_frame(client, frame_path: str, candidates: list[CandidateRef], model: str = DEFAULT_MODEL) -> Verdict | None:
    """One interactive verification. Returns None if no image could be read."""
    params = request_params(frame_path, candidates, model)
    if params is None:
        return None
    response = client.messages.parse(output_format=Verdict, **params)
    if response.stop_reason == "refusal":
        # A refusal is "no verdict", never "no match" (PLAN.md risk 8) — treating it as a
        # rejection would silently drop the frames most likely to contain people.
        return None
    return response.parsed_output


def submit_batch(client, jobs: list[tuple[str, str, list[CandidateRef]]], model: str = DEFAULT_MODEL):
    """Bulk pass at half price. `jobs` is (custom_id, frame_path, candidates)."""
    from anthropic.types.messages.batch_create_params import Request

    requests = []
    for custom_id, frame_path, cands in jobs:
        params = request_params(frame_path, cands, model)
        if params is None:
            continue
        params["output_config"] = {"format": _verdict_schema()}
        requests.append(Request(custom_id=custom_id, params=params))
    return client.messages.batches.create(requests=requests)


def collect_batch(client, batch_id: str) -> dict[str, Verdict]:
    """Results arrive in any order, so key by custom_id — never by position."""
    out: dict[str, Verdict] = {}
    for entry in client.messages.batches.results(batch_id):
        if entry.result.type != "succeeded":
            continue
        message = entry.result.message
        if message.stop_reason == "refusal":
            continue
        text = next((b.text for b in message.content if b.type == "text"), None)
        if text:
            out[entry.custom_id] = Verdict.model_validate(json.loads(text))
    return out


def _verdict_schema() -> dict:
    """Batch requests take a raw schema rather than the Pydantic helper."""
    return {"type": "json_schema", "schema": Verdict.model_json_schema()}
