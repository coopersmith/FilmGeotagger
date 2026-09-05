# Film Roll Geotagger

A local Mac tool that gives scanned film frames accurate capture dates, timezone offsets and GPS
coordinates by aligning each roll against the iPhone photos, NFC camera log and other dated signals
from the same period, then writing the result into the scan files for Lightroom and Apple Photos.

See [PLAN.md](PLAN.md) for the problem statement, design and milestones.
Status is tracked in the
[Linear project](https://linear.app/coopersmith/project/film-roll-geotagger-83469e0c59e4/overview).

## Status

M0 (write-path proof), M1 (matching-quality harness), M2 (alignment engine) and M3 (review UI)
are complete. Findings: [docs/m0-findings.md](docs/m0-findings.md),
[docs/m1-findings.md](docs/m1-findings.md), [docs/m2-findings.md](docs/m2-findings.md),
[docs/m3-findings.md](docs/m3-findings.md). The review UI walks each roll through five steps:
tell it when, let Claude look, confirm the greens, resolve the rest, write. M4 (the write step) is in
progress: the review UI's write panel (and `filmgeo write <roll> --write`) puts dates, offsets,
GPS and keywords into the scan files with a backup, a read-back check, a sidecar and one-click
restore ([docs/m4-findings.md](docs/m4-findings.md)). What remains is running a fresh roll
end to end into Photos and Lightroom.

```bash
filmgeo index                 # read the Photos library into the local cache (slow when cold)
filmgeo rolls                 # hand-tagged rolls available as ground truth
filmgeo ingest <roll-dir>     # a scan folder as ordered frames
filmgeo facts <roll> --from 2026-04 --to 2026-04 --camera "Mamiya 7II"   # what you know about a roll
filmgeo align <roll>          # solve -> .filmgeo/assignments/<roll>.json + reports/align_<roll>.html
filmgeo embed                 # embed the phone photos a new batch needs (Terminal.app; nothing twice)
filmgeo verify <roll>         # Claude verdicts (costs money, asks first)
filmgeo serve [roll...]       # review API + UI on http://127.0.0.1:8765 (needs --extra api; build the UI first)
```

The UI lives in `web/` (Vite + React + TypeScript): `cd web && npm install && npm run build`
writes `web/dist`, which `filmgeo serve` mounts at `/`. `npm run dev` runs it on port 5173
with `/api` proxied to the server.

Evaluation, which needs `--extra embed` and (for verification) a key in a gitignored `.env`:

```bash
uv run --extra embed python scripts/eval_m1.py --rolls 9
uv run --extra embed python scripts/sweep_m1.py --rolls 9 --anchored-only
uv run --extra embed --extra verify python scripts/verify_m1.py 00007044
```

## Milestone 0: write-path proof

Run on the Mac, before any matching code exists.

```bash
brew install exiftool uv
uv sync

# The lab delivers JPG only, so promote one scan to a 16-bit TIFF to test that path too.
uv run --with numpy --with tifffile python scripts/m0_make_tiff_fixture.py \
  scripts/fixtures/874478_0001.jpg

uv run python scripts/m0_exiftool_roundtrip.py \
  --camera "Mamiya 7II" --film "Kodak Portra 400" --lab "Richard Photo Lab" \
  scripts/fixtures/874478_0001.jpg \
  scripts/fixtures/874466_0012.jpg \
  scripts/fixtures/874478_0001_16bit.tif
uv run python scripts/m0_osxphotos_check.py --days 30
```

`m0_osxphotos_check.py` needs Full Disk Access for the terminal running it, and takes several
minutes: `PhotosDB()` parses the whole library, with no way to load only a date window.

Then the manual checks, tracked as COO-104, COO-105 and COO-106 in Linear:

1. Import `scripts/out/*.jpg` and `*.tif` into Apple Photos. Each should land on 14 July 2026
   at 15:32 local, pinned to Lisbon, with the `filmgeo:` keywords visible.
2. Delete them from Photos (and from Recently Deleted), re-run with a different
   `--datetime`, re-import. Confirm the new date is used.
3. Open `scripts/out/` in Lightroom (Local). Confirm date, map pin and keywords, then edit one
   metadata field and re-read with `exiftool -G1 -a` to confirm the offset and keywords survive.

Findings from the first run are in [docs/m0-findings.md](docs/m0-findings.md).
