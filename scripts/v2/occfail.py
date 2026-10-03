import sys
from datetime import datetime
from run_v2 import run
from sweep import true_event
res, detail = run("", quiet=True, verdicts=True, readings="--readings" in sys.argv, layers="--layers" in sys.argv)
for key, n, x, a, fe in detail:
    if x["tier"] != "photo": continue
    te = true_event(key, x["uuid"])
    if a.event != te:
        tt = datetime.fromisoformat(x["t"])
        print(f"{key}#{n} by={x['by']} truth ev {te} {tt:%m-%d %H:%M}  got ev {a.event} {a.time.astimezone(tt.tzinfo):%m-%d %H:%M} {a.source} conf {a.confidence:.2f} claude_conf {x['claude_conf']} q {fe.q:.2f}")
