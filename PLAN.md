# Film Roll Geotagger — Plan

## Context

The user shoots film on several cameras and gets scans back from the lab in batches of up to ~10 rolls: one folder per roll, sequential filenames, JPG (sometimes TIFF). Scans carry no capture metadata; their EXIF date is the scan date. The user wants exact capture date/time (with timezone offset) and GPS written into the scan files so that Lightroom (Local mode: reads and writes the files directly, no catalog) and Apple Photos place each frame on the timeline next to the iPhone photos from the same moment.

Today this is done by hand: find iPhone photos from the same day in Apple Photos, read their EXIF, copy it frame by frame in Lightroom, using visual clues (clothing, background, scene). With 10 rolls at a time this takes hours.

Intended outcome: a local tool that proposes date/time + GPS + confidence for every frame of every roll, lets the user review and correct, then writes the metadata into the files (plus provenance as keywords and a JSON sidecar), before the folder is opened in Lightroom.

### Decisions made during discovery

| Topic | Decision |
|---|---|
| Output target | Write EXIF + XMP into the scan files (JPG/TIFF). Lightroom Local and Apple Photos both read the file. |
| Run timing | Run on the roll folder before opening it in Lightroom. Never write while Lightroom has the folder open. |
| Cameras | Multiple bodies, all normal wind. Scan order == shooting order within a roll. Rolls from different bodies can overlap in time. |
| Reference data | Apple Photos library on the Mac, plus other signals (below). iPhone photos exist at the same scene "often, not always". |
| Date bounds | User knows a rough window per batch ("July", "the Portugal trip") and can supply known dates per roll or frame. |
| Precision | As exact as possible. Anchored frames take the matched photo's timestamp; every other frame carries an explicit uncertainty range. |
| Privacy | Cloud vision (Claude) is fine. |
| Photos storage | Optimize Mac Storage. Match on Photos' local derivatives (previews); never need originals. |
| Provenance | Keywords in the files AND a JSON sidecar per roll. Revisable later (reopen a roll, change, rewrite, or clear). |
| Form factor | Python engine as a separable core (CLI + local HTTP API) with a browser review UI. A native Mac app can later be a client or shell for the same engine. |
| Backlog | None. Only new rolls going forward, so in-place updates to assets already in Photos stay optional (M6). |
| NFC camera log | A Shortcut appends a line (time + location) to one Apple Note per scan. Ingest that note; recommend adding the camera name to the Shortcut. |
| Scan quirks | None known: frames arrive upright, unmirrored, cleanly numbered. Flip/rotate handling deferred until a real case appears. |
| Ambiguous GPS | Leave GPS blank and tag `filmgeo:location-unknown`; the UI offers the candidate places. Never invent a pin. |
| Plan delivery | Commit PLAN.md to the repo; create Linear milestones + issues; update the Linear project description. |

## Core idea: monotone alignment of an undated sequence onto a dated timeline

- A roll is an ordered, undated sequence of frames.
- The iPhone library (and other signals) form a dated, geotagged timeline.
- Any frame that visually matches a phone photo becomes an **anchor** (time, offset, GPS).
- Frames are monotone in time within a roll, so the best assignment is a monotone alignment of frames onto the timeline, solved as a hidden Markov model. This generalises the "first and last frame" straw man: every matchable frame anchors, not just two.
- Unanchored frames get a **time interval** bounded by neighbouring anchors and a **location** from the timeline inside that interval. Confidence falls out of the posterior, interval width, and location spread.

## Evidence model: phone photos are one signal among several

The engine consumes three kinds of evidence; every source plugs into one of them behind a small `Signal` interface.

| Kind | What it gives | Sources |
|---|---|---|
| **Constraints** (hard) | A frame or roll must fall inside a time range and/or at a place | User input: batch window, roll start/end dates, "frame 12 is at Sam's birthday on 4 July", "frames 1–8 are in Lisbon". Lab order/scan date as an upper bound. Previous roll from the same camera as a lower bound when the camera is tagged. |
| **Anchors** (visual match) | A specific frame matched to a specific timestamp + offset + GPS | iPhone photos and videos in Apple Photos (primary). Any other dated, geotagged image the user drops in (digital camera, a friend's export). |
| **Trails and priors** (soft) | Where the user was over time, and what they were doing | Phone-photo location trail. **NFC camera tag log**: scanning a tag on the camera appends time + location to a note; each entry is a dated location point tied to a camera. Apple Health workout routes (GPX). Google Maps Timeline export. Calendar events with locations. Email receipts (rides, restaurants, tickets, flights, hotels). Historical weather for a candidate day/place as a consistency check. |

Within-roll signals cost nothing external and are used from the start:
- **Outing segmentation**: frames sharing clothing, light, weather and scene continuity are the same outing. One roll-level Claude pass over a contact sheet groups frames; alignment then maps a few outings onto days instead of 36 frames onto weeks, and gets a "stay together" bonus between grouped frames.
- **Image clues** per frame: indoor/outdoor, time of day, weather, season, signage text, landmark or place guess, people descriptors. Used to score candidate days and to explain confidence in the UI.
- **Cross-roll continuity** (later): the same outfit or scene in rolls from different cameras lets one roll's anchors inform another.

NFC log specifics: the Shortcut appends one line per scan to a single Apple Note. Read that note's body via `osascript` (Notes AppleScript, by note title), parse lines into `(timestamp, lat, lon)`, and store them as trail points with source `nfc`. Because the user scans "not often", treat entries as strong trail points rather than per-frame anchors. Recommend two small Shortcut changes: append the camera name, and optionally a "loaded" / "finished" word, which turns the log into exact per-roll windows and a per-camera trail. Parsing must tolerate the current format and the extended one.

MVP scope: user constraints + phone-photo anchors + phone-photo trail + outing segmentation + NFC log. Health GPX, Timeline, calendar, email, weather are one adapter each in Milestone 5.

### User-supplied facts (UI and CLI)

- Per batch: rough window.
- Per roll: known start/end dates, camera name, film stock, lab, free-text notes.
- Per frame: known time or date, known place (map pin or place name), "same day as frame N", "unknown, skip".
- These become hard constraints (locked states) in the alignment, recorded as `filmgeo:manual` provenance. Adding one re-solves the roll in milliseconds and tightens neighbouring frames.

## Architecture

```
scan folders (one per roll)        Apple Photos library (Photos.sqlite + local derivatives)      other signals
        │                                          │                                            (NFC log, GPX, …)
        ▼                                          ▼                                                  │
 ingest: rolls/frames                photos index in window (osxphotos) → events (time+distance clustering)
        │                                          │                                                  │
        └──────── on-device embeddings (SigLIP + DINOv2, cached in SQLite by uuid / file hash) ◄──────┘
                                                   │
                       candidate retrieval: fused top-K phone photos per frame, diversity-capped per event
                                                   │
                       Claude: per-frame verification of top candidates + clue extraction; roll-level outing pass
                                                   │
                       alignment HMM: Viterbi proposal + forward-backward posteriors → time, interval, GPS, offset, confidence
                                                   │
                       SQLite review store ⇄ FastAPI local API ⇄ browser review UI (overrides re-solve live)
                                                   │
                       writer: exiftool → EXIF/XMP/keywords into files, backups, read-back verify, roll sidecar JSON
```

### Components (Python package `src/filmgeo/`)

- `scans/ingest.py` — roll = directory; natural-sort filenames; sha256 for idempotency; read existing EXIF to detect already-tagged files; trim scanner borders before embedding; "reverse roll" flag per roll. No mirror/rotation handling until a real case appears.
- `photos/library.py` — `osxphotos.PhotosDB()` adapter. Per asset: uuid, tz-aware date, `tzoffset`, lat/lon, largest `path_derivatives` entry, `ismissing`, type. Skip screenshots, trash, hidden. Metadata-only assets (no local derivative) still serve as time/GPS trail points, never as visual candidates. Event segmentation: new event on >45 min gap or >500 m move; each event has interval, centroid, spread radius, count.
- `signals/` — `Signal` interface returning trail points and/or constraints. MVP adapters: `photos_trail`, `nfc_log`, `user_facts`. Later: `health_gpx`, `google_timeline`, `calendar`, `email_receipts`, `weather`.
- `embed/` — SigLIP (semantic) on MPS, DINOv2 available but not in the default path. **M1 measured both plus two fusion methods on 113 hand-anchored frames: SigLIP alone won at every K** (91.2% recall@8 vs 90.3 RRF / 89.4 z-fused / 85.8 DINOv2), and DINOv2 costs 2-4x the embedding time. Fusion by z-score is actively harmful — z-scoring equalises variance but not tail shape, so the more peaked model dominates regardless of which is right. Cache vectors per (uuid|hash, model). Grayscale and border-trim variants still unmeasured; fit a logistic calibration from similarities to P(match) once labelled data exists.
- `retrieve.py` — per frame, top-K (≈8) inside the padded window, at most 3 per event so one heavily photographed scene cannot crowd out another day. **The per-event cap is worth more than the model choice** (M1: 99.1% vs 93.8% recall@32 with and without). recall@32 of 99.1% means the right candidate is nearly always retrievable and merely ranked low, so K trades directly against verification cost.
- `verify/claude.py` — structured-output calls. (1) Per frame: frame + up to 6 labelled candidates with local time and place text → `{match, confidence, evidence, clues{indoor, time_of_day, weather, season, signage_text, place_guess, people_descriptors}}`; prompted to separate "same visit" from "same place, another day" and to answer `none` freely. Bulk pass via the Batch API; interactive re-verification via direct call. (2) Per roll: contact sheet + timeline summary → frame groups sharing people/clothing/weather, out-of-sequence suspects. Model chosen in M1 by measuring accuracy against cost (start with the current mid-tier model, escalate if precision is short). Log tokens and cost per call.
- `align/model.py`, `align/solve.py` — the HMM (below).
- `geo.py` — interval location, interpolation, offset selection, ambiguity clusters.
- `db.py` — SQLite: `photos, events, trail_points, rolls, frames, candidates, clues, assignments, writes, api_calls`. `assignments` holds source (anchor/interpolated/manual), anchor uuid, time, offset, lat/lon, confidence, `t_lo/t_hi`, location flag (ok/ambiguous/none), status (proposed/confirmed/written), `user_locked`.
- `write/exiftool.py` — argfile per roll; backups; read-back verify; restore; sidecar; clear.
- `api/` — FastAPI: `/rolls`, `/rolls/{id}/frames`, `/frames/{id}/assign`, `/rolls/{id}/realign`, `/rolls/{id}/write`, thumbnails. The browser UI and any future native app are clients.
- `cli.py` (typer) — `filmgeo index --window`, `ingest`, `retrieve`, `verify`, `align`, `report`, `serve`, `write --dry-run`, `restore`, `eval`.

### Alignment HMM

States per roll, sorted by representative time:
- `A(i,c)`: frame i anchored to verified candidate c at instant t_c (exists only for verdict = c with confidence ≥ 0.5, or user-picked).
- `E(e)`: frame inside phone-photo event e (interval = event span, location = centroid).
- `G(k)`: frame inside the gap between events k and k+1 (interval known, location from trail points if any, else unknown).
- `X`: outside the window (wrong-window detection).

Emissions: anchors score from calibrated similarity plus Claude confidence; events from best in-event similarity plus clue consistency (time of day vs. local hour, indoor/outdoor, signage vs. reverse-geocoded place); gaps a flat penalty; X a larger penalty. User-locked frames prune every other state.

Transitions: hard monotone constraint on state rank; sublinear time-jump penalty so a roll can sit in a camera for weeks while consecutive frames prefer to stay close; small event-change penalty; "same outing" bonus from the roll-level pass; constraints from `Signal` sources zero out impossible states.

Solve: Viterbi for the proposal; forward-backward for per-frame posterior → confidence, 90%-mass time interval, location flag. Run Viterbi on the reversed order too; flag "possibly reverse-wound" if it wins clearly with ≥3 anchors. Wrong-window check: compare the best path against the all-gap null path and the anchored-frame fraction; offer one-click widening (retrieval is free, only new top-K get verified).

Assigned values: anchored frames take t_c and the anchor's offset exactly. Unanchored frames get a time inside their mode state's interval, then all times are forced strictly increasing with ≥2 s spacing so scan order survives in Photos and Lightroom. Uncertainty lives in `t_lo/t_hi`, not in the written time. Location for unanchored frames: trail points inside the interval; centroid if spread ≤300 m, linear interpolation between anchors ≤2 km apart, otherwise `ambiguous` with the distinct clusters offered in the UI and nothing invented. Offset from the nearest trail point in time; flag if points in the interval disagree (travel day).

### Review UI (`web/`, Vite + React + TypeScript, MapLibre)

One page per roll, three panes.
- Top: filmstrip in scan order; confidence bar (green ≥0.8, amber 0.5–0.8, red <0.5, grey locked); badges for anchor / interpolated / ambiguous location / confirmed.
- Centre: selected frame beside its chosen phone photo; candidate strip with similarity and Claude's verdict; "Not a match" and "No reference"; Claude's evidence and clues; editable local time + offset; interval shown as text ("between Sat 14:05 and Sat 17:40"); "Use this photo's time and GPS" on any candidate.
- Right: map with the draggable frame pin, trail points inside the interval, and selectable clusters for ambiguous frames; below it a timeline of the window with events as bars and frames as ticks; clicking sets time manually.
- Roll header: editable window with "widen ±1 month and re-run", reverse-roll toggle, camera, roll facts (known dates, notes), "window doubtful" warning, Claude cost so far.
- Keyboard: j/k frames, 1–9 pick candidate, Enter confirm, n no-match, u unlock.
- Every override locks the frame and re-solves; neighbours update live.
- Batch: confirm all ≥0.8, confirm roll, confirm range. Write requires confirmation.
- Write step: preview table (frame, current date, new date/offset, new GPS, keyword) → write → verification report → "Restore originals".
- Batch view across the 10 rolls sharing a window (M6).

### Metadata written (exiftool, argfile per roll)

- Time: `EXIF:DateTimeOriginal`, `EXIF:CreateDate` (capture time in both by default), `EXIF:OffsetTimeOriginal`, `OffsetTimeDigitized`, `OffsetTime`; `XMP-exif:DateTimeOriginal`, `XMP-photoshop:DateCreated`, `XMP-xmp:CreateDate` with offset. `FileModifyDate` set to the capture time as a fallback for Photos.
- Location: `GPSLatitude/Ref`, `GPSLongitude/Ref`, `GPSAltitude/Ref` when known, mirrored to `XMP-exif`.
- Camera: `EXIF:Make` and `EXIF:Model` from the roll's camera name, so Photos and Lightroom
  can filter a batch by body — impossible for film scans otherwise. The name is already a
  per-roll user fact; the UI offers the known bodies (Contax T2, Leica M7, Mamiya 7II) and the
  writer splits the name into make/model. Verified in M0.
- Verification and re-read must key off `EXIF:DateTimeOriginal` + `EXIF:OffsetTimeOriginal` +
  `XMP-photoshop:DateCreated`. Lightroom strips `XMP-exif:DateTimeOriginal` (deprecated in favour
  of `photoshop:DateCreated`) and resets `FileModifyDate` whenever it writes a file, so neither
  can be trusted on re-read. Both are still written; neither is load-bearing. Measured in M0.
- Descriptive keywords in the user's existing hand-tagging convention — plain and unprefixed,
  so generated tags merge with the tags already in the library rather than forming a private
  namespace: `Film`, the camera (`Leica M7`), the film stock (`Kodak Portra 800`) and the lab
  (`Richard Photo Lab`). Stock and lab are asked once per roll alongside the camera. No
  `EXIF:ISO`: film speed is carried by the stock keyword, and a scan's exposure fields are left
  blank rather than filled with a value the frame cannot vouch for. Verified in M0.
- Film stock is written **once, in full: `<Manufacturer> <Stock> <Speed>`** — `Kodak Portra 800`,
  not `Portra 800`, and never both. The user's library currently has some frames tagged both ways
  because the choice was made by hand each time; the tool always emits the full form so the tag is
  durable and one keyword means one stock. M3's roll form offers the known stocks as a picklist
  rather than free text, which is where the consistency actually comes from. The camera keyword
  follows the same rule (`Mamiya 7II`, matching `EXIF:Make` + `EXIF:Model`).
- Provenance keywords (`XMP-dc:Subject` + `IPTC:Keywords`): `filmgeo:anchored|interpolated|manual`, `filmgeo:conf:high|medium|low`, `filmgeo:location-unknown` when GPS is left blank. Clear command removes them.
- Sidecar `<roll>/filmgeo.json`: per frame anchor uuid, interval, confidence, decision source, Claude reasoning, write timestamp. Reopenable.
- Safety: no `-overwrite_original` (exiftool keeps `name.jpg_original`), plus a `.filmgeo_backup/` copy of the roll before the first write; read back with `exiftool -j` and diff; record in `writes`. Verify 16-bit TIFF round-trips in M0.
- Known behaviours: Lightroom shows `DateTimeOriginal` as local wall-clock and preserves but ignores the offset; Photos honours the offset. Both are what we want. Photos can ignore EXIF dates on re-import of a file it considers a duplicate: M0 tests the delete-then-reimport path. Since there is no backlog of already-imported scans, the `photoscript` in-place path is an optional M6 item.

## Ground truth already exists — but only half of it is real

Rolls the user has already hand-tagged carry their corrected EXIF, and they live in the Photos
library keyworded `Film`: 2,740 frames, 58 rolls, 2016-2026. No stripped-copy fixture set is
needed; the same assets are both the frames to match and the answer key.

**Only frames the user genuinely anchored may be scored.** For frames between two known points
they often picked a plausible date at random ("I knew frame 29 and the next roll's frame 1, so I
guessed in between"). A guessed timestamp is not evidence: it penalises a correct match and
rewards a lucky one on a densely photographed day. An anchored frame is detectable — hand-anchoring
means copying a phone photo's EXIF, so the timestamp coincides with that photo's to the second.
Across the 2026 batch that is 113 of 227 frames, ranging from 95% of one roll to 0% of another.
`Roll.anchored()` recovers them; every headline metric is measured on those alone.

Scoring on all frames instead understated recall@8 by 12 points and made one roll look like a
model failure when it was 87% guesses.

## Phased plan (solo developer with Claude Code; each milestone a few evenings to a weekend)

### M0 — Write-path proof (half a day, first)
Hand-tag 3 scans (JPG + TIFF) with exiftool using the exact tag set above; import into Photos, open in Lightroom Local. Confirm timeline placement next to iPhone photos, map pin, keywords visible, `_original` restore, and the delete-then-reimport behaviour. Also confirm `osxphotos` returns date/offset/GPS and local derivative paths (and their sizes) under Optimize Mac Storage.

### M1 — Matching-quality harness, no UI (risk-retiring)
CLI: `index → ingest → retrieve → verify → report` producing a static HTML contact sheet (frame, top-K, verdicts). Run on 2–3 hand-tagged rolls. Measure recall@K and precision@1 for embedding-only, precision of Claude's accepted matches, derivative availability. Decide SigLIP vs DINOv2 vs both, grayscale, K, model tier; fit calibration. Exit: ≥80% of frames that have a phone counterpart found in top-8; ≥95% of accepted matches correct.

### M2 — Alignment engine (CLI, JSON + HTML report)
Events, states, emissions, Viterbi, forward-backward, intervals, location/offset derivation, reverse test, wrong-window detection, `Signal` interface with `photos_trail`, `nfc_log`, `user_facts`. Validate on a multi-day roll and a deliberately wrong-month window. Exit: anchored frames exact; interpolated intervals contain the true time on hand-tagged rolls.

### M3 — Review UI
FastAPI + React as above: overrides, locking, live re-solve, roll facts input, batch confirm. No write yet.

### M4 — Write step
Argfile writer, backups, verify, sidecar, restore, clear; wired into the UI. Re-run the M0 checks on a full fresh roll end to end into Photos and Lightroom.

### M5 — More signals and robustness
Adapters: Health GPX, Google Timeline, calendar, email receipts, weather check. Cross-roll continuity. "Same camera" non-overlap constraint. Incremental library embedding cache. Timezone-change handling polish.

### M6 — Batch, polish, packaging
Multi-roll batch view, calibration refresh from confirmations, cost dashboard, `uv tool` packaging, `photoscript` path for scans already in Photos. Optional native Mac shell (SwiftUI/menu bar) hosting the same API.

## Repo layout

```
FilmGeotagger/
  PLAN.md  README.md  pyproject.toml   # uv, python ≥3.12
  src/filmgeo/
    cli.py  config.py  db.py
    scans/ingest.py
    photos/library.py
    signals/{base,photos_trail,nfc_log,user_facts}.py   # later: health_gpx, google_timeline, calendar, email, weather
    embed/{base,siglip,dinov2,cache}.py
    retrieve.py
    verify/claude.py
    align/{model,solve}.py
    geo.py
    write/exiftool.py
    api/{app,routes}.py
  web/                                 # Vite + React + TS + MapLibre + React Query
  scripts/eval_m1.py
  tests/                               # synthetic roll + fake timeline fixtures; DP unit tests
```

Dependencies to pin at M0: `osxphotos`, `anthropic`, `torch` (MPS) + `open_clip_torch` + `timm`, `pillow`, `numpy`, `fastapi`, `uvicorn`, `pydantic`, `typer`, `rich`; `exiftool` via Homebrew; `photoscript` (M6). Web: `react`, `vite`, `typescript`, `maplibre-gl`, `@tanstack/react-query`.

## Biggest risks and mitigations

1. **Film-vs-phone domain gap** → two embedding models, border trim, grayscale test, measured in M1 before any UI.
2. **Similar scenes on different days** → Claude verification on clothing/weather/light, monotone constraint, outing grouping, per-event diversity cap, runner-up margin surfaced.
3. **Derivatives missing** for some assets → metadata-only trail points; Swift PhotoKit helper as an escape hatch only if ever needed.
4. **Timezone/offset** → UTC + offset stored everywhere; offsets come from anchors/trail; travel-day intervals flagged; verified against Photos in M0.
5. **Writing damages files** → dry-run, `_original` + backup dir, read-back verify, run before Lightroom opens the folder.
6. **Wrong window** → doubtful-window flag with best-day suggestions and one-click widen.
7. **Photos ignoring EXIF on re-import** → `FileModifyDate`, documented delete-then-reimport, `photoscript` in-place path.
8. **Claude declining people photos** → treat a refusal as "no verdict", never "no match".

## Verification

- M0: scripted smoke tests (osxphotos fields, exiftool round-trip on JPG and TIFF, Photos import placement, reimport).
- M1: `filmgeo eval` on hand-tagged rolls: recall@K, precision, time error, GPS error, cost per roll.
- M2: unit tests for Viterbi/forward-backward/interval logic on synthetic fixtures; eval on multi-day roll and wrong-window roll.
- M3/M4: full fresh roll through the UI into Photos and Lightroom; API tests.
- M6: full 10-roll batch.

## Tracking

Milestones M0–M6 and their issues live in the Linear project
[Film Roll Geotagger](https://linear.app/coopersmith/project/film-roll-geotagger-83469e0c59e4/overview).
This document is the design reference; Linear is the source of truth for status.
