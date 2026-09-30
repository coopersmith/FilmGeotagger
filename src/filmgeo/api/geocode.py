"""Place names to coordinates, for the map's search box.

OpenStreetMap's Nominatim, key-free, one request a second, identified by a User-Agent as its
policy asks. The query is the only thing sent. Results are cached for the life of the process,
and the fetcher is injectable so the tests never touch the network.
"""

from __future__ import annotations

import json
import threading
import time
import urllib.parse
import urllib.request
from typing import Callable

NOMINATIM = "https://nominatim.openstreetmap.org/search"
USER_AGENT = "filmgeo/0.1 (local film-scan review tool)"
Fetcher = Callable[[str], list[dict]]


def _fetch(q: str) -> list[dict]:
    url = f"{NOMINATIM}?{urllib.parse.urlencode({'q': q, 'format': 'jsonv2', 'limit': 8, 'addressdetails': 0})}"
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, "Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=8) as r:
        return json.loads(r.read().decode())


class Geocoder:
    def __init__(self, fetcher: Fetcher = _fetch, min_interval: float = 1.0):
        self.fetcher = fetcher
        self.min_interval = min_interval
        self._cache: dict[str, list[dict]] = {}
        self._last = 0.0
        self._lock = threading.Lock()

    def search(self, q: str) -> list[dict]:
        key = " ".join(q.lower().split())
        if key in self._cache:
            return self._cache[key]
        with self._lock:
            wait = self.min_interval - (time.monotonic() - self._last)
            if wait > 0:
                time.sleep(wait)
            raw = self.fetcher(q)
            self._last = time.monotonic()
        out = [{"name": r.get("display_name", ""), "lat": float(r["lat"]), "lon": float(r["lon"]),
                "kind": r.get("type") or r.get("category") or ""} for r in raw if "lat" in r and "lon" in r]
        self._cache[key] = out
        return out
