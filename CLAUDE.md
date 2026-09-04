# FilmGeotagger — working notes

Give scanned film frames exact capture date, UTC offset and GPS by aligning each roll against
the Apple Photos library and other dated signals, then write the result into the scan files.

`PLAN.md` is the design reference. [Linear](https://linear.app/coopersmith/project/film-roll-geotagger-83469e0c59e4/overview)
is the source of truth for status — **update the issue when you finish a piece of work, not in a
batch at the end**. Findings per milestone live in `docs/`.

## Where things stand

M0 (write-path proof) and M1 (matching-quality harness) are complete and merged.
M2 (alignment engine) is in progress. Done: COO-117 (`Signal` interface, `user_facts`,
`photos_trail`, `nfc_log`) and COO-114/115 (`align/model.py`, `align/solve.py`: states,
emissions, Viterbi, forward-backward), COO-116 (`geo.py`: location, clusters, offset),
COO-118 (`align/checks.py`: reverse test, window check, widen), COO-120 (`filmgeo align`,
`filmgeo verify`, HTML report, validated with real verdicts), COO-145 (anchored frames report
their occasion, not the photo's second: 36/37 and 10/10 frames inside their intervals on the
verified rolls). COO-146 (retrieval on the honest ground truth) settled the retrieval question:
**SigLIP finds the scene, not the shot** — the exact anchor photo ranks median 10th inside its
own event and no K or cap under $6.72/roll retrieves it for more than a fifth of frames, while
occasion-level recall rises with K at cap 1 (66% @8, 74% @12, 89% @24). Re-verified at K=12 /
cap 1: wrong-day accepts fell 6/28 → 3/28, so those are the defaults now ($2.52/roll).
COO-119 (outing pass) groups well but its transition bonus hurt (median error 1.7 h → 14.8 h)
and defaults to 0; using groups as joint day constraints is COO-147. Grayscale halves the exact
photo's rank inside its event (median 10 → 5) but does nothing for occasion recall, so colour
stays the shortlist and a grayscale second stage is COO-148. **M2 is complete.**
`docs/m2-findings.md` has the NFC note format, the facts-window result and the interval
measurement; `scripts/align_m2.py` reproduces the latter without API calls.

M3 (review UI) is in progress. Done: COO-121 (`api/`: FastAPI on 127.0.0.1 under `/api`,
`Store` keeping one solved run per roll and re-solving in milliseconds via
`pipeline.resolve`, overrides in `.filmgeo/overrides/`, thumbnails in `.filmgeo/thumbs/`,
`filmgeo serve`). Building it exposed an engine bug: two anchors in one event in the wrong
order were both kept and the written times came out non-monotone on the 22-day roll. Fixed
with per-anchor head/tail states (`align/model.py`); confidence is now the posterior mass on
the frame's *occasion*, so anchored frames read 0.9-0.99 instead of ~0.55. Details and the
before/after table are in `docs/m3-findings.md`. COO-122 (React app in `web/`: filmstrip,
frame detail, candidate strip, time editor; `npm run build` → `web/dist`, mounted by `serve`)
is done; times in the UI are always rendered from the ISO instant plus the frame's `tzoffset`,
never the photo's zone. COO-123 (MapLibre map with draggable pin, trail and clusters;
two-band timeline with click-to-set-time) is done; a user pin is now the frame's location and
an interpolation anchor in `geo.place`. COO-124 (every override through `PUT …/assign`:
any-photo picker from the timeline's event bars, not-a-match, no-reference, unknown, confirm,
unlock, keys `j k 1-9 Enter n N x u ?`) is done; "unknown" drops a pick and any change
unconfirms. COO-125 (roll facts drawer with save / rebuild / widen, frame facts form,
Claude cost estimate) is done; "same day as frame N" now binds a frame to a dated partner's
day in `frame_bounds` (two undated frames sharing a day wait for COO-147). Next: COO-126
(batch confirm), the last M3 issue.

Read `docs/m1-findings.md` before touching retrieval or evaluation. Two things in it will
otherwise cost you a day:

1. **Only a quarter of the hand-tagged ground truth is real.** For frames between two known
   anchors the user picked a plausible date at random, and the library also holds 115 untagged
   *copies* of scans at the tagged frame's instant. Use `Roll.anchored()` against
   `library.phone_times()` (35 frames in the 2026 batch, not 113) and score on those only.
   The M1 headline (91.2% recall@8) was measured with the copies in the pool; the honest
   number is 62.9%, below the exit bar. See the correction at the end of `docs/m1-findings.md`.
2. **SigLIP alone is the default.** It still leads DINOv2 and both fusion methods at @8 on the
   clean set. An earlier conclusion favouring reciprocal rank fusion is retracted.
3. **`Asset.is_scan`, never `is_film`, is the filter for candidates and trail.** Keyword, lab
   filename or scanner make; the keyword alone lets the copies through.

`docs/m0-findings.md` carries the exiftool and Photos/Lightroom constraints that bind M4.

## Local setup on this machine

Homebrew, `exiftool`, `uv`, `gh` and `node` (Homebrew) are installed; `gh auth login` is done, so `git push` works.
`brew` is at `/opt/homebrew/bin`, which is **not** on the default PATH for non-interactive shells —
prefix commands with `export PATH="/opt/homebrew/bin:$PATH"` or they fail with "command not found".

Anything that needs a TTY (`gh auth login`, the Homebrew installer, interactive `sudo`) cannot be
run from a tool call, including behind the `!` prefix. Ask the user to run it in Terminal.app.

**Photos derivatives are unreadable from tool-call shells**: opening anything under
`~/Pictures/Photos Library.photoslibrary/resources/derivatives/` fails with `Operation not
permitted` (macOS Full Disk Access), so embedding (`scripts/embed_window.py`, `eval_m1.py` on an
uncached window) has to be run by the user in Terminal.app. Cached vectors in `.filmgeo/vectors/`
and `library.json` read fine, which is why everything else works from here.

### Credentials

`.env` is gitignored and holds both of:

```
ANTHROPIC_API_KEY=...
ANTHROPIC_WORKSPACE_ID=wrkspc_...
```

The key is **identity-linked**: every API call fails with a 400 unless the
`anthropic-workspace-id` header is sent, and the header takes the workspace *ID*, not its name.
`filmgeo.verify.claude.make_client()` handles this. Load with `set -a; . ./.env; set +a`.

### Running things

```bash
uv run filmgeo index                 # Photos library -> .filmgeo/library.json (~50s warm, 10+ min cold)
uv run filmgeo rolls                 # hand-tagged rolls available as ground truth
uv run filmgeo report <roll-key>     # contact sheet -> reports/ (uses the facts window if set)
uv run filmgeo facts <roll> --from 2026-04 --to 2026-04 --camera "Mamiya 7II"   # user facts -> .filmgeo/facts/
uv run filmgeo signals <roll>        # trail points + constraints from every adapter
uv run --extra embed filmgeo align <roll>            # solve -> .filmgeo/assignments/<roll>.json + reports/align_<roll>.html
uv run --extra embed --extra verify filmgeo verify <roll>   # Claude verdicts -> .filmgeo/verdicts/; costs ~$0.035/frame, asks first
uv run --extra api --extra embed filmgeo serve [roll...]   # review API + UI on http://127.0.0.1:8765 (Terminal.app: it reads Photos derivatives)
(cd web && npm install && npm run build)                  # the UI -> web/dist, which serve mounts at /; `npm run dev` proxies /api to 8765
uv run --extra embed python scripts/embed_window.py 2026-05-01 2026-05-27   # Terminal.app only (Photos access)
uv run --extra dev --extra api pytest   # unit + API tests

uv run --extra embed python scripts/eval_m1.py --rolls 9
uv run --extra embed python scripts/sweep_m1.py --rolls 9 --anchored-only   # cached vectors, seconds
uv run --extra embed --extra verify python scripts/verify_m1.py 00007044    # costs money (~$1/roll)
uv run --extra embed python scripts/align_m2.py --rolls 9 --mode oracle    # interval check, free, seconds
uv run --extra embed python scripts/sweep_retrieval.py                     # K x cap grid on real anchors, free
```

Optional dependency groups: `embed` (torch, open_clip, timm, numpy), `verify` (anthropic, pydantic),
`api` (fastapi, uvicorn, pydantic). None is installed by a bare `uv sync`; the tests need `dev` and `api`.

### Derived state (gitignored, expensive to rebuild)

`.filmgeo/` holds `library.json` (63 MB Photos metadata cache), `vectors/` (26 MB of cached
embeddings), `facts/` and `overrides/` (the user's input per roll, not derivable),
`assignments/` (the solved proposal per roll, what M4 writes), `thumbs/` and `nfc_log.txt`
(the NFC note text; re-reading it through `osascript` takes minutes, see `signals/nfc_log.py`). Do not delete casually: the library cache costs a full `PhotosDB()` parse and the
vectors cost GPU time. `reports/` holds generated contact sheets.

## Conventions

- Long runs (embedding, `PhotosDB()`, eval sweeps) belong in background tool calls. Do not pipe
  them through `grep` — it buffers, and a killed job then leaves no partial output at all.
- Anything that spends money on the API says so before running.
- Keywords written into scans follow the user's own hand-tagging convention: plain and unprefixed
  (`Film`, `Mamiya 7II`, `Kodak Portra 400`, `Richard Photo Lab`). The `filmgeo:` prefix is
  reserved for machine provenance, and is what `clear` removes.
- The user shoots a Contax T2, a Leica M7 (35 mm) and a Mamiya 7II (120, 6×7). Frame count
  identifies the format: 10 frames is a full 6×7 roll, not a partial 35 mm one.
