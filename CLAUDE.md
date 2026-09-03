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
COO-118 (`align/checks.py`: reverse test, window check, widen). COO-120 (`filmgeo align`,
`filmgeo verify`, HTML report) is built and validated without verification; its wrong-month
run with real verdicts is pending (needs shifted-window embeddings from Terminal.app and ~$3
of API). Next: that run, then COO-119 (outing pass).
`docs/m2-findings.md` has the NFC note format, the facts-window result and the interval
measurement; `scripts/align_m2.py` reproduces the latter without API calls.

Read `docs/m1-findings.md` before touching retrieval or evaluation. Two things in it will
otherwise cost you a day:

1. **Half the hand-tagged ground truth is guessed.** For frames between two known anchors the
   user picked a plausible date at random. Scoring against those numbers understated recall@8 by
   12 points and made one roll look like a model failure when it was 87% guesses. Use
   `Roll.anchored()` and score on real anchors only.
2. **SigLIP alone is the default.** It beat DINOv2 and both fusion methods at every K. An earlier
   conclusion favouring reciprocal rank fusion was drawn on the contaminated set and is retracted.

`docs/m0-findings.md` carries the exiftool and Photos/Lightroom constraints that bind M4.

## Local setup on this machine

Homebrew, `exiftool`, `uv` and `gh` are installed; `gh auth login` is done, so `git push` works.
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
uv run --extra embed python scripts/embed_window.py 2026-05-01 2026-05-27   # Terminal.app only (Photos access)
uv run pytest                        # unit tests (needs `uv sync --extra dev`)

uv run --extra embed python scripts/eval_m1.py --rolls 9
uv run --extra embed python scripts/sweep_m1.py --rolls 9 --anchored-only   # cached vectors, seconds
uv run --extra embed --extra verify python scripts/verify_m1.py 00007044    # costs money (~$1/roll)
uv run --extra embed python scripts/align_m2.py --rolls 9 --mode oracle    # interval check, free, seconds
```

Optional dependency groups: `embed` (torch, open_clip, timm, numpy), `verify` (anthropic, pydantic).
Neither is installed by a bare `uv sync`.

### Derived state (gitignored, expensive to rebuild)

`.filmgeo/` holds `library.json` (63 MB Photos metadata cache), `vectors/` (26 MB of cached
embeddings), `facts/` (user facts per roll — the user's input, not derivable) and `nfc_log.txt`
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
