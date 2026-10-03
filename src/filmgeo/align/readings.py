"""Places read off the frame itself (COO-177).

A frame of a farm stand's sign has no phone photo to match and needs none: the verifier
already wrote down "YOUNG FAMILY FARM" and "Young Family Farm, Little Compton, Rhode Island"
(`clues.signage_text`, `clues.place_guess`), and until now nothing used either. Each is looked
up in the gazetteer near the regions the window's trail covers, and a hit whose *name really
is the text* and which the trail could have reached becomes a `Reading`: a place hypothesis
for that frame, independent of the camera roll.

Signs are trusted more than guesses: a sign is text in the picture, a guess is the model's
recognition of a landmark. A guess that names only a town (the hit is a locality, not a
place) is not a reading — a town is where the trail already is.
"""

from __future__ import annotations

import collections
from dataclasses import dataclass

from filmgeo.events import haversine_m
from filmgeo.gazetteer import Gazetteer, Hit, norm_tokens
from filmgeo.photos.library import Asset

SIGN_CONFIDENCE = 0.9
GUESS_CONFIDENCE = 0.6
REACH_M = 80_000.0


@dataclass
class Reading:
    frame: int               # 0-based
    kind: str                # sign | guess
    text: str                # what was read
    name: str                # what the gazetteer calls it
    lat: float
    lon: float
    confidence: float


def regions(pool: list[Asset], limit: int = 4) -> list[tuple[float, float]]:
    """The window's distinct regions (about 100 km apart), busiest first: where to ask."""
    located = [(a.lat, a.lon) for a in pool if a.lat is not None]
    cells = collections.Counter((round(la), round(lo)) for la, lo in located)
    out = []
    for (la, lo), _ in cells.most_common(limit):
        pts = sorted(p for p in located if round(p[0]) == la and round(p[1]) == lo)
        lats, lons = sorted(p[0] for p in pts), sorted(p[1] for p in pts)
        out.append((lats[len(lats) // 2], lons[len(lons) // 2]))
    return out


def queries(clues: dict) -> list[tuple[str, str, str]]:
    """(kind, what to ask, the name the answer must carry), signs first.

    One-word signs ("VEGETABLES") and one-word guesses ("bedroom") are not names. A guess is
    asked whole — "Santa Croce church, Piazza Matteotti, Greve in Chianti" — so the town steers
    the search, but the answer must be called what the guess's first part says: asked by its
    first words alone, that church came back as the basilica of the same name 21 km away.
    """
    lines = [line for line in clues.get("signage_text") or [] if line and line.strip()]
    out = [("sign", line, line) for line in lines if len(norm_tokens(line)) >= 2]
    # A name is often set over two lines ("RISTORANTE" / "La Martellina"): ask for neighbours joined.
    out += [("sign", f"{a} {b}", f"{a} {b}") for a, b in zip(lines, lines[1:])
            if len(norm_tokens(a)) < 2 or len(norm_tokens(b)) < 2 if len(norm_tokens(f"{a} {b}")) >= 2]
    guess = clues.get("place_guess")
    if guess:
        name = guess.split(",")[0].split("(")[0].strip()
        if len(norm_tokens(name)) >= 2:
            out.append(("guess", guess, name))
    return out


def from_verdicts(verdicts: dict, pool: list[Asset], gazetteer: Gazetteer | None = None, offline: bool = False,
                  reach_m: float = REACH_M) -> list[Reading]:
    """One reading per frame at most. Of everything the gazetteer can place within reach: a
    place both a sign and the guess name, else a sign's, else the guess's; ties go to the one
    nearest the trail. A product line on a sign ("Fresh Cut Flowers") finds a shop of that
    name somewhere; the farm whose sign it is, is the one the guess names too."""
    import numpy as np

    from filmgeo.align.evidence import haversine_many

    gz = gazetteer or Gazetteer()
    where = regions(pool)
    lats = np.array([a.lat for a in pool if a.lat is not None])
    lons = np.array([a.lon for a in pool if a.lat is not None])
    out: list[Reading] = []
    for n, v in sorted(verdicts.items()):
        found: list[tuple[str, str, Hit, float]] = []           # kind, text, hit, metres from the trail
        for kind, ask, name in queries(getattr(v, "clues", None) or {}):
            for near in where:
                hit: Hit | None = gz.find(ask, near, reach_m, name=name, offline=offline)
                if hit is None:
                    continue
                d = float(haversine_many(hit.lat, hit.lon, lats, lons).min()) if len(lats) else 0.0
                if d <= reach_m:
                    found.append((kind, name, hit, d))
        if not found:
            continue

        def rank(f: tuple[str, str, Hit, float]) -> tuple:
            kind, _, hit, d = f
            kinds = {k for k, _, h, _ in found if haversine_m((h.lat, h.lon), (hit.lat, hit.lon)) <= 200}
            return (len(kinds), kind == "sign", -d)            # sign and guess agreeing, then a sign, then the nearer

        kind, text, hit, _ = max(found, key=rank)
        agreed = rank((kind, text, hit, 0.0))[0] == 2
        conf = SIGN_CONFIDENCE if (kind == "sign" or agreed) else GUESS_CONFIDENCE
        out.append(Reading(n - 1, "sign" if agreed else kind, text, hit.name, hit.lat, hit.lon, conf))
    return out
