import sys
import numpy as np
from common import load_run
from run_v2 import solve_v2
key, n = sys.argv[1], int(sys.argv[2])
r = load_run(key)
model, sol, ev = solve_v2(r, verdicts="--claude" in sys.argv, readings=True, layers="--layers" in sys.argv)
i = n - 1; fe = ev.frames[i]
print("q", round(fe.q, 3), "places", [(round(h.mass, 2), round(h.photo_mass, 2), round(h.read_mass, 2), round(h.lat, 4), round(h.lon, 4)) for h in fe.places[:4]])
post = sol.posterior[i]
order = np.argsort(-post)[:14]
for j in order:
    s = model.states[j]; c = model.choices.get((i, j))
    print(f"  {post[j]:.3f} em {model.emissions[i, j]:7.2f} {s.kind:6s} {s.t_lo:%m-%d %H:%M}..{s.t_hi:%m-%d %H:%M} stay={s.stay} visit={s.visit} ev={s.event} loc={(round(s.lat,4), round(s.lon,4)) if s.has_location else None} choice={c and (ev.photos[c[2]].filename[:24], round(c[3],2), c[4])}")
kinds = {}
for j, s in enumerate(model.states):
    k = s.kind + ("/offer" if (i, j) in model.choices else "")
    kinds[k] = kinds.get(k, 0) + post[j]
print({k: round(v, 3) for k, v in kinds.items()})
a = sol.assignments[i]; print(a.source, a.location_source, a.place_confidence, a.place_name, a.time, a.lat, a.lon)
for k in (i - 1, i + 1):
    if 0 <= k < len(sol.assignments):
        b = sol.assignments[k]; print("  neighbour", k + 1, b.source, b.time, b.t_lo, b.t_hi)
