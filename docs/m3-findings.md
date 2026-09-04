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
