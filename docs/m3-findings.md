# M3 — review UI: findings

Per-issue notes for the review UI milestone. Linear is the status; this is what was learned.

## COO-121 — the local API, thumbnails, `filmgeo serve`

Landed 4 September 2026. `src/filmgeo/api/{app,state,thumbs}.py`, `align/overrides.py`, a
`pipeline.resolve` that re-solves from cached data, `filmgeo serve`, 18 API tests (73 in the
suite). Optional dependency group `api` (fastapi, uvicorn, pydantic); `httpx` under `dev`.

### Shape

FastAPI on 127.0.0.1, routes under `/api` so a web build can own `/`:

| route | does |
|---|---|
| `GET /api/rolls` | every roll known: registered on the command line, on disk under `.filmgeo/`, or hand-tagged in the library |
| `GET /api/rolls/{key}` | header: window and its source, checks, events, facts, outings, counts |
| `GET /api/rolls/{key}/frames[/{n}]` | assignment, interval text, candidates with similarity and verdict, Claude's evidence and clues, the override, the frame fact, image URLs |
| `PUT /api/rolls/{key}/frames/{n}/assign` | one user decision; re-solves and returns every frame, because neighbours move |
| `GET/PUT /api/rolls/{key}/facts` | the facts file; a moved window rebuilds the pool |
| `POST /api/rolls/{key}/realign` | from disk again (new verdicts), or `{"widen": true}` |
| `GET /api/rolls/{key}/frames/{n}/image`, `GET /api/photos/{uuid}/image` | `?size=small` (240 px) or `large` (1024 px) |

PLAN.md wrote `/frames/{id}/assign`; there is no global frame id because there is no
database — the assignments file per roll is the table — so frames live under their roll.

**State.** The store keeps one solved `RollRun` per roll in memory. A run is expensive to build
(library cache, pool vectors, similarities: seconds) and cheap to re-solve (milliseconds), so
an override or a fact re-solves in place through `pipeline.resolve`, which raises
`WindowChanged` when the facts window moved and the pool has to be rebuilt. Every successful
re-solve rewrites the same files the CLI uses — facts, overrides, assignments — so a roll
reviewed in the browser is the roll `filmgeo align` reports on and the one M4 will write.
Nothing is written when the solver refuses (409): a lock that contradicts scan order, a fact
that leaves a frame no state.

**Overrides vs facts.** A fact is a statement about the world (frame 12 was on 4 July, at this
pin) and lives in the facts file the CLI already writes; the UI's "type a time", "drag the
pin", "skip" are facts. An override is a decision about the *matching* — this is the photo,
that is not a match, no phone photo shows this — and lives in `.filmgeo/overrides/<roll>.json`.
Overrides edit verification's anchors before the solver sees them; a pick becomes a locked
anchor at confidence 1. `confirmed` sits there too, for COO-126 to set in bulk. `unlock` drops
both the override and the frame's facts.

**Thumbnails** are cached under `.filmgeo/thumbs/<size>/<key>.jpg` keyed by the same content
key the vector cache uses (a scan's hash, a photo's uuid), so one thumbnail serves every roll
that shows the photo and survives a re-run. Photos derivatives are unreadable from sandboxed
shells; the API answers 403 with the Terminal.app instruction rather than a 500.

### The engine bug the API exposed: two anchors in one event, out of order

The first lock test through the API put frame 2 on a photo *after* frame 4's photo in the
same event and got a 200 with non-monotone times. The written times on the 22-day roll were
already non-monotone in two places (frames 13/14 and 27/28 of `00007037`), which PLAN.md's
"strictly increasing with ≥ 2 s spacing" forbids — `_assign_times` only moves unanchored
frames, and the anchors themselves were reversed.

Cause: `build_transitions` let an anchor step *down* in rank into its own event state so
that later frames could stay in the event. A chain anchor (13:00) → event → anchor (12:40)
was therefore legal. Two verdicts on the same occasion pointing at photos seconds apart in the
wrong order — common, since Lightroom spaces a tagged group a second apart and Claude picks a
same-session photo minutes off (COO-120) — walked straight through it.

Fix (`align/model.py`): the exception is gone and rank is strictly monotone. Every anchor gets
a **tail**: an event state over the anchor's occasion, ranked directly after the anchor and
open only to the frames after it. An anchor on an event's *first* photo also gets a **head**,
ranked directly before it and open only to earlier frames, because there the event state ranks
above the anchor and could not serve them; anywhere else the event state already does, and a
second state over the same occasion would double-count "on the occasion, not at that second"
in the posterior. Anchors on one instant are ordered by frame within the instant, so a burst of
frames on one photo keeps all its anchors.

Two things followed:

* **Confidence is the mass on the occasion**, not on the chosen state (`solve.py`). Once a
  neighbour's tail offers "same occasion, not that second" as a cheap alternative, the anchor
  state alone loses mass it never deserved to hold: median anchored confidence would have
  fallen 0.57 → 0.32 on the 22-day roll. Summing the event state, every head and tail over
  it, and the frame's own anchors in it is what the verdict vouches for (COO-145) and what the
  UI's green/amber/red bar should mean.
* **The reverse-roll test can be asked to solve an infeasible model** — a lock on frame 4 and a
  date on frame 3 swap into a contradiction backwards — and now returns "not reversed" instead
  of raising.
* **Order wins over spacing.** A frame squeezed between two anchors on one instant (frames 3
  and 5 locked to the same photo, say) has no room for the 2 s spacing; the backward pass used
  to push it 2 s *before* the first anchor. It now sits on that instant with them, which is
  the truth PLAN.md already accepts for two anchors on one photo.

Measured, same verdicts and facts as before (`00007037` and `00007044`, both shortlists):

| roll | non-monotone pairs | anchored | truth inside interval | interpolated median error | median confidence, anchored / interpolated |
|---|---|---|---|---|---|
| `00007037`, K 12 | 2 → **0** | 13 → 11 | 36 / 37 → 36 / 37 | 1.7 h → **1.5 h** | 0.57 / 0.12 → **0.99** / 0.18 |
| `00007037`, K 6 | 2 → **0** | 13 → 11 | 36 / 37 → 36 / 37 | 1.7 h → **1.5 h** | 0.56 / 0.12 → **0.99** / 0.11 |
| `00007044`, K 12 | 0 | 6 → 6 | 10 / 10 | 44.6 h | 0.55 / 0.22 → **0.90** / 0.20 |
| `00007044`, K 6 | 0 | 4 → 4 | 10 / 10 | 4.5 h | 0.51 / 0.42 → **0.86** / 0.42 |

The two anchors lost on `00007037` are the two reversed pairs: frames 11-13 and 14-15 are
matched to photos of one burst seconds apart (all five hand-tagged 08:50), and frames 27 and
29 to two photos at 11:08 in the wrong order. Keeping both halves of a reversed pair was the
bug; the frames now sit on the occasion with the truth inside their interval. Frames with
confidence ≥ 0.8 go from 0 to 16 on the 22-day roll, which is the first time the bar has had
a green band. The oracle measurement (`scripts/align_m2.py --mode oracle`) is unchanged: 16 / 16
inside, median error 0.0 h, median width 3.6 → 3.9 h.

One trap met on the way: the Leica Q3 photos in the library carry their dates in UTC, so a
`%H:%M` print shows 12:50 for a photo taken at 08:50 -04:00. Aware datetimes compare
correctly; only the eye is fooled.

### What `filmgeo serve` needs

Run it from Terminal.app — Photos derivatives are unreadable from tool-call shells, and the
first request for a roll builds its run (library cache plus pool vectors, a few seconds). With
no web build under `web/dist`, `/` redirects to the OpenAPI page. Roll folders that have no
facts or assignments yet are not discoverable; name them on the command line
(`filmgeo serve ~/scans/roll-x`).

## COO-122 — the React app: filmstrip, frame detail, candidate strip

Landed 4 September 2026. `web/` (Vite 5, React 18, TypeScript, React Query 5), built by
`npm run build` into `web/dist`, which `filmgeo serve` mounts at `/`; `npm run dev` proxies
`/api` to the running server. Node was not on this Mac; `brew install node` put it there.

### What it is

One page per roll, chosen from a roll list (`#/rolls/<key>` in the hash so a reload keeps
its place). The look is a light table in a darkroom: warm near-black, paper-white type, one
safelight-amber accent, Fraunces for display and Instrument Sans / JetBrains Mono for data,
the filmstrip drawn with sprocket edges. Loud choices were avoided on purpose — the photos
are the content, and the tool is used for an evening at a time.

* **Filmstrip** in scan order: thumbnail, number, local time, a confidence bar coloured by
  band (green ≥ 0.8, amber 0.5–0.8, red below, blue when locked), badges as glyphs
  (⚓ anchored, ● locked, ~ interpolated, ? ambiguous place, ± offset disputed, ✓ confirmed).
* **Frame detail**: the scan beside the phone photo it was matched to (or an empty plate
  saying why not), the time editor, interval text, confidence, place or the offered clusters,
  the outing description, the hand-tagged time when the roll is an eval roll, and Claude's
  verdict — evidence sentence and clues — with the user's own decisions pinned next to it.
* **Candidate strip**: retrieval's shortlist with similarity, local time, distance from the
  assigned time, and what Claude said about each (match / seen, no / not shown), the chosen one
  and any rejected one marked. **"Use this photo's time and GPS"** on every card locks the
  frame to it; the whole roll re-solves and the strip updates in place.
* **Time editor**: the assigned local time and UTC offset, editable; "set time" sends a frame
  fact (`when`) in ISO-8601 with the chosen offset, which the API renders into the roll's zone
  to the minute. "unlock" drops the frame's override and facts.

Every write goes through `PUT …/assign`, which returns all frames; the query cache is
replaced wholesale so neighbours move with no second round trip. The roll header shows the
window, its source, the camera and film, counts, the "window doubtful" and "possibly
reverse-wound" flags, and a "re-solve" that re-reads verdicts from disk.

### Two things the UI found

* **Times must be rendered in the frame's own offset, not the photo's zone.** The API's
  `interval_text` is built server-side from the state's datetimes, whose zone is the photo's
  — for the Leica Q3 that is UTC (COO-121's trap), and on the synthetic fixture it showed
  08:50 beside a filmstrip saying 04:50. The UI now formats every time from the ISO instant
  and the frame's `tzoffset`, which is what will be written, and derives the interval text
  the same way; `interval_text` stays in the JSON for the report.
* **A typed time came back a minute late.** A fact "07:15" is the half-open minute
  [07:15, 07:16), and `_assign_times` clipped the midpoint to the *exclusive* end, so the
  frame read 07:16. The clip now stops a second short of the end.

Verified in the browser against the synthetic roll (real scan fixtures as frames, generated
photos as candidates): the interval reads in the frame's offset, "Use this photo's time and
GPS" locks a frame and squeezes its neighbours, "set time" produces the fact and locks. Map
and timeline (COO-123), keyboard and the remaining overrides (COO-124), facts editing
(COO-125) and batch confirm (COO-126) are the next issues; the right-hand column is reserved
for the map and timeline.

## COO-123 — map and timeline panes

Landed 4 September 2026. `web/src/components/{MapPane,Timeline}.tsx`, one new API route
(`GET …/frames/{n}/trail?pad_minutes=`), a user-pin rule in `geo.place`, 75 tests.

### Map

MapLibre GL on OpenFreeMap's key-free vector tiles (`liberty`), with a blank ground as the
fallback when the tile host is unreachable, so the pin, trail and clusters still draw offline.
Per frame: the pin (amber when the solver placed it, blue when the user did), draggable —
dropping it sends a place fact; the trail points inside the frame's interval, padded by
30 minutes, as dots with a popup; and for an ambiguous frame, the clusters the solver offers
as numbered discs with their photo counts, one click placing the frame at the cluster's
centroid with the cluster's spread as radius and its label as the place name. The viewport
fits whatever is drawn. Markers are DOM and never wait for the style: the first version
gated everything on `isStyleLoaded()`, which is false while tiles stream in even after `load`
has fired, so nothing ever appeared.

### Timeline

Two bands in one SVG. The top band is the whole window: events as bars (height by log photo
count, the current frame's event lit), every frame as a tick coloured by its confidence band,
the selected frame's interval as a translucent stripe, day gridlines. The bottom band zooms
to the selected frame's interval padded by a third, with hour ticks, frame numbers, and the
trail points as dots along the bottom. Hovering shows the instant under the cursor;
**clicking either band sets the frame's time by hand** — a `when` fact at that minute, in
the frame's own offset. Ticks are computed in the frame's local wall clock, so midnight lines
fall on midnight.

### The engine gap the map exposed

Clicking a cluster locked the frame (the place fact is a constraint) but left it
"ambiguous" with no pin: `geo.place` located frames from anchors and the trail only and knew
nothing about the user's pins. PLAN.md lists "known place (map pin or place name)" as a
per-frame fact, so a pin now *is* the frame's location (`location_source` "user") and
counts as an anchor for interpolating its neighbours, the same way a verified photo does.
The API passes the frame facts' places through as `pins`.

Verified in the browser on the synthetic roll: three clusters offered for an interpolated
frame between two days, one click placing it; a click on the zoomed band setting a frame to
the minute under the cursor; the trail endpoint's points inside the interval. Tiles could
not be seen rendering from the sandboxed browser pane, only the attribution they bring —
worth a look from Terminal.app.

## COO-124 — overrides, locking, live re-solve, keyboard

Landed 4 September 2026. `web/src/components/{PhotoBrowser,Keys}.tsx`, an action bar in
`FrameDetail`, a "not a match" on every candidate card, the keyboard layer in `RollPage`,
one new route (`GET …/photos?event=N | ?start=&end=`), two rules in the assign handler,
76 tests.

### Every decision, one round trip

Pick a candidate (button or `1`–`9`), pick **any** phone photo (click an event bar on the
timeline: its photos open in a grid, one click anchors the frame to it), type a time, click
the timeline, drag the pin or pick a cluster, "not a match" (`n`, or per card), "no
reference" (`N`), "unknown" (`x`), confirm (`Enter`, again to unconfirm), unlock (`u`).
Each goes through `PUT …/assign`; the API re-solves the roll and returns every frame, and
the filmstrip, timeline, map and neighbours update from that one response. `?` shows the
keys; keys are ignored while an input has focus.

### Two rules the keyboard made obvious

* **"Unknown" drops the pick.** A skip on a frame the user had already anchored did nothing:
  the locked anchor pruned every other state, and the uniform "skipped" emission had one
  state to be uniform over. The assign handler now clears the pick (and "no reference") when
  `skip` is set — "unknown" means no photo, not a locked one.
* **A confirmation is of an assignment.** Rejecting the chosen photo after confirming left
  the frame confirmed with a different answer. Any change to the matching or the facts now
  clears `confirmed` unless the same request sets it; the batch actions in COO-126 build on
  the same flag.

Also: unlock is offered whenever the frame carries any decision or fact (a rejection alone
does not pin the frame but is still the user's), not only when it is locked.

Verified in the browser on the synthetic roll: `j`/`k`, `2` (locks), `Enter` (confirms),
`n` (rejects, leaves the frame interpolated with the card marked), `u`, the photo browser
from an event bar and a pick from it, `x`, `?`.

## COO-125 — roll facts input

Landed 4 September 2026. `web/src/components/{FactsPanel,FrameFacts}.tsx`, a cost estimate
in the roll header, "same day as" propagation in `signals.base.frame_bounds`, 78 tests.

### What it is

A **roll facts** drawer under the header: the window as two periods in the facts syntax
(`2026-04`, `2026-04-12`, `2026-04-12 14:05`) and an optional zone, camera (with the three
bodies as suggestions), film, lab, notes, the reverse-scan toggle. "Save" writes the facts
file through `PUT …/facts`; when the window moved the button says so ("save and rebuild")
because the pool is rebuilt, which takes seconds rather than milliseconds. "Widen ±1 month
and re-run" writes a month more on each side into the window and rebuilds. Under the fields:
the window check (doubtful, and why), the best days by posterior mass, and **Claude so far**,
an estimate from the verdict file's K and the measured $0.035 per frame at K = 6, plus the
outing pass if one ran — an estimate until COO-140 logs tokens.

A **frame facts** form under the time editor: a known period (a day, a month, a minute), a
place name for the pin, "same day as frame N", a note. It goes through `assign` like every
other decision; the pin itself comes from the map.

### "Same day as" was recorded and ignored

The facts file, the CLI and the constraint carried `same_day_as` since COO-117, but nothing
in `build_emissions` read it. The per-frame case is now in `frame_bounds`: when either side
of the pair is dated, the other takes that local calendar day, chains included (7 → 5 → 3),
and the monotone propagation then tightens the frames between. Two undated frames that
merely share a day cannot be expressed as per-frame bounds in a first-order model; that is
the joint constraint COO-147 is for, and it is left alone rather than faked.

Verified in the browser on the synthetic roll: saving camera and film puts them in the
header; moving the window to 1–5 April rebuilds the pool, drops the day-9 anchor and raises
the doubtful-window warning; widening writes 1 March – 6 May; a frame fact of "2026-04-02"
locks the frame to that day.

## COO-126 — batch confirm

Landed 4 September 2026. `POST /api/rolls/{key}/confirm`, `web/src/components/BatchBar.tsx`,
shift-click ranges on the filmstrip, 79 tests.

A bar under the filmstrip: **all ≥ 0.8** (with the count it would add), **whole roll**,
**frames N–M** and **unconfirm N–M** for a range chosen by shift-clicking the filmstrip from
the selected frame, and **unconfirm all**. One route takes an explicit list, a confidence
floor, or neither for the whole roll, and the flag `confirmed` in the overrides file; a
skipped frame is never confirmed. The assignments file carries it as `status` per frame,
which is what M4's write step will require, and the roll list and header count it.

Confirmation is of an assignment (COO-124's rule), so changing one frame in a confirmed
range drops that frame's confirmation and no other. Confirming does not re-solve anything —
it goes through the same store update so the three files stay consistent, and it costs a
few milliseconds.

Verified in the browser on the synthetic roll: ≥ 0.8 confirms the two anchored frames,
shift-click selects 2–4, the range confirms and unconfirms, whole roll, unconfirm all, and
the header's count follows each step.

## M3 in one paragraph

The review UI exists end to end: `filmgeo serve` on 127.0.0.1 with the React app at `/`,
one page per roll — filmstrip, frame beside its photo, candidates, Claude's evidence, time
editor, frame facts, map with pin / trail / clusters, two-band timeline, roll facts drawer
with rebuild and widen, batch confirm, keyboard. Every decision is one `PUT …/assign` and
the whole roll re-solves in milliseconds from the cached run. Four engine gaps came out of
building it, each measured or tested before it was fixed: reversed anchors in one event
producing non-monotone times (the big one — confidence is now the mass on the occasion),
a fact minute's exclusive end being written, user pins ignored by `geo.place`, and
`same_day_as` recorded but never read. Nothing has been written to a scan file yet: that is
M4, and it starts from the assignments files these pages now keep consistent.

Still to look at from Terminal.app, since the sandboxed browser could not: the map tiles
over a real roll, and the Photos derivatives loading as thumbnails.

## COO-149 — candidates inside the interval, and a second verification round

From the first real review session (4 September 2026, `00007044-k12` in the browser):
frame 4 sits between frame 3 (anchored 4 April) and frame 5 (anchored 5 April), and its
candidate strip offered 21, 11, 30 and 9 April. The solver had placed it on 4 April — order
is respected — but the strip showed retrieval's month-wide shortlist, built before the
solve and ranked by similarity alone. That is also what Claude saw: **of the 12 photos it was
asked about for frame 4, none was inside the frame's interval.** Its "none" was correct and
useless.

### Possible photos

`pipeline.possible_candidates`: after the solve, each frame's top photos by the same cached
similarities, masked to its interval, one per event (the whole occasion for an anchored
frame, so the exact shot can be picked by hand). Milliseconds; re-computed on every
re-solve, so a fact — "frame 4 is the same day as frame 3" — narrows the list at once. In
the API as `possible` beside `candidates`.

On the real roll, for the four interpolated frames, the shortlist had 0, 1, 1 and 1 photos
inside the interval out of 12; the possible list has 4, 8, 8 and 8.

The strip now leads with these ("Possible photos, between frame 3 (4 Apr 10:01) and frame 5
(5 Apr 10:16), one per occasion") with "use this photo" on each; keys 1–9 pick from it. The
month-wide shortlist with Claude's verdicts is folded under "what Claude saw", with the ones
outside the stretch dimmed and counted. When nothing lies inside the stretch the strip says
so and points at the three ways out: same day as a neighbour, a typed time, no reference.

### The second round

`filmgeo verify --inside`: only the unanchored frames, each shown its possible photos. On
this roll that is 4 frames and 28 images, about $0.16, against $2.52 for the first round.
The loop the tool now supports is: verify, align, add what you know, `verify --inside`,
align, confirm. Not yet run on a real roll — it costs money and needs Terminal.app.
