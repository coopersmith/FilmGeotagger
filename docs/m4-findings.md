# M4 — the write step: findings

Per-issue notes for the milestone that puts dates, offsets, GPS and keywords into the scan
files. `docs/m0-findings.md` holds the exiftool, Photos and Lightroom behaviour this builds on.

## COO-127 — the argfile writer and `filmgeo write --dry-run`

Landed 5 September 2026. `src/filmgeo/write/exiftool.py`, `filmgeo write`, 5 tests including
a real exiftool round-trip (86 in the suite).

### Shape

`plan(key, folder, …)` reads the roll's assignments file and facts, maps frame numbers onto
the scan folder's files in the same natural order `ingest` used, and produces a `WritePlan`:
the confirmed frames with everything they will get, and every other frame with the reason it
is left alone — "not confirmed", "skipped", "no file". A roll aligned from a hand-tagged key
is refused: its frames live inside the Photos library. `filmgeo write <roll>` prints the plan
as a table (file, what it says now, new local time, offset, GPS, provenance, action) and saves
the argfile under `.filmgeo/writes/<roll>.args`; `--write` runs it, after asking.

Per frame the tag set is PLAN.md's, as M0 proved it: `EXIF:DateTimeOriginal` and
`CreateDate`, the three `OffsetTime*`, `XMP-exif:DateTimeOriginal`, `XMP-photoshop:DateCreated`,
`XMP-xmp:CreateDate`, EXIF GPS with hemisphere refs and the signed XMP mirror, `EXIF:Make` and
`Model` from the camera name, and keywords in the user's own convention (`Film`, `Mamiya 7II`,
`Kodak Portra 400`, `Indie Film Lab`) plus the provenance `filmgeo:anchored|interpolated|manual`,
`filmgeo:conf:high|medium|low` and `filmgeo:location-unknown`. One argfile, one exiftool
process, `-execute` between files, never `-overwrite_original`.

### Three things exiftool taught, on top of M0's four

* **`FileModifyDate` as an explicit value with the capture offset works in the same pass.**
  M0's problem was the copy operator (`<DateTimeOriginal`), which reads the file as it was
  before the command and takes the Mac's zone. `-FileModifyDate=2026:04:04 10:01:49-04:00` sets
  the right instant directly, verified against `st_mtime`, so the second pass is gone.
* **`+=` on a keyword list duplicates.** Writing `Film` to a file that already has `Film`
  yields `Film` twice. The idiom is remove-then-add per keyword (`-Subject-=Film -Subject+=Film`),
  which exiftool documents as "add if absent"; the writer emits both for every keyword.
* **A second write must know the first.** Provenance from an earlier run
  (`filmgeo:anchored`) survives a later write of `filmgeo:manual` unless it is removed by
  value, and nothing can remove "everything starting with filmgeo:" in one argument. The plan
  therefore reads the files first (`current_tags`: the current date, for the preview, and any
  `filmgeo:` keywords present) and emits a removal for each stale one. The user's own keywords
  are never touched.

The time written is the assignment's instant rendered in the frame's `tzoffset`, so a
UTC-dated Leica Q3 anchor still writes `10:01:49 -04:00` for a frame shot in New York.

### Not yet

A folder copy before the first write, read-back verification against the trusted keys
(`ExifIFD:DateTimeOriginal`, `OffsetTimeOriginal`, `XMP-photoshop:DateCreated` — never
`XMP-exif:DateTimeOriginal`, which Lightroom strips), `restore` and `clear`, and a record of
what was written are COO-128; the sidecar is COO-129; the UI is COO-130.

## COO-128 — backup, read-back verify, record, restore, clear

Landed 5 September 2026. `write/ops.py`; `filmgeo write --write` now runs backup → write →
verify → record; `filmgeo restore <folder>` and `filmgeo clear <folder> [--all]`; one
full-cycle test on real exiftool (87 in the suite).

### The order is the safety

`write_roll`: save the argfile, copy every planned scan into `<folder>/.filmgeo_backup/`
(never overwriting a copy already there — the backup is the pristine lab scan however often
the roll is re-written), drop stale `<name>_original` files that would make exiftool refuse,
run the argfile, read everything back with `-j -n -G1`, and append the run to
`.filmgeo/writes/<roll>.json` with per-frame values and whether each verified.

Verification compares the keys the engine trusts — `ExifIFD:DateTimeOriginal`,
`OffsetTimeOriginal`, `XMP-photoshop:DateCreated`, GPS in both conventions, Make/Model —
and that every keyword is present exactly once in both `XMP-dc:Subject` and `IPTC:Keywords`.
It does not look at `XMP-exif:DateTimeOriginal` or `FileModifyDate` (Lightroom strips one and
resets the other, M0), so a roll that has been through Lightroom still verifies. A CLI write
whose read-back fails exits 1 and names `filmgeo restore`.

### Which copy is the original

The first version restored from exiftool's `_original` when one existed and the backup
otherwise. Wrong way round: `_original` is the file as it was before the *last* write, so
after a re-write or a `clear` it is itself a written file, and restoring from it would
"restore" filmgeo's own tags. The backup folder holds the lab scan and wins; `_original` is
the fallback for a file that was never backed up. Restore also removes the stale `_original`
so the next write starts clean.

`clear` reads each file's `filmgeo:` keywords and removes them by value (the user's
descriptive keywords stay); `--all` also blanks the written dates, offsets, GPS and
Make/Model. It writes through the same argfile path, so it, too, leaves the backup alone.

## COO-129 — the sidecar, and writing only what changed

Landed 5 September 2026. `write/sidecar.py`; `plan()` skips unchanged frames; the store
adopts a sidecar's decisions; `written` state in the API; 88 tests.

### What the sidecar is for

`<roll>/filmgeo.json` travels with the scans, where `.filmgeo/` is one machine's cache. Per
frame: the written local time and offset, GPS, source, confidence, interval, anchor photo,
Claude's evidence sentence, the write timestamp and whether it verified; plus the roll facts
and the user's overrides as they stood. Each write merges into it — frames written now are
replaced, the others keep their record — so the file is always the current state of the roll
as written.

Two things read it:

* **`plan()` writes only what changed.** A confirmed frame whose local time, offset, GPS and
  keywords equal the sidecar's record is skipped as "unchanged"; `--force` writes anyway.
  Moving one frame by an hour in the UI and writing again touches one file. A frame whose
  last write did not verify is never "unchanged".
* **Reopening a roll without its caches.** When the store loads a roll from a scan folder,
  `adopt()` seeds missing facts and overrides files from the sidecar, so the review UI shows
  the prior decisions and can change them. It never overwrites files that exist.

The API now says per frame what is in the file and whether the current assignment differs
(`written.changed`), and per roll whether it is writable (a scan folder, not the Photos
library) and when it was last written. That is what COO-130's write page needs.
