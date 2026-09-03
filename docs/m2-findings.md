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
SigLIP vectors, no API calls. Scored on held-out anchored frames only:

| anchors given | held-out frames | truth inside 90% interval | median abs. error | median width |
|---|---|---|---|---|
| every other anchored frame | 54 | **54 / 54** | 23.6 h | 72.5 h |
| first and last only | 97 | **97 / 97** | 73.3 h | 176 h |
| none — similarity, events, order | 113 | **112 / 113** | 72.0 h | 199 h |

This is the M2 exit criterion ("anchored frames exact; interpolated intervals contain the true
time") met on the hand-tagged rolls. Two caveats that the numbers carry:

* **The interval test uses a two-minute tolerance.** Without it, six of 54 held-out frames
  fall "outside" by one second: the user tagged groups of frames a second apart in Lightroom,
  not always in scan order, so a frame between two oracle anchors one second apart can sit a
  second past its clipped interval. That is the ground truth's granularity, not the solver's.
* **The intervals are wide, and honestly so.** With only the ends anchored, the median
  interval is a week; the roll that lived in the camera for 22 days (`00007037`) reports three
  weeks. This is the "between Tue 14:05 and Wed 17:40" output M1 argued for; the width is what
  verification anchors and outing groups (COO-119) exist to shrink, and it is now measurable.

The proposal beats the all-gap null path by 39-149 log units on every roll, and mean posterior
mass on `outside` is 0.001-0.031, so the right-window case is clearly separable — the
wrong-window half of COO-118 still needs the deliberately-wrong-month run.

One roll (`00007044`) ran under its facts window, the whole of April with 2,428 photos in 182
events, and gave 371 states: solving takes well under a second, so the month-wide windows
users will actually type are not a performance concern.
