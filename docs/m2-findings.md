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

## COO-120, continued — real verdicts, and what they found in the ground truth

Run 3 September 2026 by the user from Terminal.app (`filmgeo verify` on `00007037`,
`00007044` and `00007044` under a deliberately wrong May window; $2.00).

### Claude matched the frames to themselves

The verdicts looked superb — 26 of 37 and 10 of 10 anchored, 23 and 9 of them exact to the
second — until the anchor photos were inspected: **30 of the 36 anchors were untagged copies
of the scans**, sitting in the Photos library at the same instant as the tagged frame, without
the `Film` keyword. The library holds 115 such copies. Three things had let them through:
`library.candidates()` filtered on the keyword alone; `Roll.anchored()` tested against every
asset lacking it, so it counted a frame anchored to its own copy; and the trail included them.

Fixed with `Asset.is_scan` — keyword, lab filename (`000070440001.jpg`, `348542_0012.jpg`,
with Photos' `_Original` and dated-export prefixes), or scanner make (`NORITSU KOKI`) — used
by the candidate pool, the trail, and `library.phone_times()` for the anchored test.

### Everything above, re-measured on the clean set

The anchored ground truth falls from 113 frames to **35** (`m1-findings.md` carries the M1
correction: recall@8 is 62.9% there, not 91.2%, and the exit bar is not met). The M2
oracle measurements on the 35:

| anchors given | held-out | truth inside interval | median abs. error | median width | location ok | truth is top cluster |
|---|---|---|---|---|---|---|
| every other anchored frame | 16 | 16 / 16 | 0.0 h | 3.3 h | 11 / 16 | 3 / 4 |
| first and last only | 20 | 20 / 20 | 13.2 h | 56 h | 15 / 20 | 5 / 5 |
| none | 35 | 34 / 35 | 49.8 h | 96 h | 0 / 35 | 33 / 35 |

The solver's guarantees hold: intervals contain the truth, anchored frames are exact,
offsets are right on every frame. The no-anchor error rises from 1.2 h to 50 h, which is the
honest number — the earlier one was similarity finding the frame's own copy.

### The verified runs, with the copies discarded

| run | candidates shown that were scan copies | anchored | anchors within 30 min | interpolated inside interval | window check |
|---|---|---|---|---|---|
| `00007037`, true range | 58 / 222 | 3 / 37 | 2 | 32 / 34 | doubtful |
| `00007044`, April facts | 22 / 60 | 1 / 10 | 0 | 9 / 9 | doubtful |
| `00007044`, wrong May window | 0 / 60 | 1 / 10 | 0 (50 days off) | 0 / 9 | doubtful |

The first two are not a fair test of verification: a quarter to a third of every candidate
list Claude saw was the frame's own copy, which it (correctly) chose, and those verdicts are
now discarded. They need re-running with the clean pool, about $1.65, before precision can be
stated. The May run *is* fair — no copies reached it — and it is the wrong-window result the
issue asked for: Claude abstained on 8 of 10 (against 0 of 10 under April), matched 2 at a
mean confidence of 0.51, and the one anchor that survived the threshold is seven weeks from
the truth. Verification separates the windows where similarity could not.

The doubtful-window rule gained a count floor from this: on a 10-frame roll one lucky match
is 10%, so "fewer than max(2, 10% of frames) anchored" is the test.

### Re-run with the clean pool ($1.65) — verification precision on real anchors

| run | accepted | ≤5 min | ≤30 min | ≤2 h | same day | accepted at conf ≥0.8: same day |
|---|---|---|---|---|---|---|
| `00007037` | 20 / 37 | 11 | 13 | 16 | 17 | 12 / 12 |
| `00007044` | 8 / 10 | 1 | 2 | 5 | 5 | 5 / 5 |

So of 28 accepted matches, 15 are within 30 minutes and 22 on the same day; the six wrong-day
accepts all carry confidence 0.38-0.70, and every accept at ≥0.8 is on the right day. M1's
"≥95% of accepted matches correct" was measured with the copies in the pool and is retracted
with the rest of M1's headline; the honest number at a 30-minute tolerance is **54%**, or 71%
at confidence ≥0.8. These are two rolls, and the domestic-repetition failure M1 described is
exactly what the wrong-day accepts look like.

**The solver absorbed every wrong-day verdict.** After alignment, 13 of 37 and 4 of 10 frames
are anchored, and all 17 sit on the right day: the monotone constraint dropped each wrong-day
accept because it contradicted its neighbours, which is PLAN.md risk 2's mitigation doing its
job on real data. Interpolated frames: 23 of 24 and 5 of 6 contain the truth.

### "Anchored frames exact" fails — at retrieval, and for a reason that changes the design

Scored on the nine frames the user genuinely anchored (five on `00007037`, four on
`00007044`): the true photo was in the shortlist of six Claude saw on **0 of 9**. Claude then
chose a same-session photo 2-6 minutes off on six of them (confidence 0.88-0.94) and a wrong
day on three (0.45-0.70), which the solver rejected. After alignment all nine are on the right
day, four within 30 minutes, none exact.

Two conclusions. First, retrieval on genuinely anchored frames is worse than the 62.9% clean
recall@8 suggests once K is 6 and the roll is hard: these nine are in the two rolls where M1's
per-roll recall was 50% and 100% — but the *exact* photo was never in the six. Second, and
more useful: **verification anchors a frame to the occasion, not the instant.** Claude's
question is "same occasion, within an hour", and its answer is right to within minutes when it
is right at all; the solver then writes the chosen photo's second as the frame's time and a
zero-width interval, which is more precision than the evidence carries and is why nine
anchored frames "miss" the truth by five minutes. An anchored frame's interval should be the
anchor's event span (or ±30 min if the event is a single photo), with the photo's time still
the written value. Filed as a follow-up; it is a small change in `solve._intervals`.

COO-120's exit criterion therefore reads: interpolated intervals contain the truth (28 of 30
on the two rolls); anchored frames are exact when the exact photo reaches Claude, which on
the honest ground truth it currently does not.

## COO-145 — an anchored frame's interval is its occasion, not the photo's second

Landed 4 September 2026. `align/model.py` (`State.occ_lo/occ_hi`, `OCCASION_MIN_SPAN`),
`align/solve.py` (`_intervals`), `align/report.py`; one new test, 51 in the suite.

An anchor state keeps the photo's instant as the frame's *time* — that is what gets written,
as PLAN.md says — but reports the anchor's event span, widened to at least an hour around the
photo, as its *interval*. Frames before an anchored one end no later than its occasion's end;
frames after it start no earlier than its start. The report now says "this occasion, Sat 4 Apr
14:01–17:40" instead of "exact".

Effect, same verdicts as the COO-120 re-run, nothing else changed:

| roll | frames with the truth inside the interval, before | after |
|---|---|---|
| `00007037` | 28 / 37 | **36 / 37** |
| `00007044` | 5 / 10 | **10 / 10** |

Median occasion width on anchored frames is 1.5 h. The oracle measurement is unchanged
(16/16, 20/20, 34/35 inside; median width 3.6 h in the first row, up from 3.3 h). The one
remaining miss on `00007037` is a genuine verification error: frame 8 was matched at
confidence 0.50 to a photo six hours earlier on the same day, and the solver kept it because
it did not contradict the neighbours. That is the case a confidence threshold above 0.5, or
the outing pass (COO-119), is for — every wrong-session accept in these runs sits at 0.38-0.70.

## COO-146 — retrieval on genuine anchors: K, cap, calibration

Measured 3 September 2026 with `scripts/sweep_retrieval.py` on cached SigLIP vectors only:
the 9 most recent hand-tagged rolls (`00007037`-`00007045`), `.clean()`, window = each
roll's true range ±2 days, events from `events.segment()`, scored on the **35 frames anchored
to a phone photo** (`Roll.anchored(library.phone_times(assets))`). No embedding, no API
spend. **n = 35; the 95% confidence interval is about ±16 points**, so every number below is a
direction, not a measurement.

Two hit definitions side by side: **exact** — a shown candidate is within 2 s of the frame's
true time, i.e. the very photo the user copied the timestamp from, which is what "anchored
frames exact" needs; and **30 min** — M1's `SAME_MOMENT`, any photo on the same occasion.

One property of the ground truth matters for reading the exact column. Lightroom spaces a
tagged group one second apart, so consecutive anchored frames share one phone photo and at
most one of them depicts it: the 35 frames rest on **14 distinct anchor photos** (per roll:
1, 1, 1, 2, 3, 1, 3, 2). Three of the 14 are Leica Q3 shots rather than iPhone photos.

### The grid

recall@K, exact photo, SigLIP, per-event cap:

| cap | @6 | @8 | @12 | @16 | @24 | @32 |
|---|---|---|---|---|---|---|
| 1 | 5.7 | 5.7 | 8.6 | 11.4 | 11.4 | 11.4 |
| 2 | 5.7 | 8.6 | 8.6 | 8.6 | 14.3 | 14.3 |
| 3 | 5.7 | 5.7 | 8.6 | 8.6 | 8.6 | 14.3 |
| 5 | 2.9 | 5.7 | 8.6 | 8.6 | 8.6 | 8.6 |
| none | 8.6 | 8.6 | 20.0 | 20.0 | 28.6 | **37.1** |

recall@K, same occasion (30 min):

| cap | @6 | @8 | @12 | @16 | @24 | @32 |
|---|---|---|---|---|---|---|
| **1** | 60.0 | **65.7** | **74.3** | **77.1** | **88.6** | **88.6** |
| 2 | 62.9 | 62.9 | 68.6 | 71.4 | 82.9 | 82.9 |
| 3 (current) | 60.0 | 62.9 | 62.9 | 65.7 | 77.1 | 82.9 |
| 5 | 57.1 | 60.0 | 62.9 | 62.9 | 68.6 | 74.3 |
| none | 57.1 | 57.1 | 60.0 | 62.9 | 62.9 | 62.9 |

recall@K by distinct anchor photo (n = 14; a hit if *any* frame of the group retrieves it):

| cap | @6 | @8 | @12 | @16 | @24 | @32 |
|---|---|---|---|---|---|---|
| 1 | 14.3 | 14.3 | 14.3 | 14.3 | 14.3 | 14.3 |
| 3 | 14.3 | 14.3 | 21.4 | 21.4 | 21.4 | 21.4 |
| none | 21.4 | 21.4 | 35.7 | 35.7 | 42.9 | 57.1 |

Per roll, exact, hits / anchored (the 30-min column is cap 3 @8, from `sweep_m1.py`):

| roll | anchored | anchor photos | exact @6 cap 3 | exact @8 cap 3 | exact @8 none | 30 min @8 cap 3 |
|---|---|---|---|---|---|---|
| 00007043 | 2 | 1 | 0 | 0 | 0 | 100% |
| 00007040 | 5 | 1 | 1 | 1 | 0 | 20% |
| 00007039 | 2 | 1 | 0 | 0 | 1 | 100% |
| 00007044 | 4 | 2 | 0 | 0 | 0 | 50% |
| 00007038 | 8 | 3 | 0 | 0 | 1 | 87.5% |
| 00007041 | 1 | 1 | 1 | 1 | 1 | 100% |
| 00007042 | 8 | 3 | 0 | 0 | 0 | 25% |
| 00007037 | 5 | 2 | 0 | 0 | 0 | 100% |

Three things the grid says:

* **The exact photo is not a K or cap problem.** At K ≤ 8 no cap reaches 9%; the only way
  up is *uncapped* K = 32 (37% of frames, 57% of anchor photos), which costs $6.72 a roll and
  drops occasion-level recall from 83-89% to 63%. The two definitions pull the cap in opposite
  directions: the exact photo wants no cap (it is buried inside its own event), the occasion
  wants cap 1 (slots spent on a second photo of the same event are wasted).
* **On the honest set the cap direction reverses M1's.** M1 found cap 3 beat no cap; here
  cap 1 ≥ cap 2 ≥ cap 3 ≥ cap 5 ≥ none at every K for the occasion definition, though the
  gaps are 1-4 frames of 35.
* **Raising K buys occasions, not instants.** Cap 1: 66% @8, 74% @12, 89% @24.

### Miss analysis at K = 6, cap 3

33 of 35 frames miss the exact photo. Plain-similarity rank of the true photo: median
**118**, rank 1 on one frame, ≤ 6 on three, ≤ 32 on 13, worst 869.

| kind | frames | median true rank | note |
|---|---|---|---|
| wrong photo within the right event | 19 | 27 | every one is a 30-min hit; median event size 44 |
| event missing entirely | 14 | 310 | ranks 51-869; none a 30-min hit |

The 14 "event missing" frames are 4 on `00007040` (newborn at home), 6 on `00007042`, 2 on
`00007044`, and one each on `00007037` and `00007038` — the domestic-repetition rolls M1 named.

The load-bearing number is the true photo's rank **within its own event**: median 10, top-1
on 4 of 35, top-6 on 15. The frame's similarity to its exact counterpart (median 0.771) is
*lower* than to the best photo of its own event (0.829) and to the best photo of some other
event (0.857). SigLIP finds the scene, not the shot: the phone photo the user copied a
timestamp from is often a different framing, subject or moment from the film frame, and on
similarity it is one of the crowd. A two-stage scheme (find the event at K with cap 1, then
show the event's top-N by similarity) would reach 26% exact at K = 12 / N = 6 and 46% at
K = 24 / N = 10 — better than any single list, still not a majority.

Consequence for the design: **"anchored frames exact" is not reachable through retrieval on
this ground truth**, and the follow-up filed under COO-120 (an anchored frame's interval is
the anchor's *event span*, with the photo's time still the written value) is the honest
representation. The exact second is what the user copies by hand; the tool should promise the
occasion.

### Calibration

P(true | similarity) by grid search (centre 0.50-0.99 step 0.01, slope in
{5, 10, 15, 20, 30, 40, 60}), positives = the exact photo, negatives = the best photo of any
other event:

| | median | range |
|---|---|---|
| exact photo | 0.771 | 0.471-0.924 |
| best photo of another event | 0.857 | 0.632-0.907 |
| pool | 0.668 | |

The exact photo beats the best other-event photo on **5 of 35** frames. The fit is
**centre 0.80, slope 5** — slope at the grid floor, cross-entropy 0.841 against 0.693 for a
constant predictor, i.e. *no* increasing logistic on similarity separates the exact photo
from a competitor. The current `AlignParams` (centre 0.88, slope 10, fitted on the 113
contaminated frames where the "true photo" was usually the frame's own copy at 0.948) scores
1.104 here.

At the event level, which is what `AlignParams.calibrate` is actually applied to (best
similarity per event, `build_emissions`): true event's best median 0.829, best other event's
0.857, the true event wins 18 of 35. Fit vs the best other event: centre 0.83, slope 5
(0.741, again worse than constant 0.693). Fit vs *every* other event (35 positives, 2,623
negatives): centre **0.95, slope 20** (0.050 vs 0.070 constant) — a real but small gain that
mostly encodes the prior that an event is one of ~70.

**Not applied.** The three fits disagree (0.80/5, 0.83/5, 0.95/20), two are worse than a
constant, and the one that helps would push every event with best similarity below ~0.80 to
the `event_floor`, which changes the balance against gap and outside states that the COO-114
and COO-118 tables were measured with. The refit belongs in COO-140, at the event level, with
the solver re-measured (`scripts/align_m2.py`) in the same change.

### Alternative ranking: events by margin

Top-1 per event, events ranked by (best − runner-up similarity; a single-photo event's
runner-up taken as the frame's pool median): **exact 0.0% at @6 and @8; 30 min 17.1% / 20.0%**
against 60.0% / 65.7% for plain similarity with cap 1. Margin rewards small events with one
odd photo, not exact matches. Rejected.

### Cost

$0.035 a frame at K = 6 on `claude-opus-5` (M1), linear in images shown, 36-frame roll:

| K | $/frame | $/roll | occasion recall, cap 1 | exact recall, none |
|---|---|---|---|---|
| 6 | 0.035 | 1.26 | 60.0% | 8.6% |
| 8 | 0.047 | 1.68 | 65.7% | 8.6% |
| 12 | 0.070 | 2.52 | 74.3% | 20.0% |
| 16 | 0.093 | 3.36 | 77.1% | 20.0% |
| 24 | 0.140 | 5.04 | 88.6% | 28.6% |
| 32 | 0.187 | 6.72 | 88.6% | 37.1% |

### Recommendation

**K = 12 with cap 1** for the list that feeds verification: 74% of occasions for $2.52 a
roll, against 63% for $1.68 at today's K = 8 / cap 3; K = 24 / cap 1 reaches 89% for $5.04
if a roll is worth it. Do not chase the exact photo with K: nothing under $6.72 a roll gets
it for more than a fifth of frames, and the event-span interval is the right output anyway.

**Nothing in `config.py` or `AlignParams` was changed**, because the recommendation is not
unambiguous at n = 35: the cap 1 vs cap 3 gap is 1 frame at K = 8 and 4 at K = 12, inside
±16 points; the two hit definitions want opposite caps; and whether Claude's precision holds
when it sees one photo per event instead of three is unmeasured — the "same session, same
sofa" evidence it cites in the wrong-day accepts is exactly what a second in-event photo
feeds. Note also that `filmgeo verify` has its own default of `k=6` in `cli.py`, so `TOP_K`
alone would not change what Claude sees. What settles it: re-run `filmgeo verify` on
`00007037` and `00007044` with the clean pool at K = 12 / cap 1 (about $3.30) and score
occasion-level precision against the existing K = 6 / cap 3 verdicts; if precision holds,
change `TOP_K` to 12, `MAX_PER_EVENT` to 1, and the `verify` default together.

### Grayscale — worth one run; border trim is not implemented

The exact photo sits *below* its own event's neighbours on similarity, which is where a
film-vs-phone colour gap would show. Grayscale is implemented (`Embedder.grayscale`,
variant `siglip_gray` in `scripts/eval_m1.py`) and costs GPU minutes, not money. It needs
Photos derivatives, so it runs from Terminal.app, not a tool call:

```bash
cd ~/GitHub/FilmGeotagger
uv run --extra embed python scripts/eval_m1.py --rolls 9 --variants siglip_gray   # embeds frames + ~8,000 pool photos into .filmgeo/vectors/siglip_gray; tens of minutes on MPS
uv run python scripts/sweep_retrieval.py --variant siglip_gray                     # seconds; compare the two grids above
```

Border trim exists only in PLAN.md (under `scans/ingest.py`); nothing in `src/` crops a scan
before embedding, so there is no command to give. If grayscale moves the within-event rank,
trim is the next variant to add (a fixed-fraction crop in `Embedder._load`, cached under its
own variant name); if grayscale does nothing, the gap is content, not colour, and trim is
unlikely to help either.

## COO-119 and the K = 12 re-run — what $3.55 of verdicts settled

Run 4 September 2026 from Terminal.app: `filmgeo outing` on both verified rolls ($0.30) and
`filmgeo verify --k 12 --cap 1` on both, under `-k12` aliases so the K = 6 / cap 3 verdicts
stayed for comparison ($3.25).

### K = 12 with one photo per event is the better shortlist

| roll | shortlist | accepted | ≤ 30 min | same day | wrong day | at conf ≥ 0.8: accepted / same day |
|---|---|---|---|---|---|---|
| `00007037` | K 6 / cap 3 | 20 / 37 | 13 | 17 | 3 | 12 / 12 |
| `00007037` | **K 12 / cap 1** | 21 / 37 | 13 | 19 | **2** | 14 / 14 |
| `00007044` | K 6 / cap 3 | 8 / 10 | 2 | 5 | 3 | 5 / 5 |
| `00007044` | **K 12 / cap 1** | 7 / 10 | 3 | 6 | **1** | 4 / 4 |

Wrong-day accepts fall from 6 of 28 to 3 of 28; every accept at ≥ 0.8 is on the right day in
both settings. Aligned, the ten-frame roll goes from 4 to 6 anchored frames and from 6 to 8
frames with a pin; the 22-day roll is unchanged (13 anchored, 36 of 37 inside). The exact
photo reaches the shortlist no more often (COO-146 predicted that), and Claude's precision
does not fall when it sees one photo per event, which was the open question. **Defaults are
now `TOP_K = 12`, `MAX_PER_EVENT = 1`, `verify --k 12 --cap 1`, `MAX_CANDIDATES = 12`**:
$2.52 a roll instead of $1.68. n is two rolls; the direction agrees with the 35-frame grid.

### The outing pass groups well and the transition bonus does nothing useful

Claude's groups are good descriptions — "grandparents visit: man in a mint polo holding the
baby on the green couch", "spring walk: Manhattan skyline over the harbour with cyclists at
the railing" — 15 outings on the 22-day roll, 6 on the ten-frame roll, no out-of-sequence
flags, and it isolated frame 8 of `00007037` (the one wrong-session anchor) as a group of
its own at 0.70, which is the right call. The bonus fed by those groups, though, changed
nothing that matters and one thing for the worse:

| roll | shortlist | bonus | anchored | interpolated inside | median error of interpolated frames |
|---|---|---|---|---|---|
| `00007037` | K 6 | off | 13 | 24 / 24 | 1.7 h |
| `00007037` | K 6 | on (22 pairs) | 13 | 24 / 24 | **14.8 h** |
| `00007044` | K 12 | off | 6 | 4 / 4 | 44.6 h |
| `00007044` | K 12 | on (4 pairs) | 5 | 5 / 5 | 70.1 h |

The reason is what the groups *are* on these rolls: on a newborn-at-home roll the outings
are "who is holding the baby", which is true and says nothing about which day. A pairwise
"stay in the same event" bonus then pulls frames toward their neighbour's event rather than
toward the day the evidence favours. So `outing_bonus` defaults to 0 and the pass is kept for
the review UI (the descriptions are exactly what a person needs to confirm a group) and for
the out-of-sequence flag. The right use of the groups is as a *joint* constraint — every
frame in a group shares one day — solved by mapping groups onto days rather than nudging
frame pairs; that is COO-147, and it is where the "a few outings onto days instead of 36
frames onto weeks" idea in PLAN.md actually lives.
