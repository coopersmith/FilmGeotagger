import collections, math
import numpy as np
from filmgeo.photos import library
from filmgeo.embed.cache import VectorCache
assets = library.load()
phone = [a for a in assets if a.lat is not None and not a.is_scan and a.derivative]
print("located phone photos with a derivative:", len(phone))
cache = VectorCache("siglip")
def cell(a, m=200.0):
    return (round(a.lat / (m / 111320.0)), round(a.lon / (m / (111320.0 * max(0.2, math.cos(math.radians(a.lat)))))))
cells = collections.defaultdict(list)
for a in phone: cells[cell(a)].append(a)
sizes = np.array(sorted((len(v) for v in cells.values()), reverse=True))
print("200 m cells:", len(cells), "largest:", sizes[:8].tolist())
for cap in (8, 12, 20, 30):
    tot = int(np.minimum(sizes, cap).sum())
    print(f"  cap {cap:2d} per cell -> {tot} photos")
# how many are already embedded
print("already embedded:", sum(a.uuid in cache.index for a in phone))
ext = collections.Counter((a.derivative or "").rsplit(".", 1)[-1].lower() for a in phone)
print("derivative types:", ext.most_common(5))
synd = sum("/scopes/syndication/" in (a.derivative or "") for a in phone)
print("syndicated:", synd)
