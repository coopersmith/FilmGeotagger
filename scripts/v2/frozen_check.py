"""Run the real pipeline (v2) on every reviewed roll without saving anything, and check that
confirmed frames come out exactly as the last saved solve had them."""
import json
from datetime import datetime
from common import ROLLS, origin_of
from filmgeo.align import pipeline
from filmgeo.config import DATA_DIR
from filmgeo.events import haversine_m
from filmgeo.photos import library

assets = library.load()
moved = total = 0
for key in ROLLS + ["00007037", "00007044-k12"]:
    saved = {f["number"]: f for f in json.loads((DATA_DIR / "assignments" / f"{key}.json").read_text())["frames"]}
    try:
        r = pipeline.run(origin_of(key), assets=assets, alias=key if key.endswith("-k12") else None)
    except Exception as e:
        print(key, "FAILED:", type(e).__name__, e); continue
    conf = prop = changed_prop = newly_placed = 0
    for f, a in zip(r.frames, r.solution.assignments):
        s = saved[f.number]
        dt = abs((a.time - datetime.fromisoformat(s["time"])).total_seconds())
        dd = haversine_m((a.lat, a.lon), (s["lat"], s["lon"])) if (a.lat is not None and s["lat"] is not None) else (0 if (a.lat is None and s["lat"] is None) else 1e9)
        if s["status"] == "confirmed":
            conf += 1; total += 1
            if dt > 1 or dd > 5 or (a.tzoffset != s["tzoffset"]):
                moved += 1
                print(f"   MOVED {key}#{f.number}: time {s['time']} -> {a.time.isoformat()} ({dt:.0f}s) place moved {dd:.0f} m tz {s['tzoffset']}->{a.tzoffset} src {s['source']}->{a.source}")
        else:
            prop += 1
            changed_prop += dt > 3600 or dd > 300
            newly_placed += (s["lat"] is None and a.lat is not None)
    print(f"{key}: engine {pipeline.to_json(r)['engine']}, {conf} confirmed held, {prop} unconfirmed ({changed_prop} now proposed differently, {newly_placed} newly placed), readings {[(x.frame + 1, x.name) for x in r.readings]}", flush=True)
print(f"confirmed frames: {total}, moved: {moved}")
