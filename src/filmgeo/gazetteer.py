"""Names to coordinates: where is "Young Family Farm"? (COO-177)

A frame often says where it was taken — a farm stand's sign, a drive-in's, a restaurant's — and
the verifier reads it (`clues.signage_text`, `clues.place_guess`). A gazetteer turns the name
into a point; the engine then keeps it only if it is somewhere the trail could reach.

Backends, first that answers:

* **MapKit local search** on macOS (`pyobjc-framework-MapKit`): Apple's points of interest,
  biased to a region. It knows the small businesses OpenStreetMap does not — measured on the
  reviewed rolls, three signs of three within 160 m, where Nominatim found none.
* **Nominatim** (`api/geocode.py`) otherwise: towns, landmarks, some businesses.

Only the name and a rough region leave the Mac. Every answer is cached in
`.filmgeo/gazetteer.json`, so a re-solve never asks twice and the tests never ask at all.
"""

from __future__ import annotations

import json
import re
import time
import unicodedata
from dataclasses import asdict, dataclass
from pathlib import Path

from filmgeo.config import DATA_DIR
from filmgeo.events import haversine_m

CACHE = DATA_DIR / "gazetteer.json"
REGION_CELL_DEG = 1.0            # queries are cached per name and per ~100 km region


@dataclass
class Hit:
    name: str
    lat: float
    lon: float
    locality: str | None = None
    source: str = "mapkit"


def norm_tokens(s: str) -> list[str]:
    s = unicodedata.normalize("NFKD", s).encode("ascii", "ignore").decode().lower()
    return [t for t in re.split(r"[^a-z0-9]+", s) if len(t) > 1 and t not in STOP]


STOP = {"the", "of", "and", "di", "de", "del", "della", "la", "le", "il", "in", "at", "est", "snc", "inc", "llc", "co"}


def name_match(query: str, name: str) -> float:
    """How far the hit's name *is* the query: the smaller of the two coverages, on at least two shared words.

    Both ways, because a search engine answers anything: "Tuscan vineyard" finds "Apartment
    among Tuscan vineyard hill" (every word of the query, two of the hit's five), and a slogan
    on a sign finds a business with the slogan in its name.
    """
    q, n = set(norm_tokens(query)), set(norm_tokens(name))
    shared = q & n
    if len(shared) < 2:
        return 0.0
    return min(len(shared) / len(q), len(shared) / len(n))


def _mapkit(query: str, near: tuple[float, float], span_deg: float = 1.5, timeout: float = 10.0) -> list[Hit] | None:
    try:
        import CoreLocation
        import MapKit
        from Foundation import NSDate, NSRunLoop
    except ImportError:
        return None
    req = MapKit.MKLocalSearchRequest.alloc().init()
    req.setNaturalLanguageQuery_(query)
    req.setRegion_(MapKit.MKCoordinateRegionMake(CoreLocation.CLLocationCoordinate2D(near[0], near[1]),
                                                 MapKit.MKCoordinateSpanMake(span_deg, span_deg)))
    search = MapKit.MKLocalSearch.alloc().initWithRequest_(req)
    out: dict = {}
    search.startWithCompletionHandler_(lambda resp, err: out.update(r=resp, e=err))
    t0 = time.time()
    while "r" not in out and time.time() - t0 < timeout:
        NSRunLoop.currentRunLoop().runUntilDate_(NSDate.dateWithTimeIntervalSinceNow_(0.05))
    if "r" not in out:
        return None                                   # no answer: do not cache a silence as "nothing there"
    if out.get("r") is None:
        err = out.get("e")
        # Code 4 is "nothing found"; anything else (throttled, offline) is not an answer.
        return [] if err is not None and int(err.code()) == 4 else None
    hits = []
    for it in out["r"].mapItems():
        c = it.placemark().coordinate()
        hits.append(Hit(str(it.name() or ""), float(c.latitude), float(c.longitude),
                        str(it.placemark().locality()) if it.placemark().locality() else None))
    return hits


def _nominatim(query: str, near: tuple[float, float]) -> list[Hit] | None:
    from filmgeo.api.geocode import Geocoder

    try:
        rows = _NOMINATIM.search(query) if (_NOMINATIM := globals().setdefault("_NOMINATIM", Geocoder())) else []
    except OSError:
        return None
    return [Hit(r["name"].split(",")[0], r["lat"], r["lon"], None, "nominatim") for r in rows]


class Gazetteer:
    """Cached lookups. `backend(query, near) -> list[Hit] | None` (None = could not ask)."""

    def __init__(self, path: Path = CACHE, backend=None, min_interval: float = 1.3):
        self.path = path
        self.backend = backend
        self.min_interval = min_interval
        self._last = 0.0
        self.cache: dict[str, list[dict]] = json.loads(path.read_text()) if path.exists() else {}

    @staticmethod
    def _key(query: str, near: tuple[float, float]) -> str:
        return f"{' '.join(norm_tokens(query))}@{round(near[0] / REGION_CELL_DEG)},{round(near[1] / REGION_CELL_DEG)}"

    def lookup(self, query: str, near: tuple[float, float], offline: bool = False) -> list[Hit]:
        key = self._key(query, near)
        if key in self.cache:
            return [Hit(**h) for h in self.cache[key]]
        if offline:
            return []
        wait = self.min_interval - (time.time() - self._last)
        if wait > 0:
            time.sleep(wait)                           # MapKit throttles at about fifty a minute
        ask = self.backend or (lambda q, n: (r if (r := _mapkit(q, n)) is not None else _nominatim(q, n)))
        hits = ask(query, near)
        self._last = time.time()
        if hits is None:
            return []
        self.cache[key] = [asdict(h) for h in hits[:5]]
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps(self.cache, indent=1))
        return hits[:5]

    def find(self, query: str, near: tuple[float, float], reach_m: float, min_match: float = 0.6, offline: bool = False,
             name: str | None = None) -> Hit | None:
        """The best hit whose name really is `name` (default: the query) and which lies within reach of `near`."""
        best, best_score = None, 0.0
        for h in self.lookup(query, near, offline=offline):
            m = name_match(name or query, h.name)
            if m >= min_match and haversine_m(near, (h.lat, h.lon)) <= reach_m and m > best_score:
                best, best_score = h, m
        return best
