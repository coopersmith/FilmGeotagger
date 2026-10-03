import cProfile, pstats
from common import load_run
from run_v2 import solve_v2
r = load_run("00000126")
solve_v2(r, verdicts=True)
pr = cProfile.Profile(); pr.enable()
solve_v2(r, verdicts=True)
pr.disable()
pstats.Stats(pr).sort_stats("cumulative").print_stats(18)
