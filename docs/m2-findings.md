# M2 — alignment engine: findings

Running notes for M2, one section per piece of work. Read `m1-findings.md` first: the
half-guessed ground truth and the SigLIP-only decision both still bind here.

## COO-117 — `Signal` interface, `user_facts`, `photos_trail`, `nfc_log`

Landed 3 September 2026. `src/filmgeo/signals/`, 16 unit tests, two CLI commands.

### The interface is two nouns

A `Signal` returns **trail points** (the user was here at this instant, with an offset if the
source knows one) and/or **constraints** (a roll or a frame must lie in a time range and/or
near a place, or is to be skipped, or shares a day with another frame). Anchors are not a
signal: they come out of retrieval and verification. `collect()` merges sources;
`effective_window()` intersects the roll constraints into the retrieval window; and
`frame_bounds()` pushes every frame fact through the monotone order, so "frame 12 is on
4 July" bounds frames 1-11 from above and 13-36 from below before the solver ever runs.

### `user_facts` is a CLI input now

`filmgeo facts <roll> --from 2026-04 --to 2026-04 --camera "Mamiya 7II" ...` and
`--frame 3 --on 2026-04-04`. Periods are the user's own granularity — `2026`, `2026-04`,
`2026-04-12`, `2026-04-12 14:05` — read in an IANA zone (default this Mac's) as half-open
ranges, so "April" needs no arithmetic at the prompt. Facts persist as one JSON per roll in
`.filmgeo/facts/`; the M3 UI edits the same file. `validate()` refuses a window that ends
before it starts, a frame dated before an earlier frame, and a place with one coordinate.

`filmgeo report` now uses the facts window when one exists. On roll `00007044`, retrieval
with the honest window — the whole of April, 2,428 phone photos, rather than the answer
key's range ±2 days — still finds the phone counterpart in the top 8 for **9 of 10 frames**.
The number M1 warned was flattered by a window derived from the answer holds up under the
window a user would actually type.

### The NFC note is richer than PLAN assumed, and slower to read

PLAN.md expected one line per scan. The real note (35 entries since April 2024) has a
multi-line block per tap, separated by `--`, in two shapes because the Shortcut changed:

```
🕑 Apr 29, 2024 at 5:34 PM        🕑 Aug 12, 2026 at 11:37 AM
📍41.5536 , -71.1929              📍43.5828 , 11.3179
🗺️ 4398 Main Rd                    🗺️ Via Cesare Battisti 8A
Tiverton RI 02878                  50022 Greve in Chianti Tuscany
United States                      Italy
📷 Mamiya 7II
🎞️ Kodak Portra 160
📓Notes f11, 125
```

18 entries carry camera, film and notes; 16 carry only time and place. The parser tolerates
both, plus: a narrow no-break space before AM/PM, emoji with or without the variation
selector, the object-replacement character where a photo is attached, address blocks of any
length, and the note title itself (which begins `📍🎞️`). **Repeated taps append repeated
entries** — three identical blocks at one instant on 14 April 2026 — so consecutive
duplicates collapse to one point.

**Only time, location and camera are trusted.** The user says the film stock in the note is
stale — the Shortcut carries whatever was last entered, not what is loaded — so `film` is
parsed but never reaches a trail point, and the notes line is treated the same way.

The camera line PLAN recommended adding is already there on most entries; the
`loaded`/`finished` word is not yet, but the parser surfaces it as `NfcEntry.event` when it
appears. Only one entry falls inside the April window that the eval rolls occupy, so the log
is a trail source for future rolls more than a lever on the current ground truth.

**Reading the note costs minutes.** `osascript` on this library needs a `with timeout of 900
seconds` block; the default two-minute AppleEvent timeout fires, and the Notes MCP connector
fails outright on buffer size. The text is therefore cached at `.filmgeo/nfc_log.txt` and
re-read only with `filmgeo signals --refresh-nfc`.

**The note's times have no zone.** The Shortcut writes wall-clock time. `PhotosTrail.offset_for`
resolves it from the nearest phone photo by wall clock (osxphotos dates are aware in the
photo's own zone, so wall clocks compare directly), falling back to this Mac's zone. On the
Italy entries that yields +02:00, on the Rhode Island ones −04:00.

### `photos_trail`

Every non-film asset in the window is a trail point, including the ones without a local
derivative and the ones without GPS — a time-only point still carries the offset. April 2026
gives 2,428 points, 2,068 with GPS, every one at −04:00.

## COO-114 / COO-115 — HMM states, emissions, Viterbi and forward-backward

Landed 3 September 2026. `src/filmgeo/align/model.py`, `align/solve.py`, 14 unit tests on
synthetic timelines, `scripts/align_m2.py` for the measurement below.

### Shape

States per roll: one anchor state per verified candidate (only its frame may occupy it), one
state per phone-photo event, one per gap between events (plus the lead-in and tail of the
window), and a single `outside` state. Sorted by time. A transition is allowed when the next
state can end no earlier than the current one begins, which is the monotone constraint
expressed on intervals rather than ranks — it lets two frames share an event, lets an anchored
frame be followed by frames in the same event, and refuses anything that moves backwards.
`outside` is reachable from and to anything at a flat cost; its posterior mass is the
wrong-window signal COO-118 will read.

Emissions are log-probabilities. There is no fitted calibration yet (M1 left it open, COO-140
refits from confirmations), so `AlignParams` carries a hand-set logistic on SigLIP similarity
and hand-set floors, each documented at the field. The structure the tests pin down is what
matters: an anchor beats its own event's similarity, an event with nothing similar still holds
a floor (the user photographs "often, not always"), gaps and outside sit below that, a
`time_of_day` clue that contradicts an event's local hours costs a fixed penalty, a
same-outing pair earns a bonus for staying in one event, and a user fact zeroes out every
state it excludes — a date fact also zeroes `outside`, and a locked frame prunes everything
but its anchor. Constraints that leave a frame with no state raise instead of solving.

The solver runs Viterbi for the proposal and forward-backward for the posterior. Confidence is
the posterior mass on the chosen state; the interval is the union of the fewest states that
carry 90% of the mass, then **clipped to the frame's own facts and to the nearest anchored
frames on either side** — without that clip, a gap state that ends at an anchor's instant
reports the whole gap. Anchored frames take the anchor's instant exactly; unanchored ones take
their state's midpoint pulled inside the neighbouring anchors, then everything is forced
strictly increasing by 2 s so scan order survives in Photos and Lightroom.

### Measured: interpolated intervals contain the truth

`scripts/align_m2.py` simulates verification from the ground truth itself (the frames
`Roll.anchored()` recovers, whose timestamp matches a phone photo to the second), so the
solver is measured on its own logic rather than on Claude's precision. Nine rolls, cached
SigLIP vectors, no API calls. Scored on held-out anchored frames only. **These are the numbers
after the two fixes described under COO-118 below**; the first measurement, made with a
transition bug and a mis-centred calibration, read 23.6 h median error and 72.5 h median
width in the first row and is superseded.

| anchors given | held-out frames | truth inside 90% interval | median abs. error | median width |
|---|---|---|---|---|
| every other anchored frame | 54 | **54 / 54** | 0.0 h | 0.8 h |
| first and last only | 97 | **97 / 97** | 0.7 h | 72.8 h |
| none — similarity, events, order | 113 | **113 / 113** | 1.2 h | 96.2 h |

This is the M2 exit criterion ("anchored frames exact; interpolated intervals contain the true
time") met on the hand-tagged rolls. Three caveats the numbers carry:

* **The interval test uses a two-minute tolerance.** The user tagged groups of frames a second
  apart in Lightroom, not always in scan order, so a frame between two oracle anchors one
  second apart can sit a second past its clipped interval. That is the ground truth's
  granularity, not the solver's.
* **Held-out frames are the easy half by construction.** A frame counts as anchored ground
  truth precisely because a phone photo of the same second exists, so similarity has a target
  to find; the 1.2 h median error with no anchors is what happens when a counterpart exists.
  The other half of every roll — frames with no phone counterpart — get the interval, not
  the point, and the interval is what transfers.
* **The intervals are honestly wide where there is no evidence.** With only the ends anchored
  the median interval is three days; the roll that lived in the camera for 22 days reports
  weeks. This is the "between Tue 14:05 and Wed 17:40" output M1 argued for; the width is
  what verification anchors and outing groups (COO-119) exist to shrink, and it is now
  measurable.

A month-wide facts window (`00007044`: 2,428 photos, 182 events, 371 states) solves in well
under a second, so the windows users will actually type are not a performance concern.

## COO-116 — location and offset derivation (`geo.py`)

Landed 3 September 2026. `src/filmgeo/geo.py`, 8 unit tests; `scripts/align_m2.py` now scores
location and offset as well as time.

### Rules

An anchored frame takes its photo's GPS and offset exactly. For every other frame the trail
points inside its interval decide: within 300 m of their centroid, that centroid is the pin
(`ok`, source `trail`); spread wider but the frame lies between two anchors within 2 km of
each other, the pin is interpolated linearly in time between them (`ok`, `interpolated`);
otherwise the frame is `ambiguous`, the distinct clusters (greedy, 300 m radius, biggest
first, carrying any NFC label) are kept for the UI, and **no pin is written**. No trail and no
close anchors is `none`. The offset comes from the nearest trail point in time; if points in
the interval disagree, the frame is flagged `offset_disputed` and the nearest still wins.

### Measured on the nine hand-tagged rolls

Held-out anchored frames, the user's hand-copied GPS as the answer, phone-photo trail only.
Numbers after the COO-118 fixes; the first measurement (24/54 `ok` in the first row, every
frame ambiguous without anchors) is superseded, because the wormhole and the saturated
calibration were widening every interval to a week and pulling many places into it.

| anchors given | ok | ambiguous | none | truth among offered clusters | truth is the top cluster | offset right |
|---|---|---|---|---|---|---|
| every other anchored frame | 49 / 54 | 5 | 0 | 5 / 5 | 5 / 5 | 54 / 54 |
| first and last only | 50 / 97 | 47 | 0 | 47 / 47 | 45 / 47 | 97 / 97 |
| none | 14 / 113 | 99 | 0 | 98 / 98 | 90 / 98 | 113 / 113 |

Three things follow.

**The offset is a solved problem on this data.** Every held-out frame got the right UTC
offset in every mode, because the nearest phone photo in time always carried it. Travel days
are not in the 2026 batch; `offset_disputed` exists for them and is unexercised.

**A pin is only ever written when it is right.** The `ok` frames sit at 0.00-0.01 km median
error and 0.1 km at the 90th percentile, and the derivation refused to guess on the rest.
With nothing anchored, 88% of frames are ambiguous: a multi-day interval spans several
places, and the design says so rather than picking one.

**"Ambiguous" is a three-way pick, and the first option is usually right.** The true place was
within 500 m of an offered cluster on 150 of 150 ambiguous frames, and it was the biggest
cluster on 92-100% of them, with a median of three clusters offered. That reshapes the M3 UI:
an ambiguous frame is not a blank map but a short list with a strong default, and "confirm
top cluster" will resolve most of them in one keystroke. It also says the trail is
informative even without anchors — the user photographs where they are — so a cluster
prior for the solver's gap states is worth trying later.

## COO-118 — reverse-roll test, wrong-window detection, and two bugs it found

Landed 3 September 2026. `src/filmgeo/align/checks.py`, 6 unit tests; `AlignParams` and
`build_transitions` in `model.py` changed as a result.

### Building the reverse test exposed a transition bug

Solving a deliberately reversed synthetic roll, the *forward* order still held two of three
anchors that lay in the wrong sequence. The path went anchor (day 9) → its event → the gap
before it → the event of day 5 → the gap before that → day 2: each pair of touching intervals
is compatible at their shared instant, but the chain walks backwards through a week. A
first-order transition cannot carry the time variable that would forbid it. The fix is what
PLAN.md specified in the first place — a monotone constraint on state **rank** — with one
exception so a frame after an anchored one may sit later in the anchor's own event. The
`outside` state had the same wormhole in a different form (leave the window, re-enter
earlier), so it is now two states, `before` and `after`, and a path may enter or leave the
window but never both.

### The calibration centre was wrong by 0.3

Measured on the 113 anchored frames: similarity to the true photo has median 0.948, to the
best photo of any *other* event 0.877, and the pool median is 0.70. The informative range is
0.85-0.99; the hand-set logistic centred at 0.55 was saturating every event to 1.0, so the
solver had been placing frames on gaps-versus-events alone. Refit by grid on true-vs-best-other:
centre 0.88, slope 10. Two consequences worth knowing: the true photo beats the best photo of
every other event only **67%** of the time, so similarity is a real but weak discriminator
(which is why verification exists); and a verdict at 0.9 confidence is worth log(0.9/0.1) =
2.2 over the alternatives, about the same as the jump penalty for a week, so the emissions now
treat a verdict as a likelihood split (the matched event takes q, everything else is scaled by
1-q) rather than adding similarity to the anchor a second time.

Together these two fixes took the every-other-frame measurement from 23.6 h / 72.5 h to
0.0 h / 0.8 h (median error / median width), and the no-anchor case from 72 h to 1.2 h.

### Reverse-wound: count anchors, not score

Because one anchor and one week's jump are worth about the same, the scores of the two orders
sit close even when one is plainly wrong. What a reversed roll changes decisively is the
number of anchors a monotone path can hold: of any two anchors in the wrong sequence, it keeps
one. So the flag is: reversed order scores higher **and** holds at least three anchors **and**
at least two more than the forward order.

### Wrong window: similarity cannot tell, so verification must

Rolls run with their window shifted ±14 and ±30 days (where vectors were cached):

| | right window | shifted |
|---|---|---|
| per-frame score above the null path | 2.7 - 3.3 | 2.5 - 3.1 |
| median best similarity | 0.86 - 0.96 | 0.83 - 0.89 |
| median z of the best candidate | 1.5 - 4.3 | 1.6 - 2.6 |

They overlap. The user photographs the same rooms, the same people and the same streets
month after month, so a wrong month still holds photos that look like the roll. The
doubtful-window flag therefore rests on verification: a roll whose frames were verified and
fewer than 10% anchored is doubtful, as is one whose mean posterior mass on `outside`
exceeds 0.25; with no verification the check says so rather than guessing. `best_days()`
sums posterior mass per calendar day so the UI can suggest where to look, `widen()` adds a
month each side, and `new_candidates()` lists only the top-K that appeared after widening,
which is all that needs verifying.

The deliberately-wrong-month validation on real verdicts is COO-120's, and needs embeddings
for the shifted windows, which have to be built from Terminal.app (see CLAUDE.md).

## COO-120 — `filmgeo align`, `filmgeo verify`, the HTML report, and validation

Landed 3 September 2026. `src/filmgeo/align/pipeline.py`, `align/report.py`, two CLI
commands, 3 more tests (47 in the suite).

### The pipeline

`filmgeo align <roll>` resolves a scan folder or a hand-tagged key, takes the window from
the facts file (or, for an eval roll with no facts, the true range ±2 days, and says so),
builds the pool and events, embeds the frames if needed, retrieves top-K from cached
vectors, reads verdicts from `.filmgeo/verdicts/<roll>.json`, builds the constraints and
trail from every signal, solves, places, runs the reverse and window checks, and writes
`.filmgeo/assignments/<roll>.json` plus `reports/align_<roll>.html`. The JSON is the
`assignments` table PLAN.md describes, one document per roll; M3's API reads and re-solves
from the same inputs.

`filmgeo verify <roll>` shows each frame's top-K to Claude and stores the verdict, the
candidates shown, the confidence, the evidence sentence and the clues. It prints the cost
($0.035 a frame, measured in M1) and asks before spending; `--only-new` after `--widen`
verifies just the candidates that widening surfaced, which is how a doubtful window is
re-run cheaply.

The report carries the window timeline (events as bars, each frame's interval as a line and
its assigned time as a tick, coloured by confidence, the hand-tagged truth as a dot when
known), then a row per frame: source badge, local time with offset, "between … and …",
confidence bar, the pin or the offered clusters, Claude's evidence, and the truth with its
delta and whether it fell inside the interval.

### Validation without verification

Similarity only, no verdicts, no API spend:

| roll | window | frames | truth inside interval | median interval width |
|---|---|---|---|---|
| `00007044` | facts: all of April | 10 | 10 / 10 | 19 days |
| `00007037` | true range ±2 d (22-day roll) | 37 | 37 / 37 | 8 days |

Both meet the exit criterion's second half. The intervals are wide because nothing is
anchored — this is the honest floor. The one frame on `00007044` with a fact ("frame 3 on
4 April") came out pinned to the Montague Street trail centroid, which is where the roll's
hand tags put it.

### Validation with verification — pending two things only the user can do

The exit criterion's first half ("anchored frames exact") and the deliberately-wrong-month
run both need real verdicts, which cost about $0.035 a frame, and the wrong-month run needs
embeddings for the shifted window, which have to be built from Terminal.app because Photos
derivatives are unreadable from tool-call shells. Commands to run are in CLAUDE.md. Once
both exist, `filmgeo verify` then `filmgeo align` on `00007037` (multi-day) and on
`00007044` with its facts window moved to May will finish this issue.
