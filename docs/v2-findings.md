# Engine v2 — place first (COO-177)

3 October 2026. The request: ignore the UX, question everything, and rework the logic that
arrives at *where* a frame was taken — the camera roll as the primary signal plus the fact that
a roll is shot in order, with check-ins and anything else as an optional extra layer.

This is the record of what was measured, what was built, what was tried and dropped, and what
the numbers are. `scripts/v2/` reproduces all of it without an API call.

## 1. A ground truth worth measuring against

Until now the engine was scored on 35 hand-anchored frames from the spring. By October the user
had reviewed eleven rolls in the UI, and their decisions are a better answer key than anything
before it. `scripts/v2/truth.py` turns them into one row per frame, by how strong the truth is:

| tier | frames | what it is |
|---|---|---|
| photo, by Claude | 60 | the user confirmed the photo verification chose |
| photo, by the user | 6 | the user picked the photo themselves |
| hand | 18 | the user typed a time and/or a place (a fact, a check-in, a pin) |
| weak | 28 | an interpolation the user accepted untouched — not evidence, never scored |
| unconfirmed | 25 | |

137 frames, 84 with strong truth (81 with a known place, 81 with a known time). The 24
frames *the user had to supply* are the measure of what the engine missed. There is a selection
effect and it is stated wherever it matters: the 60 are frames v1 could find, by definition.

## 2. Where v1 lost frames

v1's hit rate was 60 of 137 (44%). `diag.py` follows each of the 84 through the pipeline:

* The true occasion was the single most similar event for 62 of 80, inside the top 12 for 73.
  Claude was right on 67 of the 72 it was shown. Retrieval and verification were not the
  problem for the frames that *have* an occasion to match.
* **For the frames the user had to fix, the most similar phone photo in the window was at the
  true place for 14 of the 20 that had any photo of the place at all** — while the true
  *occasion* ranked 3rd to 88th and Claude, asked "same occasion?", correctly said no.

Roll `00000126` is the case in one picture: six frames at a house the phone photographed 669
times across a week. There is no single occasion to match. v1 gave up on all six; their nearest
photo was at the house every time.

v1's unaided proposals, scored on the truth (`baseline.py`):

| | place within 300 m (81) | of the user's 23 fixes | right day (81) | within 1 h |
|---|---|---|---|---|
| v1 with Claude verdicts ($2.50 a roll) | 62 (77%) | 5 | 72 | 69 |
| v1 without Claude | 0 — it places nothing | 0 | 62 | 47 |
| a similarity vote over places, no sequence logic, no API (`place_knn.py`) | 70 (86%) | 15 | — | — |

The engine was asking the wrong first question. "Which photo shows the same occasion" has an
answer for a frame shot beside the phone; for a frame shot at a place visited ten times it has
none, and the *where* — which is what a geotagger is for — was sitting in the similarities all
along.

## 3. The design

**Where, then when.**

1. **Evidence** (`align/evidence.py`). Each frame's 30 nearest photos vote, soft-max weighted
   (τ = 0.03). The weight within 300 m of a spot is the evidence the frame was taken there. How
   much the whole vote is worth (`q`) depends on how similar the best photo is at all: the
   winning place was within 300 m for 54 of 54 frames whose best photo scored ≥ 0.85, 11 of 13
   at 0.80–0.85 and 5 of 14 below — a logistic centred at 0.775.
2. **Readings** (`align/readings.py`, `gazetteer.py`). A frame of a farm stand's sign needs no
   phone photo. Verification had been writing down `signage_text` and `place_guess` for every
   frame since M1 and nothing used them: 201 of 241 cached verdicts carry a guess, 36 carry
   signage. Each is looked up near the regions the window's trail covers — Apple's MapKit
   local search, which knows the small businesses OpenStreetMap does not (Nominatim found none
   of three) — and kept only if the hit's *name really is the text* (both ways: a search engine
   answers anything) and the trail could reach it. A place both a sign and the guess name beats
   either alone. On the reviewed rolls: 9 readings, 6 with truth, all 6 within 175 m, three of
   them frames the user had set by hand (Young Family Farm, Evelyn's Drive In, Ristorante La
   Martellina).
3. **A timeline that carries places** (`align/model.py`, `align/visits.py`). Events as before;
   a silence between two bursts of photos at one place is a *stay* there; a check-in or a
   timeline stop is a *recorded visit* — one more entry in the diary, with no special handling
   and nothing required; every gap remembers where the trail was on both sides and for how
   long, so a place off the trail can be offered where it can be reached and nowhere else.
4. **Emissions.** A frame's support for a state is its vote for the state's place: in full for
   the visit whose own photo looks most like the frame, and for another visit to the same
   place a fraction that depends on how *reliable* a best-looking visit is at that similarity
   (about 9 in 10 above 0.84 cosine, 1 in 2 below, 0 of 6 for a house visited daily) — so a
   strong match holds against the roll's order and a merely place-level match yields to it.
   Emission = (1 − q) × the first engine's floor + q × support.
5. **Decide the place, then the time within it** (`align/locate.py`, `solve.solve`). A place
   holding most of a believable vote is decided outright and its frame held to it. The roll is
   solved; each remaining frame takes the heaviest place of its vote that the held ones leave
   *possible*; those are held too; a last solve gives the times. How many time slots a place
   has never enters the place decision.
6. **Verification stays**, as anchors, on the frame's own scale (a verdict worth q against
   v1's best case must be worth q against a place-evidence state that scores higher).
7. **Two confidences.** `confidence` is still "which occasion"; `place_confidence` is new and
   separate. A frame at the house is placed at 0.9+ and dated at 0.1, and says so.

## 4. Results

Unaided proposals — no user facts, no overrides — on the same truth (`sweep.py`):

| | place ≤ 300 m (81) | user's fixes recovered (23) | occasion (66) | right day (81) | ≤ 1 h | frames placed (137) |
|---|---|---|---|---|---|---|
| v1 + Claude | 62 | 5 | — | 72 | 69 | — |
| v1 free | 0 | 0 | — | 62 | 47 | — |
| **v2 free** | **69** | **13** | 61 | **75** | 69 | 110 |
| v2 free + readings | 72 | 16 | 61 | 75 | 69 | 115 |
| v2 + Claude verdicts | 72 | 14 | 66 | 73 | 69 | 127 |
| **v2 + Claude verdicts + readings** | **75 (93%)** | **17** | 66 | 73 | 69 | 132 |
| … + recorded visits (Swarm) | 75 | 17 | 66 | 73 | 69 | 131 |

* **With no API call, v2 places more frames correctly than v1 did with verification (69 vs
  62), and dates as many or more (75 vs 72 right day).**
* With the same verdicts already paid for, v2 recovers 17 of the 23 frames the user had to
  fix by hand; v1 had 5.
* "Readings" in the free row stands for a cheap read-the-frame call (one image a frame); in
  the Claude rows they come free with the verdicts.
* Recorded visits change the totals by nothing here but put the farm-stand frame on its true
  afternoon (its place came from the sign; its *time* only the check-in knows).

**Confidence means something** (`calib.py`), with no API:

| occasion confidence | frames | right | | place confidence (unanchored) | frames | within 300 m |
|---|---|---|---|---|---|---|
| ≥ 0.8 | 38 | 38 | | ≥ 0.95 | 26 | 26 |
| 0.6 – 0.8 | 13 | 12 | | 0.8 – 0.95 | 35 | 34 |
| 0.3 – 0.6 | 12 | 10 | | 0.6 – 0.8 | 11 | 9 |
| < 0.3 | 3 | 1 | | < 0.6 (no pin written) | 9 | 0 |

**Held out.** All of the above was shaped on the eleven reviewed rolls. The eight hand-tagged
rolls of the spring shaped nothing (`heldout9.py`; 35 frames anchored by hand, window = the
roll's true range ± 2 days, no facts, no verification):

| | right day | within 1 h | place ≤ 300 m |
|---|---|---|---|
| v1 | 4 / 35 | 4 / 35 | 0 / 24 |
| v2 | 15 / 35 | 15 / 35 | 19 / 24 (79%) |

Place generalises (79% held out, 85% in sample). The day does not on those rolls — months of
frames of a newborn at home, the many-visits case at its worst — and that is the case v2 now
reports as "placed, not dated" rather than guessing. On the two of them that have verdicts
(`heldout.py`, 9 frames): v1 + Claude 7 / 7 / 7, v2 free 6 / 6 / 8, v2 + Claude 7 / 7 / 8.

**What is still wrong** (`inspect_fail.py --claude`):

* Two wrong pins, both at place confidence 0.64–0.65, just over the gate: a generic water view
  matched 6 km off, and a church exterior matched to the interior of another church 21 km away.
* The six house frames of roll 126: right place (39–75 m), a day late, occasion confidence
  0.02–0.12. Honestly unknowable from the photos; the user dated them from a check-in.
* Frames of ferns and of a mooring field with no phone photo anywhere near: no pin, correctly.

## 5. What was tried and dropped

* **Centring or hub-correcting the SigLIP vectors** (`checks.py`): each domain centred 65/81,
  both on the pool mean 70, hub-corrected 68, raw cosine 70. Nothing.
* **The all-time place atlas** (`filmgeo embed --atlas`, 40,120 photos embedded, 48 minutes).
  The idea was sound on its face: every place the reviewed rolls missed had 4 to 474 phone
  photos in the library from other years, and only 12k of 129k located photos had ever been
  embedded. Measured: at every reach from 5 to 150 km the place score was unchanged or worse,
  and in the final architecture the free engine fell from 69 to 64. The frames that lack a
  photo in the window are the generic ones — water, ferns, a sign — and an embedding ties
  those to a look-alike beach 64 km away at 0.90 as readily as to the real one, 28 atlas
  photos of which never made the top 30. Off (`config.ATLAS`, `FILMGEO_ATLAS=1`); the vectors
  are cached for the next idea (a place-recognition model, geometric verification).
* **Dealing a place's vote out among its visits.** Calibrates the occasion posterior
  beautifully (62 of 66 first choices; ≥ 0.9 right 29 of 29) and breaks the path: a
  restaurant with 7% of a frame's vote and one visit outbid a house with 92% and forty, and
  drew six frames into its time slot.
* **Scaling the matching visit by the number it competes with** (calibrated, consistent, the
  "right" likelihood). A run of look-alike days at the house then out-voted three verified
  anchors at the cathedral.
* **Posterior-mass decoding** in place of the single best path (`solve.posterior_path`, kept,
  off): with the final emissions it is equal when verdicts are in and worse without them
  (right day 64 of 81 against 75).
* **Reading the place off the posterior mass.** A house with sixty stays and a tenth of the
  vote outweighed a farm stand read off the frame with nine tenths and one recorded visit.
  Hence the place decision by vote among what is possible — order as a constraint, not a
  weight. Third time this project has measured that (docs/m5-findings.md).
* **The v2 posterior as verification's shortlist**: no better than plain similarity (the true
  occasion within the top 3 for 65 vs 63, top 12 for 65 vs 66). The shortlist stays as it was.

## 6. What changed besides the engine

* **A confirmation freezes its frame** (`FrameOverride.snapshot`). Before, a confirmed frame
  with no pick and no fact was re-derived on every solve; a new engine, or a neighbour's fact,
  could move a frame already written to disk. Now it is held to what was confirmed, to the
  second; existing confirmations adopt the last saved solve; the user's *newer* decisions
  release the confirmations they contradict (shown as needing confirmation again) instead of
  failing the solve. Checked on the real rolls (`frozen_check.py`): 117 confirmed frames
  across 13 rolls, none moved under v2.
* **`geo.clusters` was quadratic.** A month-wide interval cost 6 to 30 seconds a solve, which
  was the whole of a re-solve's time in the review UI. Running sums: under a second.
* **`filmgeo verify` skips frames the engine is already sure of** (`--sure 0.6`): on the
  reviewed rolls an unverified frame dated at 0.6 or more was on the right occasion 50 times
  in 51, and 66 of the 137 frames reach it.
* **Loading a roll scanned the whole library once per check-in.** `PhotosTrail.offset_at` walked
  141,000 assets, with a filename regex each, for every check-in, route sample and NFC tap: 29
  seconds to open a roll with 134 check-ins in its window. Indexed once: 2 seconds.
* The vector cache saves in chunks, atomically, and survives an unreadable chunk.
* `FILMGEO_ENGINE=v1` runs the first engine, for comparison.

## 7. What this suggests next (not done; the UX was explicitly out of scope)

* **Spend Claude where the engine is unsure.** A cheap read-the-frame pass (one image) gets
  the signs for every frame; full verification only for frames under 0.6. Estimated at under
  half of today's $2.50 a roll. Needs the user's go-ahead to measure, since it costs money.
* **"Which day at the house?"** is the remaining hard case, and it is a question a person
  answers from clothes, light and who is in the frame. A verification round that shows one
  photo from each *visit* to the decided place is the natural way to ask it.
* **The review UI can ask a different question now**: not "is this the right photo" but "is
  this the right place", with the day as a second, separate answer.
