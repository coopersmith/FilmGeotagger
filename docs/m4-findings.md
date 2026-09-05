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
