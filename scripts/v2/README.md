# The v2 evaluation (COO-177)

Everything here reads `.filmgeo/` and writes only under `.filmgeo/eval_v2/`. No API calls.
Run from this directory with `uv run --extra embed python <script>` (`FILMGEO_WEATHER=0` keeps the weather adapter quiet).

| script | what it answers |
|---|---|
| `truth.py` | builds `truth.json`: one row per frame of the rolls the user reviewed, with how strong the truth is (photo / hand / weak) |
| `cache_runs.py` | pickles each roll's solved run so the rest is fast; re-run after new verdicts |
| `diag.py` | where each frame with truth was lost in v1: retrieval rank, shown to Claude or not, what Claude said |
| `place_knn.py` | how well a plain similarity vote over places does, with no sequence logic at all |
| `baseline.py` | v1's unaided proposals scored on the truth — the bar |
| `sweep.py [--atlas]` | v2 in its five configurations, one line each |
| `calib.py` | whether occasion and place confidence mean what they say |
| `shortlist.py` | plain similarity against the posterior as a shortlist for verification |
| `tau.py`, `alpha.py` | the two calibrations behind `EvidenceParams.tau_occasion` and `alpha_*` |
| `readings_eval.py` | places read off the frames (signs, guesses) against the truth; asks the gazetteer (cached after the first run) |
| `checks.py`, `atlas_size.py` | the embedding tweaks that did not help; how big the atlas is |
| `heldout.py`, `heldout9.py` | the hand-tagged rolls, which shaped nothing here |
| `frozen_check.py` | runs the real pipeline on every reviewed roll and checks no confirmed frame moves; saves nothing |
| `unconfirmed.py` | v1's saved proposal beside v2's for the frames not yet confirmed |
| `inspect_fail.py`, `dbg.py` | the failures one by one; one frame's posterior in full |
