# M5 — more signals and robustness: findings

## COO-147 — outing groups as a joint day constraint

Landed 5 September 2026. `align/model.py` (`AlignParams.outing_day_penalty`, gap states cut
at midnight, `RollModel.same_day`), `align/solve.py` (`_pair_transitions`), 93 tests.

### The constraint

COO-119 measured the pairwise "stay in the same event" bonus as harmful (1.7 h → 14.8 h on
the 22-day roll) and turned it off, keeping Claude's outing groups only for their
descriptions. The idea PLAN.md actually had — a few outings onto days instead of 36 frames
onto weeks — is a *joint* constraint: every frame in a group is on one calendar day.

In a first-order chain that is expressible pairwise, exactly, as long as no state spans two
days: for two consecutive frames in one outing, every transition between states whose
calendar-day ranges do not meet costs `outing_day_penalty` (6, about 400:1). It is a strong
finite penalty rather than −∞ so a user lock that contradicts a group still solves — the
group loses, visibly — instead of leaving no path.

The first version leaked: a week-long gap state "covers" both ends of the week, so a frame
parked in it satisfied both hops and the group straddled seven days. **Gap states are now cut
at local midnight**, one per calendar day. That makes the day constraint exact, and gives the
posterior — and the best-days summary in the roll header — a day's resolution inside long
silences. Anchors keep their occasion span; an event that crosses midnight still counts as
either day, which is what it is.

### Measured

Same verdicts, facts and outing groups as before; nothing else changed. `no outings` and
`penalty 0` are identical, which is the COO-119 baseline.

| roll | groups → pairs | | anchored | right day, all frames | truth inside | interpolated median error | median interval width |
|---|---|---|---|---|---|---|---|
| `00007044`, K 12 | 6 → 4 | before | 6 | 7 / 10 | 10 / 10 | **44.6 h** | 1.8 h |
| | | after | 6 | **9 / 10** | 10 / 10 | **0.2 h** | 1.8 h |
| `00007037`, K 12 | 15 → 22 | before | 11 | 29 / 37 | 36 / 37 | 1.5 h | **50.6 h** |
| | | after | 11 | 29 / 37 | 36 / 37 | 1.5 h | **21.8 h** |
| `00007037`, K 6 | 15 → 22 | before | 11 | 28 / 37 | 36 / 37 | 1.5 h | **63.5 h** |
| | | after | 11 | 29 / 37 | 36 / 37 | 1.5 h | **41.7 h** |

Penalties of 3, 6 and 12 give the same answers on all three (6 is the default). The oracle
measurement (`scripts/align_m2.py --mode oracle`, no outings) is unchanged at 16 / 16 inside,
median width 3.9 h, and the no-anchor mode at 34 / 35, so the midnight split changed nothing
where there are no groups.

What the numbers say: where the bonus had pulled frames toward their neighbour's *event*, the
day constraint only forbids the one thing an outing rules out — a different day — and lets
similarity and the anchors place the frames inside it. On the ten-frame roll the two
interpolated frames that had drifted two days from their outing's anchored members snapped
onto the right day; on the 22-day roll nothing moved that was already right, and the
intervals of the interpolated frames halved because a frame grouped with an anchored one now
inherits that day. Containment held at every step, which was the criterion.

The one wrong-day frame left on `00007044` and the eight on `00007037` are frames whose
groups hold no anchored member — a constraint has nothing to bind them to. That is what
`filmgeo verify --inside` (COO-149) is for.

## COO-148 — the grayscale second stage

Landed 5 September 2026. `pipeline.exact_ranking`, `possible_variant` in the API, the
occasion strip's wording, 94 tests.

### What it does

Once a verdict has named a frame's occasion, the photos of that occasion are re-ranked by
grayscale SigLIP similarity to the frame (`siglip_gray`, the variant COO-146 measured) and
offered as the frame's "possible photos" — the exact shot, if it exists, should sit near the
top. It runs only when every vector needed is already cached, because it must never try to
embed Photos derivatives from a shell that cannot read them; otherwise the occasion is shown
in colour and the roll says which (`exact_variant`). No API calls either way.

### Measured

On the 35 honest anchored frames (`Roll.anchored` against `library.phone_times`), with the
*true* event given — the ceiling of any second stage — where the exact photo ranks inside its
event (median event size 29 photos):

| ranking | median rank | top-1 | top-3 | top-6 | top-8 | top-12 |
|---|---|---|---|---|---|---|
| colour | 10 | 3 | 7 | 15 | 17 | 19 |
| **grayscale** | **7** | 4 | **12** | 17 | 19 | 20 |

Smaller than COO-146's quick read (median 5), which used the sweep's event assignment; this
is the pipeline's own. The gain is in the top three — 12 of 35 against 7 — which is what a
person scanning a strip of twelve actually looks at.

On the two verified rolls the stage has little to bite on: of 17 anchored frames the exact
photo is in the occasion list for one (frame 26 of `00007037`, ranked 4th). The rest are
either frames whose hand-tagged time is a Lightroom group time with no photo behind it, or
frames whose verdict named a neighbouring occasion. The second stage inherits the first
stage's occasion recall (74% at K = 12, COO-146) and cannot repair it; it makes the last
click cheaper when the occasion is right.

The UI says which ranking it is showing ("ranked in grayscale to find the exact shot — form,
not colour") and the question for an anchored frame points down to it.

## COO-132 — Apple Health workout routes

Landed 5 September 2026. `signals/health_routes.py`, `PhotosTrail.offset_at`, wired into
`trail_for` and `filmgeo signals`; 97 tests.

A Health export's `workout-routes/*.gpx` files are the only thing in that export that locates
the user: one track point a second, in UTC, for every walk or ride that recorded a route. The
adapter opens only the files whose name-date falls inside the window (±1 day), subsamples to a
point a minute, borrows the UTC offset from the nearest phone photo *by instant* (a new
`PhotosTrail.offset_at`; the NFC log borrows by wall clock because its times have no zone), and
emits `health` trail points labelled with the route's name. No constraints.

Drop the export folder, or just `workout-routes/`, under `.filmgeo/signals/health/` and every
roll picks it up; `filmgeo signals <roll>` reports how many routes lie in the window, or says
where to put them. Not measured on a real export — none is on this Mac — but this is the case
the trail has been blind to: a walk with the film camera and no phone photo left the frames in
a gap with an unknown place; a route puts a point every minute along it.

Also on this Mac, in `~/Downloads`: a Foursquare/Swarm data export (`checkins*.json`). That is
COO-144's input and the next adapter.

## COO-144 — Foursquare / Swarm: named check-ins and passive visits

Landed 5 September 2026. `signals/swarm.py`, wired into `trail_for` and `filmgeo signals`;
99 tests. The user's own export was in `~/Downloads` (`data-export-29125`): 14,376 check-ins
since 2009 and 17,536 passive visits since 2018.

### What the export holds, measured

* `checkins<N>.json`: `createdAt` is a `YYYY-MM-DD HH:MM:SS.ffffff` string **in UTC**, with
  `timeZoneOffset` in minutes east of UTC. Settled against the Photos library: of 154 check-ins
  in 2026 with a phone photo within 300 m, reading the string as UTC puts the nearest photo a
  median 6 minutes away (119 of 154 within half an hour); reading it as local wall clock puts it
  4 hours away. Each carries `lat`/`lng` and `venue.name`.
* `visits.json`: Swarm's background location — `timeArrived`/`timeDeparted` in the same UTC
  format, coordinates, `city`, `locationType` in {`Venue` 13,018, `Home` 3,918, `Work` 600};
  median stay 0.9 h, 90th percentile 18.5 h. April 2026 alone has 152 visits, 65 of them
  `Home`. No zone offset; the adapter borrows the nearest check-in's within a day, else the
  photo trail's by instant.

### The adapter

Check-ins become `swarm` trail points labelled with the venue; visits become `visit` points at
arrival, at departure and every half hour between, labelled with the type and city. Nothing
here is a constraint — a check-in is where the user was, not where a frame was — and nothing
here changes the solver: the trail feeds location, clusters and offsets (`geo.place`).

### Effect on the verified rolls

| roll | | ambiguous → ok | GPS error where ok | ambiguous frames whose top cluster is right | clusters carrying a name |
|---|---|---|---|---|---|
| `00007037`, K 12 | without | 13 / 24 | 0.02 km, 22 / 24 within 500 m | 5 / 12 | 0 |
| | with Swarm | 13 / 24 | 0.02 km, 22 / 24 | **7 / 12** | **57** |
| `00007044`, K 12 | without | 0 / 10 | 0.03 km, 10 / 10 | — | 0 |
| | with Swarm | 0 / 10 | 0.03 km, 10 / 10 | — | 0 |

Coverage does not move: the ambiguous frames on the 22-day roll have intervals that span both
the flat and the walks, and more points inside those intervals sharpen the clusters without
collapsing them to one. What the export buys is names on the choices — "787 Coffee Co.",
"Brooklyn Heights Promenade", "Home: Brooklyn" — which is what a person needs to pick a cluster
in one glance, and the top cluster is right two frames more often. Offsets are unchanged
(37 / 37 and 10 / 10). The at-home ambiguity is not solved, as the issue predicted; the
`Home` visits will matter on a roll whose frames *are* at home and whose photo trail says
otherwise.

To use it: copy the export folder to `.filmgeo/signals/swarm/`; `filmgeo signals <roll>`
confirms how many check-ins and visits it found.

## COO-133 — Google Maps Timeline

Landed 5 September 2026. `signals/timeline.py`, wired into `trail_for` and `filmgeo signals`;
102 tests. Not measured — no Timeline export on this Mac; the fixtures follow Google's field
names for each format.

Three shapes, one adapter: the current on-device `Timeline.json` (`semanticSegments` with
paths, visits and activities, `rawSignals` position fixes, `frequentPlaces` labels; times
carry their own offset, so the zone comes free), the legacy Takeout `Records.json`
(`latitudeE7`/`longitudeE7`, UTC), and the legacy `Semantic Location History` month files
(`placeVisit` with a **name**, `activitySegment` with waypoints). All yield `timeline` points
along paths and fixes, subsampled to one a minute and dropped when the fix is coarser than
200 m, and `visit` points at a stay's start, end and every half hour between, labelled with
the place name or its type (`Home`, `Work`, `Walking`). UTC-only stamps borrow the photo
trail's offset by instant. Files that are none of the three shapes are ignored.

Drop the export under `.filmgeo/signals/timeline/`. When one exists, the same location
coverage measurement as COO-144 (ambiguous → ok, top cluster right, names on clusters) is the
test; the dense path points should be what turns "ambiguous" into "ok" for frames between
outings, which the sparse Swarm data could not do.

## COO-136 — historical weather: measured, and turned off

Landed 5 September 2026 with the term **off**. `signals/weather.py` (Open-Meteo archive, one
request per 0.1° cell and day, cached under `.filmgeo/weather/`), `FrameClues.weather`,
`AlignParams.weather_penalty`, `RollInputs.event_weather`; 106 tests. Fetching is opt-in
(`FILMGEO_WEATHER=1`).

### The idea, and the two numbers that killed it

Claude reads `weather` off the outdoor frames (`clear | overcast | rain | snow | fog`; 27 of 104
verdicts carry one). The archive knows the sky at every event's place and hour. An event whose
sky contradicts the clue should be a slightly worse home for the frame — the same nudge
`time_of_day` already gives.

**First number: is the clue right?** At the hand-tagged time and place of the 25 outdoor frames
with both a clue and a true pin (`Roll` frames carry the user's GPS):

| | frames |
|---|---|
| clue and archive agree | 14 |
| compatible (archive says `mixed` or `fog`) | 3 |
| **contradict** | **8** |

The eight are frames 16–19 of `00007037` on both shortlists — the "spring walk: Manhattan
skyline over the harbour" outing, read as `clear` on film, which the reanalysis has at
overcast for that hour and cell. Either the harbour had a hole in the cloud or the 10 km grid
did not; a film frame's sky is a small patch of a coarse field, and one bright outing is
enough to be wrong a third of the time.

**Second number: does the term help anyway?** Same verdicts, facts and groups as COO-147;
150 of 162 events had an archive reading:

| roll | penalty | anchored | right day | inside | interpolated median error |
|---|---|---|---|---|---|
| `00007037`, K 12 | 0 | 11 | 29 / 37 | 36 / 37 | 1.5 h |
| | 1.5 or 3 | 11 | **25 / 37** | 35 / 37 | **8.3 h** |
| `00007037`, K 6 | 0 | 11 | 29 / 37 | 36 / 37 | 1.5 h |
| | 1.5 or 3 | 11 | **25 / 37** | 36 / 37 | **8.3 h** |
| `00007044`, K 12 | any | 6 | 9 / 10 | 10 / 10 | 0.2 h (no weather clues on this roll) |

The penalty pushes the four walk frames off their true day toward a clearer one. It cannot
tell a wrong clue from a wrong event, and here the clue was wrong. So `weather_penalty`
defaults to 0 and the fetcher to off; both stay for the next measurement, when more rolls
with outdoor frames exist. The "explanation in the UI" half of the issue — showing the
archive's sky beside Claude's clue — is not built: with a third of the clues disagreeing it
would explain the wrong thing as often as the right one.

This is the second emission term in a row (after COO-119's outing bonus) to measure harmful
and ship off. The pattern is the same: a plausible nudge that pulls frames toward a
*neighbour's* or a *record's* opinion instead of the day the anchors favour. The constraints
that have worked — monotone order, facts, joint days — remove impossibilities rather than
add opinions.
