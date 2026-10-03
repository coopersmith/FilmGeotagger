import time
from filmgeo.photos import library
from common import ROLLS, load_run
assets = library.load()
for k in ROLLS:
    t = time.time(); r = load_run(k, assets, fresh=True)
    print(k, "pool", len(r.pool), "events", len(r.events), "states", len(r.inputs.build().states), f"{time.time()-t:.1f}s", flush=True)
