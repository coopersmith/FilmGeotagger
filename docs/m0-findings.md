# M0 — write-path proof: findings

Run on 2 September 2026, macOS 26.5.2 (Apple silicon), exiftool 13.55, osxphotos 0.76,
Photos library 123 GB (`Photos.sqlite` 5.0 GB), Optimize Mac Storage on.

Scans used: `SH75312_LiskaSmith` (Richard Photo Lab, 12 rolls, 255 JPGs at 5902×4815).

## Verdict

The write path works, on both JPG and 16-bit TIFF, and `osxphotos` gives the engine
everything the matching stage needs. Two script bugs and three exiftool behaviours had to be
pinned down first; all are fixed or documented below. Apple Photos honours the written time,
offset, GPS, keywords and camera and places the frame correctly on the timeline (COO-104),
re-reads corrected metadata after a delete and re-import (COO-105), and Lightroom preserves
everything the engine depends on across an edit (COO-106). **M0 is complete.**

## What the scans actually carry

The lab's JPGs have **no EXIF IFD at all** — not even a scan date. Only JFIF resolution and an
sRGB ICC profile. PLAN.md assumed "their EXIF date is the scan date"; in fact there is no EXIF
date whatsoever, so Photos and Lightroom fall back to the filesystem mtime, which is the lab
delivery date. This makes the engine's job cleaner (nothing to overwrite or reconcile) and means
`ingest.py`'s "already tagged?" check can simply test for the presence of `DateTimeOriginal`.

The batch is JPG only; no TIFFs. The lab does send TIFFs occasionally, though, so the write path
still has to be proven against one — exiftool inserting an EXIF IFD into a 16-bit TIFF could in
principle damage pixel data in a way it cannot with JPEG. `scripts/m0_make_tiff_fixture.py`
promotes a scan to full-resolution 16-bit RGB for that test (Pillow cannot write 16-bit RGB, so
it uses `tifffile`). It is a test fixture generator only; nothing in the engine calls it.

## exiftool behaviours worth keeping

1. **`-FileModifyDate<DateTimeOriginal` cannot share a command with the assignments.** The copy
   reads its source from the file as it was *before* the command's writes, so on a scan with no
   EXIF it warns `No writable tags set` and silently leaves the mtime alone. It has to be a
   second pass. The original script had it inline and swallowed stderr, so the failure was
   invisible. **M4's writer must run the mtime copy as its own pass and must not discard
   exiftool's stderr** — exiftool exits 0 on this warning.

2. **`FileModifyDate` gets the local machine's offset, not the capture offset.** Copying
   `15:32:10` from a frame shot at `+01:00` on a Mac in `-04:00` yields mtime
   `2026:07:14 15:32:10-04:00` — same wall clock, different instant. That is the right fallback
   for Photos (which shows wall-clock), but M4 should set it deliberately rather than inherit it
   by accident.

3. **Read-back must use `-G1` group names.** Tags are written with `-G0` names (`EXIF:`) but a
   `-j -G1` dump keys them as `ExifIFD:`, `GPS:`, `XMP-exif:`. The original verifier looked up
   `EXIF:DateTimeOriginal` in a `-G1` dump, got `None`, and reported three false failures on
   tags that had written correctly.

   Related trap while debugging: `exiftool -s -GPS:all` **without `-a`** suppresses tags whose
   names are duplicated across groups, so GPS looked absent when it was present. Always verify
   with `-j -n -G1` (or add `-a`).

4. EXIF stores GPS unsigned with a hemisphere ref (`GPSLongitude` 9.1393 + `GPSLongitudeRef` W);
   XMP-exif and Composite are signed (−9.1393). Verification has to expect both conventions.

## Write path results

Full PLAN.md tag set — `DateTimeOriginal`, `CreateDate`, all three `OffsetTime*`,
`XMP-exif:DateTimeOriginal`, `XMP-photoshop:DateCreated`, `XMP-xmp:CreateDate`, EXIF + XMP-exif
GPS, `XMP-dc:Subject` + `IPTC:Keywords` provenance, `FileModifyDate` — writes and reads back
correctly on all three files:

| File | Result |
|---|---|
| `874478_0001.jpg` (28 MB, 5902×4815) | PASS |
| `874466_0012.jpg` (5 MB) | PASS |
| `874478_0001_16bit.tif` (87 MB, 16-bit RGB, deflate) | PASS |

- **16-bit TIFF round-trips losslessly.** Pixels are bit-identical to the pre-write file
  (`numpy.array_equal`), dtype stays `uint16`, range still spans 0–65535, and `BitsPerSample`
  stays `16 16 16`. exiftool creates the EXIF and GPS IFDs from scratch without touching image data.
- **`-restore_original` is byte-exact.** The script now verifies this by restoring a throwaway
  copy and comparing sha256 against the untouched source, rather than only checking that an
  `_original` file exists.
- exiftool refuses to overwrite an existing `_original`, so a re-run over a previous run's output
  fails unless the stale backup is cleared first — the script now does that.

## osxphotos under Optimize Mac Storage

`scripts/m0_osxphotos_check.py --days 30 --limit 15`:

- 3,414 still photos in the last 30 days (~114/day — a two-month matching window is ~7k candidates).
- **Local derivative available: 3,414/3,414 (100%).** This retires PLAN.md risk #3 (missing
  derivatives) for recent photos; no PhotoKit escape hatch needed.
- Median derivative long edge **1024 px**, and 1024 px is the largest available. Comfortably above
  what SigLIP (384 px) and DINOv2 (224–518 px) consume, so M1 can match on derivatives as planned.
- `tzoffset` is populated and correct (−4.0 h EDT), `location` returns lat/lon, and
  `original_local` was true for every sampled asset — Optimize Mac Storage had not evicted
  recent originals, though the engine deliberately does not depend on that.
- No Full Disk Access prompt was needed for this terminal.

### Cost to be aware of in M1

`PhotosDB()` parses the entire library — 146,744 stills here — with no way to load only a date
window, so a `--window` flag can only filter after the fact. Measured on this 5 GB
`Photos.sqlite`:

| Step | Warm |
|---|---|
| `import osxphotos` | 0.8 s |
| `PhotosDB()` | 47–55 s |
| `db.photos(from_date=…)` + filter to 3,414 stills | 0.1 s |
| `path_derivatives` + `Image.open` for size, per 100 photos | 0.2 s |

The **first, cold run took over 10 minutes** — it was still going at the 600 s mark. Every warm
run since is about a minute. The cost is one-time page-cache warming of the 5 GB SQLite file,
not anything osxphotos does per photo: derivative probing is negligible (~1.9 derivative paths
per photo, ~2 ms each).

Two consequences for M1. `PhotosDB()` costs ~50 s per process even warm, so `index` must persist
photo/event/trail rows into the engine's own SQLite store and re-open the library only for new
assets — iterating `retrieve` or `verify` must never pay it again. And the first index of a
session should warn the user it may take minutes on a cold cache, so it does not look hung.

## Apple Photos import (COO-104) — confirmed

`874466_0012.jpg` imported into Photos shows:

- **`July 14, 2026  3:32:10 PM`** — Photos honours `OffsetTimeOriginal` and displays the
  capture-local wall clock. It did **not** re-express the instant in the Mac's zone (which would
  have read 10:32 AM). This is the behaviour the whole design depends on, and it holds.
- Both provenance keywords (`filmgeo:anchored`, `filmgeo:conf:high`) visible in the Info panel.
- Map pin in Lisbon, reverse-geocoded to "Lisboa, Portugal".
- "No camera information / No lens information" — we were writing no `Make`/`Model`.

**Changed as a result:** the tag set now includes `EXIF:Make` and `EXIF:Model`, taken from the
roll's camera name (already a per-roll user fact in PLAN.md) and split on the first space —
`"Contax T2"` becomes Make `Contax`, Model `T2`. This makes a batch filterable by body in both
Photos and Lightroom, which is otherwise impossible for film scans. Round-trips on JPG and TIFF;
`--camera ""` writes neither tag. PLAN.md's written-metadata section records the decision.

**Timeline placement confirmed**, and the evidence is unambiguous because the ordering is
counterintuitive. With the frame written as `2026:08:22 09:15:44 +01:00`, its neighbours in the
library (shot in Manitowish Waters, WI — CDT, −05:00) were:

| Position | Asset | Displays as | Absolute instant |
|---|---|---|---|
| before | `L1021409.JPG` (Leica Q3) | Aug 21, 7:13:06 PM | `Aug 22 00:13:06Z` |
| — | `874466_0012.jpg` (our scan) | Aug 22, 9:15:44 AM | `Aug 22 08:15:44Z` |
| after | `IMG_2689.HEIC` (iPhone 16 Pro) | Aug 22, 7:25:46 AM | `Aug 22 12:25:46Z` |

The frame *displays* 9:15 AM yet sorts *before* a photo displaying 7:25 AM. That is impossible
under wall-clock ordering and exactly correct under instant ordering. Photos stores the offset,
sorts on the resulting UTC instant, and renders the capture-local time — which is precisely what
the alignment design assumes when it gives an anchored frame the matched photo's timestamp and
offset verbatim.

**Corollary for M1:** the neighbouring assets here came from a Leica Q3 and an iPhone, both
already dated and geotagged in the library. Non-iPhone assets are therefore usable anchors for
free, and `photos/library.py` must not filter candidates by camera — only by the things that
make an asset useless (movies, screenshots, trash, hidden).

## Scan formats: two tiers in one batch

The Info panel showed 2048×3089 for a frame the roll folder listing suggested would be large.
Checking across the batch, `SH75312_LiskaSmith` contains **two different film formats**:

| Rolls | Frames | Pixels | Size | Aspect | Format | Body |
|---|---|---|---|---|---|---|
| 874466–874470 | 37 | 2048×3089 | ~5 MB | 1.51 | 35 mm | Contax T2 or Leica M7 |
| 874472–874478 | 10 | 5902×4815 | ~27 MB | 1.23 | 120, 6×7 | Mamiya 7II |

The user shoots three bodies: **Contax T2** and **Leica M7** (35 mm) and **Mamiya 7II** (120, 6×7).
Ten frames is exactly a 6×7 roll on 120, so frame count alone identifies the Mamiya rolls, and
the two 35 mm bodies are distinguishable only by the user saying which — a good default for the
review UI to guess and the user to confirm.

Consequences for M1/M2:

- Roll length is not a constant. Ingest must not assume ~36 frames; a 10-frame roll is a full
  120 roll, not a partial 35 mm one.
- Frames arrive in both orientations and two aspect ratios, and the 35 mm scans are 5× smaller.
  Embedding preprocessing has to normalise across this rather than assume one input size.
- A 10-frame roll gives the alignment HMM far fewer anchors to work with than a 37-frame roll, so
  medium-format rolls lean harder on trail points and user constraints.

## Delete-then-reimport (COO-105) — confirmed

Re-ran the writer with `--datetime "2026:08:22 09:15:44" --camera "Contax T2"`, deleted the
previously imported assets from Photos, emptied Recently Deleted, and re-imported. The Info
panel then read **`August 22, 2026  9:15:44 AM`** and **`Contax T2`**.

So Photos does *not* cling to a remembered date for a file it has seen before, provided the
asset is deleted from Recently Deleted first. That is the documented recovery path for a frame
written with a wrong time, and it means **PLAN.md risk #7 does not need the `photoscript`
in-place fallback** as a correctness backstop. M6 can keep `photoscript` as a convenience for
avoiding the delete/re-import round trip, not as a required escape hatch.

`EXIF:Make`/`Model` also survive the round trip and display in Photos.

## Lightroom round-trip (COO-106) — confirmed, with two caveats

Opened `scripts/out/` in Lightroom Local, confirmed date, Lisbon pin, keywords and `Contax T2`
all display, then added the keyword `newkeyword` to `874466_0012.jpg` and let Lightroom write
back. Re-read with `exiftool -j -n -G1`.

Lightroom wrote **into the file**, not to a `.xmp` sidecar, and touched only the file that was
edited — the other two were left byte-for-byte alone.

Survived intact: `ExifIFD:DateTimeOriginal`, all three `OffsetTime*` (`+01:00`),
`XMP-photoshop:DateCreated`, `XMP-xmp:CreateDate`, EXIF GPS, `IFD0:Make`/`Model`, and **both
`filmgeo:` provenance keywords in both `XMP-dc:Subject` and `IPTC:Keywords`**, with `newkeyword`
appended rather than replacing them. The provenance scheme is durable through Lightroom edits,
which is the property it needs to have.

Two tags did not survive:

| Tag | Result | Consequence |
|---|---|---|
| `XMP-exif:DateTimeOriginal` | **removed** | Adobe treats it as deprecated in favour of `photoshop:DateCreated` and drops the redundant copy. No information lost — EXIF `DateTimeOriginal` and two XMP date fields still carry the time with offset. |
| `System:FileModifyDate` | **reset to the edit time** | The mtime fallback is best-effort only; any Lightroom edit destroys it. |

**Consequences for M4.** The writer should keep writing `XMP-exif:DateTimeOriginal` (it is correct
until an editor touches the file, and some readers use it), but **read-back verification and the
"reopen a roll" path must key off `ExifIFD:DateTimeOriginal` + `OffsetTimeOriginal` +
`XMP-photoshop:DateCreated`, never `XMP-exif:DateTimeOriginal`** — otherwise a roll that has been
through Lightroom will fail its own verification. Likewise `FileModifyDate` must be treated as a
convenience for apps that ignore EXIF, not as a field the engine can trust on re-read.

Unrelated artifact: the TIFF fixture carries `IFD0:Software = tifffile.py` from the generator.
Harmless, and absent from real lab scans.

## Camera and film stock (added during M0)

Neither existed in the tag set at the start of M0. Both are per-roll user facts, and a film scan
has no camera or exposure metadata at all — Photos showed "No camera information" and five blank
exposure fields.

| Fact | Written as | Why there |
|---|---|---|
| Camera | `EXIF:Make` + `EXIF:Model`, split on the first space, **and** a plain keyword | Make/Model makes a batch filterable by body; the keyword matches how the user already tags |
| Film stock | plain keyword (`Kodak Portra 800`) | No canonical EXIF home, and the user already tags stock this way |
| Lab | plain keyword (`Richard Photo Lab`) | Same |
| — | plain keyword `Film` | The user's existing blanket tag for film frames |

**Keywords follow the user's existing convention, not a private namespace.** A sample of their
hand-tagged frames carries `Film`, `Leica M7`, `Kodak Portra 800`, `Portra 800`, `Indie Film Lab`
— plain and unprefixed. Generated keywords therefore merge with the tags already in the library
instead of sitting beside them. The `filmgeo:` prefix stays reserved for machine provenance
(`anchored`, `conf:*`, `location-unknown`), which is what `clear` removes; descriptive keywords
are the user's and are left alone.

**No `EXIF:ISO`.** An earlier pass wrote the shot-at speed there, on the reasoning that it is the
one exposure field a scan can honestly fill. Dropped: the stock keyword already carries the
speed, and the rest of the exposure block stays blank anyway, so a lone ISO is more noise than
signal. `--camera ""`, `--film ""` and `--lab ""` each write nothing, so every field is optional.
Verified round-tripping on JPG and 16-bit TIFF.

The user's bodies are **Contax T2**, **Leica M7** (35 mm) and **Mamiya 7II** (120, 6×7), which is
what the review UI should offer.

### Labs differ on what EXIF they leave behind

A previously hand-tagged frame from **Indie Film Lab** carries `Make`/`Model` = `EZ Controller`
— the Noritsu scanner software. The Richard Photo Lab batch examined above carries no EXIF IFD at
all. So "scans have no EXIF" is lab-specific, not universal: ingest must handle a scanner-populated
`Make`/`Model`, and the writer overwrites it with the actual camera, which is the desired outcome.

One bug worth remembering from the ISO attempt: it wrote `EXIF:ISO=2026-08-22T09:15:44+01:00`, because
the new `iso` parameter shadowed a local variable already holding the ISO-8601 *timestamp*.
exiftool reported `Warning: Not an integer for IFD0:ISO` and exited 0 — caught only because the
verifier now surfaces stderr. That is the second time in M0 that surfacing exiftool's warnings
turned a silent no-op into a visible failure.

## Still open

Nothing. M0 is complete.
