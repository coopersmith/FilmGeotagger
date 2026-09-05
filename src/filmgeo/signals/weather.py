"""Historical weather as a consistency check on Claude's weather clue (COO-136).

Claude reads `weather` off a frame — `clear | overcast | rain | snow | fog` — for the outdoor
ones (27 of the 104 verdicts on the verified rolls). Open-Meteo's archive gives the hourly WMO
weather code, cloud cover and precipitation for any point and day since 1940, free and without
a key. So for every phone-photo event with a centroid, the weather at its place and hour is
known, and an event whose sky contradicts the frame's clue is a worse home for that frame —
the same small emission term `time_of_day` already carries.

Coarse on purpose. Observed weather collapses to `clear`, `overcast`, `rain`, `snow` or `mixed`
(partly cloudy), and only a clear contradiction costs anything: a `clear` clue against rain or
overcast, `overcast` against clear skies, `rain`/`snow` against a dry hour. `mixed` and `fog`
never contradict. The archive is 10 km reanalysis, and a film frame's sky is a small patch of
it; the penalty is a nudge (`weather_penalty`), not evidence.

One request per (0.1° cell, day), cached under `.filmgeo/weather/` forever — weather in the
past does not change. Fetching happens only with `FILMGEO_WEATHER=1`; a network failure is
quiet (the roll solves as before); tests inject a fetcher.

**Measured and turned off** (docs/m5-findings.md): at the hand-tagged time and place of the 25
outdoor frames with a clue, Claude and the archive agree on 14, are compatible on 3 and
contradict on 8 — frames 16-19 of `00007037`, one walk read as "clear" that the reanalysis has
at overcast. With any penalty the 22-day roll gets worse (right day 29 → 25 of 37,
interpolated error 1.5 h → 8.3 h). `AlignParams.weather_penalty` defaults to 0.
"""

from __future__ import annotations

import json
import os
import urllib.parse
import urllib.request
from collections.abc import Callable
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

from filmgeo.config import DATA_DIR

WEATHER_DIR = DATA_DIR / "weather"
API = "https://archive-api.open-meteo.com/v1/archive"
CELL = 0.1          # degrees; ~10 km, the archive's own resolution
TIMEOUT = 10        # seconds per request

Fetcher = Callable[[float, float, date], dict | None]


def enabled() -> bool:
    """Off unless `FILMGEO_WEATHER=1`: the penalty measured harmful (see AlignParams.weather_penalty)."""
    return os.environ.get("FILMGEO_WEATHER", "0") in ("1", "true", "yes")


def classify(code: int | None, cloud: float | None, precip: float | None) -> str | None:
    """WMO code + cloud cover + precipitation -> clear | mixed | overcast | rain | snow | fog | None."""
    if code is None and cloud is None:
        return None
    c = int(code) if code is not None else -1
    if c in (45, 48):
        return "fog"
    if 71 <= c <= 77 or c in (85, 86):
        return "snow"
    if 51 <= c <= 67 or 80 <= c <= 82 or c >= 95 or (precip is not None and precip >= 0.5):
        return "rain"
    if c == 3 or (cloud is not None and cloud >= 80):
        return "overcast"
    if c in (0, 1) or (cloud is not None and cloud <= 30):
        return "clear"
    return "mixed"


def normalise_clue(text: str | None) -> str | None:
    """Claude's free-ish text -> the same vocabulary."""
    if not text:
        return None
    t = text.strip().lower()
    for key, words in (("rain", ("rain", "drizzle", "shower", "storm", "wet")), ("snow", ("snow", "sleet", "hail")),
                       ("fog", ("fog", "mist", "haze")), ("overcast", ("overcast", "cloud", "grey", "gray", "dull")),
                       ("clear", ("clear", "sun", "blue sky", "bright"))):
        if any(w in t for w in words):
            return key
    return None


def contradicts(clue: str | None, observed: str | None) -> bool:
    """Only a plain disagreement counts; `mixed` and `fog` skies never do."""
    if clue is None or observed is None or observed in ("mixed", "fog") or clue == "fog":
        return False
    if clue == observed:
        return False
    if clue == "clear":
        return observed in ("overcast", "rain", "snow")
    if clue == "overcast":
        return observed in ("clear",)
    if clue in ("rain", "snow"):
        return observed in ("clear", "overcast")
    return False


def _cell(lat: float, lon: float) -> tuple[float, float]:
    return round(round(lat / CELL) * CELL, 1), round(round(lon / CELL) * CELL, 1)


def fetch_day(lat: float, lon: float, day: date) -> dict | None:
    """Open-Meteo's hourly archive for one cell and day, as returned. None on any failure."""
    q = urllib.parse.urlencode({"latitude": lat, "longitude": lon, "start_date": day.isoformat(), "end_date": day.isoformat(),
                                "hourly": "weather_code,cloud_cover,precipitation", "timezone": "UTC"})
    try:
        with urllib.request.urlopen(f"{API}?{q}", timeout=TIMEOUT) as r:
            return json.loads(r.read().decode())
    except Exception:
        return None


class Weather:
    """Hourly observed weather, by cell and UTC day, cached on disk."""

    def __init__(self, directory: Path = WEATHER_DIR, fetcher: Fetcher = fetch_day):
        self.directory = Path(directory)
        self.fetcher = fetcher
        self._mem: dict[tuple[float, float, date], dict | None] = {}
        self.failed = 0

    def day(self, lat: float, lon: float, day: date) -> dict | None:
        clat, clon = _cell(lat, lon)
        key = (clat, clon, day)
        if key in self._mem:
            return self._mem[key]
        p = self.directory / f"{clat:.1f}_{clon:.1f}_{day.isoformat()}.json"
        if p.exists():
            data = json.loads(p.read_text())
        else:
            data = self.fetcher(clat, clon, day)
            if data is None:
                self.failed += 1
            else:
                p.parent.mkdir(parents=True, exist_ok=True)
                p.write_text(json.dumps(data))
        self._mem[key] = data
        return data

    def at(self, lat: float, lon: float, t: datetime) -> str | None:
        """Observed weather class at an instant, from the hour containing it."""
        u = t.astimezone(timezone.utc)
        data = self.day(lat, lon, u.date())
        if not data:
            return None
        h = data.get("hourly") or {}
        times = h.get("time") or []
        stamp = u.strftime("%Y-%m-%dT%H:00")
        if stamp not in times:
            return None
        i = times.index(stamp)
        pick = lambda k: (h.get(k) or [None] * len(times))[i]
        return classify(pick("weather_code"), pick("cloud_cover"), pick("precipitation"))

    def for_events(self, events: list) -> dict[int, str]:
        """Observed weather per event, at its centroid and midpoint. Events without a place are skipped."""
        out: dict[int, str] = {}
        for e in events:
            if e.lat is None or e.lon is None:
                continue
            mid = e.start + (e.end - e.start) / 2
            w = self.at(e.lat, e.lon, mid)
            if w:
                out[e.index] = w
        return out
