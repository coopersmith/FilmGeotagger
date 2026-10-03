"""What changes for the frames the user has not confirmed yet: the saved (v1) proposal beside v2's. Nothing is saved."""
import json
from datetime import datetime
from common import origin_of
from filmgeo.align import pipeline
from filmgeo.config import DATA_DIR
from filmgeo.events import haversine_m
from filmgeo.photos import library
assets = library.load()
tot = dict(n=0, v1_placed=0, v2_placed=0, v2_sure=0)
for key in ("00000120", "00000122", "00000123", "00000124"):
    saved = {f["number"]: f for f in json.loads((DATA_DIR / "assignments" / f"{key}.json").read_text())["frames"]}
    r = pipeline.run(origin_of(key), assets=assets)
    for f, a in zip(r.frames, r.solution.assignments):
        s = saved[f.number]
        if s["status"] == "confirmed": continue
        tot["n"] += 1; tot["v1_placed"] += s["lat"] is not None; tot["v2_placed"] += a.lat is not None; tot["v2_sure"] += a.confidence >= 0.6 and a.lat is not None
        t1 = datetime.fromisoformat(s["time"])
        moved = haversine_m((a.lat, a.lon), (s["lat"], s["lon"])) if (a.lat is not None and s["lat"] is not None) else None
        print(f"{key}#{f.number:<2} v1: {s['source']:12s} {t1:%m-%d %H:%M} conf {s['confidence']:.2f} place {(s['location_source'] or 'none'):12s} | "
              f"v2: {a.source:12s} {a.time.astimezone(t1.tzinfo):%m-%d %H:%M} conf {a.confidence:.2f} place {(a.location_source or 'none'):8s} "
              f"{('pc %.2f' % a.place_confidence) if a.place_confidence is not None else '':8s} {a.place_name or ''}"
              f"{('  [%.1f km from v1]' % (moved / 1000)) if moved and moved > 300 else ''}")
print(tot)
