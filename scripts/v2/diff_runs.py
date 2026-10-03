import sys
from datetime import datetime
from run_v2 import run
a, da = run("", quiet=True, verdicts=True, readings=False)
b, db = run("", quiet=True, verdicts=True, readings=True, use_atlas="--atlas" in sys.argv)
for (key, n, x, aa, fa), (_, _, _, bb, fb) in zip(da, db):
    if abs((aa.time - bb.time).total_seconds()) > 1800 or aa.source != bb.source:
        tt = datetime.fromisoformat(x["t"])
        print(f"{key}#{n:<2} {x['tier']}/{x['by']} truth {tt:%m-%d %H:%M}  A {aa.time.astimezone(tt.tzinfo):%m-%d %H:%M} {aa.source} conf {aa.confidence:.2f}   B {bb.time.astimezone(tt.tzinfo):%m-%d %H:%M} {bb.source} conf {bb.confidence:.2f} src {bb.location_source} {bb.place_name or ''}")
