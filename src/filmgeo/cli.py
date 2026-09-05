"""Command-line entry point. Subcommands arrive milestone by milestone (see PLAN.md)."""

from __future__ import annotations

from datetime import timedelta
from pathlib import Path

import typer
from rich.console import Console
from rich.table import Table

from filmgeo import __version__

app = typer.Typer(help="Film Roll Geotagger", no_args_is_help=True)
console = Console()


@app.command()
def version() -> None:
    """Print the engine version."""
    typer.echo(__version__)


@app.command()
def index() -> None:
    """Read the Apple Photos library into the local cache.

    Slow on a cold cache — PhotosDB parses the whole library, which took over ten minutes the
    first time on a 5 GB Photos.sqlite and about 50 s warm (M0).
    """
    from filmgeo.photos import library

    with console.status("reading the Photos library (minutes, if the cache is cold)..."):
        n = library.build_cache()
    console.print(f"indexed [bold]{n}[/] assets -> {library.CACHE}")


@app.command()
def ingest(directory: Path) -> None:
    """List a roll folder as ordered frames."""
    from filmgeo.scans.ingest import ingest as do_ingest

    roll = do_ingest(directory)
    table = Table(title=f"{roll.name} — {len(roll.frames)} frames, {roll.format_guess}")
    for col in ("#", "file", "tagged"):
        table.add_column(col)
    for f in roll.frames:
        table.add_row(str(f.number), f.path.name, "yes" if f.already_tagged else "")
    console.print(table)


@app.command()
def rolls(limit: int = 20) -> None:
    """List the hand-tagged rolls available as ground truth."""
    from filmgeo import eval_set
    from filmgeo.photos import library

    assets = library.load()
    table = Table(title="hand-tagged rolls (ground truth)")
    for col in ("roll", "frames", "outliers", "format", "start", "span"):
        table.add_column(col)
    for r in eval_set.rolls(assets)[:limit]:
        table.add_row(
            r.key, str(len(r.frames)), str(len(r.outliers())) or "",
            r.format, f"{r.start:%Y-%m-%d}", str(r.span).split(".")[0],
        )
    console.print(table)


@app.command()
def report(roll_key: str, pad_days: int = 2, k: int = 8, out: Path = Path("reports")) -> None:
    """Write the contact sheet for one hand-tagged roll, from cached vectors."""
    import numpy as np

    from filmgeo import eval_set, events as ev, report as rep, retrieve
    from filmgeo.config import SAME_MOMENT
    from filmgeo.embed.cache import VectorCache
    from filmgeo.photos import library
    from filmgeo.signals.base import Window, effective_window
    from filmgeo.signals.user_facts import RollFacts, UserFacts

    assets = library.load()
    roll = next((r.clean() for r in eval_set.rolls(assets) if r.key == roll_key), None)
    if roll is None:
        raise typer.BadParameter(f"no hand-tagged roll {roll_key}")

    facts = RollFacts.load(roll_key)
    f_lo, f_hi = facts.window()
    if f_lo or f_hi:
        # The user's window is the one the tool will actually have in use (COO-117).
        window = effective_window(UserFacts(facts).constraints(), Window(f_lo or roll.start, f_hi or roll.end))
        pool = library.candidates(assets, window.start, window.end)
        window_text = f"facts window {facts.window_from or '..'} .. {facts.window_to or '..'}"
    else:
        pool = library.candidates(assets, roll.start, roll.end, pad_days=pad_days)
        window_text = f"+/-{pad_days}d"
    ids, _ = ev.segment(pool)
    caches = {v: VectorCache(v) for v in ("siglip", "dinov2")}
    try:
        fv = {v: c.get([f.uuid for f in roll.frames]) for v, c in caches.items()}
        pv = {v: c.get([a.uuid for a in pool]) for v, c in caches.items()}
    except KeyError:
        raise typer.BadParameter("vectors not cached for this roll/window — run scripts/eval_m1.py first")

    rows = []
    for i, frame in enumerate(roll.frames):
        cands = retrieve.top_k({v: fv[v][i] for v in fv}, pv, pool, events=ids, k=k)
        rows.append({
            "number": roll.numbers[i],
            "path": frame.derivative,
            "date": frame.date,
            "candidates": [
                {
                    "path": c.asset.derivative,
                    "date": c.asset.date,
                    "score": c.score,
                    "correct": abs((c.asset.date - frame.date).total_seconds()) <= SAME_MOMENT,
                }
                for c in cands
            ],
        })
    hits = sum(any(c["correct"] for c in r["candidates"]) for r in rows)
    path = rep.write(
        out / f"roll_{roll_key}.html", roll_key, rows,
        subtitle=(f"{len(roll.frames)} frames, {roll.format}, {roll.start:%Y-%m-%d} .. {roll.end:%Y-%m-%d} | "
                  f"pool {len(pool)} photos, {window_text} | recall@{k} {hits}/{len(rows)}"),
    )
    console.print(f"wrote {path}  (recall@{k} {hits}/{len(rows)})")


def _roll_key(roll: str) -> tuple[str, int | None]:
    """A roll is named by its scan folder or its lab key; a folder also tells us the frame count."""
    p = Path(roll).expanduser()
    if p.is_dir():
        from filmgeo.scans.ingest import ingest as do_ingest

        return p.name, len(do_ingest(p).frames)
    return roll, None


def _print_facts(facts, n_frames: int | None) -> None:
    from filmgeo.signals.user_facts import UserFacts

    lo, hi = None, None
    try:
        lo, hi = facts.window()
    except ValueError:
        pass
    table = Table(title=f"facts for roll {facts.roll}", show_header=False)
    table.add_column("field", style="bold")
    table.add_column("value")
    table.add_row("window", f"{facts.window_from or '…'} .. {facts.window_to or '…'}"
                  + (f"   ({lo:%Y-%m-%d %H:%M %Z} → {hi:%Y-%m-%d %H:%M %Z}, exclusive)" if lo and hi else ""))
    table.add_row("tz", facts.tz or f"{facts.zone} (this Mac)")
    for k in ("camera", "film", "lab", "notes"):
        if getattr(facts, k):
            table.add_row(k, str(getattr(facts, k)))
    if facts.reverse:
        table.add_row("reverse", "yes — scanned in reverse order")
    console.print(table)

    frames = {n: f for n, f in sorted(facts.frames.items()) if not f.is_empty}
    if frames:
        ft = Table(title="frame facts")
        for col in ("#", "when", "place", "same day as", "skip", "note"):
            ft.add_column(col)
        for n, f in frames.items():
            place = ""
            if f.lat is not None:
                place = f"{f.lat:.5f}, {f.lon:.5f}" + (f" ±{f.radius_m:.0f} m" if f.radius_m else "")
            if f.place_name:
                place = f"{f.place_name} ({place})" if place else f.place_name
            ft.add_row(str(n), f.when or "", place, str(f.same_day_as or ""), "yes" if f.skip else "", f.note or "")
        console.print(ft)
    for p in facts.validate(n_frames):
        console.print(f"[red]problem:[/] {p}")
    n = len(UserFacts(facts).constraints())
    console.print(f"{n} constraint{'s' if n != 1 else ''} -> {facts.path_for(facts.roll)}")


@app.command()
def facts(
    roll: str = typer.Argument(..., help="scan folder, or a roll key such as 00007044"),
    from_: str = typer.Option(None, "--from", help="earliest period: 2026-04, 2026-04-12, '2026-04-12 14:05'"),
    to: str = typer.Option(None, "--to", help="latest period (inclusive), same forms as --from"),
    tz: str = typer.Option(None, help="IANA zone the dates are in, e.g. Europe/Lisbon; default this Mac's"),
    camera: str = typer.Option(None, help="camera body, e.g. 'Mamiya 7II'"),
    film: str = typer.Option(None, help="film stock in full, e.g. 'Kodak Portra 400'"),
    lab: str = typer.Option(None, help="lab, e.g. 'Richard Photo Lab'"),
    note: str = typer.Option(None, help="free-text roll notes"),
    reverse: bool = typer.Option(None, "--reverse/--no-reverse", help="roll was scanned in reverse order"),
    frame: int = typer.Option(None, help="frame number the following options apply to"),
    on: str = typer.Option(None, help="frame: known period, e.g. 2026-04-12 or '2026-04-12 14:05'"),
    place: str = typer.Option(None, help="frame: 'lat,lon'"),
    place_name: str = typer.Option(None, help="frame: place label"),
    radius: float = typer.Option(None, help="frame: place radius in metres"),
    same_day_as: int = typer.Option(None, help="frame: same day as this frame number"),
    skip: bool = typer.Option(None, "--skip/--no-skip", help="frame: leave unassigned"),
    frame_note: str = typer.Option(None, help="frame: free-text note"),
    forget: bool = typer.Option(False, help="frame: drop every fact about it"),
    clear_window: bool = typer.Option(False, help="drop the roll window"),
) -> None:
    """Record what you know about a roll: its window, camera, film, lab, and per-frame facts.

    Facts are hard constraints for the alignment (PLAN.md) and the cheapest way to narrow the
    window — M1 measured window width at ~9 points of recall. With no options, shows the facts.
    """
    from filmgeo.config import KNOWN_CAMERAS
    from filmgeo.signals.user_facts import RollFacts, parse_period

    key, n_frames = _roll_key(roll)
    rf = RollFacts.load(key)
    changed = False
    for k, v in (("window_from", from_), ("window_to", to), ("tz", tz), ("camera", camera),
                 ("film", film), ("lab", lab), ("notes", note), ("reverse", reverse)):
        if v is not None:
            if k in ("window_from", "window_to"):
                parse_period(v, rf.zone)  # fail loudly before saving
            setattr(rf, k, v)
            changed = True
    if clear_window:
        rf.window_from = rf.window_to = None
        changed = True
    if camera and camera not in KNOWN_CAMERAS:
        console.print(f"[yellow]note:[/] {camera!r} is not one of {', '.join(KNOWN_CAMERAS)} — fine if it is a new body")

    frame_opts = (on, place, place_name, radius, same_day_as, skip, frame_note)
    if frame is None and (any(v is not None for v in frame_opts) or forget):
        raise typer.BadParameter("frame options need --frame N")
    if frame is not None:
        if n_frames is not None and not (1 <= frame <= n_frames):
            raise typer.BadParameter(f"frame {frame} does not exist; the roll has {n_frames} frames")
        if forget:
            rf.frames.pop(frame, None)
        else:
            ff = rf.frame(frame)
            if on is not None:
                parse_period(on, rf.zone)
                ff.when = on
            if place is not None:
                try:
                    lat, lon = (float(x) for x in place.split(","))
                except ValueError:
                    raise typer.BadParameter("--place wants 'lat,lon'")
                ff.lat, ff.lon = lat, lon
            if place_name is not None:
                ff.place_name = place_name
            if radius is not None:
                ff.radius_m = radius
            if same_day_as is not None:
                ff.same_day_as = same_day_as
            if skip is not None:
                ff.skip = skip
            if frame_note is not None:
                ff.note = frame_note
        changed = True

    if changed:
        problems = rf.validate(n_frames)
        if problems:
            for p in problems:
                console.print(f"[red]problem:[/] {p}")
            raise typer.Exit(1)
        rf.save()
    _print_facts(rf, n_frames)


@app.command()
def signals(
    roll: str = typer.Argument(..., help="scan folder, or a hand-tagged roll key"),
    pad_days: int = typer.Option(2, help="fallback window padding around a hand-tagged roll's true range"),
    refresh_nfc: bool = typer.Option(False, help="re-read the NFC note from Notes.app (minutes)"),
) -> None:
    """Show what every evidence source says about a roll's window: trail points and constraints."""
    from collections import Counter

    from filmgeo import eval_set
    from filmgeo.photos import library
    from filmgeo.signals.base import Window, collect, effective_window, frame_bounds
    from filmgeo.signals.nfc_log import NfcLog
    from filmgeo.signals.photos_trail import PhotosTrail
    from filmgeo.signals.user_facts import RollFacts, UserFacts

    key, n_frames = _roll_key(roll)
    rf = RollFacts.load(key)
    assets = library.load()
    truth = next((r.clean() for r in eval_set.rolls(assets) if r.key == key), None)
    if truth is not None and n_frames is None:
        n_frames = len(truth.frames)

    f_lo, f_hi = rf.window()
    if f_lo or f_hi:
        if not (f_lo and f_hi):
            raise typer.BadParameter("give both --from and --to (filmgeo facts) so the window is bounded")
        default = Window(f_lo, f_hi)
        how = "from facts"
    elif truth is not None:
        default = Window.around(truth.start, truth.end, pad_days)
        how = f"hand-tagged range +/-{pad_days}d — no facts recorded, so this is the answer key's window, not a user's"
    else:
        raise typer.BadParameter(f"no facts window for {key}; set one with `filmgeo facts {roll} --from ... --to ...`")

    trail = PhotosTrail(assets)
    sigs = [UserFacts(rf), trail]
    try:
        sigs.append(NfcLog.from_notes(offset_for=trail.offset_for, refresh=refresh_nfc))
    except RuntimeError as e:
        console.print(f"[yellow]nfc_log unavailable:[/] {e}")
    from filmgeo.signals.health_routes import HEALTH_DIR, HealthRoutes

    if HEALTH_DIR.is_dir():
        health = HealthRoutes(HEALTH_DIR, offset_at=trail.offset_at)
        sigs.append(health)
        console.print(f"health routes: {len(health.files(default))} GPX files in the window under {HEALTH_DIR}")
    else:
        console.print(f"[dim]no Health routes: drop an export's workout-routes/ under {HEALTH_DIR} to use them[/]")
    ev = collect(sigs, default)
    window = effective_window(ev.constraints, default)

    console.print(f"[bold]{key}[/]  window {window.start:%Y-%m-%d %H:%M %Z} .. {window.end:%Y-%m-%d %H:%M %Z}  ({how})")
    if truth is not None:
        inside = window.start <= truth.start and truth.end <= window.end
        console.print(f"  hand-tagged range {truth.start:%Y-%m-%d} .. {truth.end:%Y-%m-%d}: "
                      + ("[green]inside the window[/]" if inside else "[red]NOT inside the window[/]"))

    t = Table(title="trail points in window")
    for col in ("source", "points", "with GPS", "offsets seen", "first", "last"):
        t.add_column(col)
    for src in sorted({p.source for p in ev.trail}):
        pts = [p for p in ev.trail if p.source == src]
        offs = Counter(p.tzoffset for p in pts if p.tzoffset is not None)
        t.add_row(src, str(len(pts)), str(sum(p.has_location for p in pts)),
                  ", ".join(f"{o/3600:+.0f}h×{n}" for o, n in offs.most_common(3)),
                  f"{pts[0].time:%Y-%m-%d %H:%M}", f"{pts[-1].time:%Y-%m-%d %H:%M}")
    console.print(t)
    for p in [p for p in ev.trail if p.source == "nfc"]:
        console.print(f"  nfc  {p.time:%Y-%m-%d %H:%M %z}  {p.lat:.4f},{p.lon:.4f}  {p.camera or '-':10}  {p.label or ''}")

    if ev.constraints:
        c = Table(title="constraints")
        for col in ("scope", "frame", "from", "to", "place", "other"):
            c.add_column(col)
        for k in ev.constraints:
            other = ", ".join(x for x in (f"same day as {k.same_day_as}" if k.same_day_as else "", "skip" if k.skip else "", k.note or "") if x)
            c.add_row(k.scope, str(k.frame or ""), f"{k.t_lo:%Y-%m-%d %H:%M}" if k.t_lo else "",
                      f"{k.t_hi:%Y-%m-%d %H:%M}" if k.t_hi else "",
                      f"{k.lat:.4f},{k.lon:.4f}" if k.has_place else "", other)
        console.print(c)
    if n_frames:
        bounds = frame_bounds(ev.constraints, n_frames, window)
        tight = sum(1 for lo, hi in bounds if (lo, hi) != (window.start, window.end))
        console.print(f"{n_frames} frames; {tight} have bounds tighter than the window after monotone propagation")


def _require_readable(*paths: str | None) -> None:
    """Images inside the Photos library are unreadable from sandboxed shells (CLAUDE.md); fail
    before spending anything rather than after 37 silent 'no verdict' lines."""
    for p in paths:
        if not p:
            continue
        try:
            with open(p, "rb") as f:
                f.read(16)
        except PermissionError:
            console.print(f"[red]cannot read[/] {p}\nmacOS denies this shell access to the Photos library. "
                          "Run this command from Terminal.app, which has Full Disk Access.")
            raise typer.Exit(2)


@app.command()
def verify(
    roll: str = typer.Argument(..., help="scan folder, or a hand-tagged roll key"),
    k: int = typer.Option(12, help="candidates shown to Claude per frame (COO-146: 12)"),
    cap: int = typer.Option(1, help="at most this many candidates per phone-photo event (0 = no cap; COO-146: 1)"),
    limit: int = typer.Option(0, help="only the first N frames"),
    only_new: bool = typer.Option(False, help="skip frames whose shown candidates are unchanged (after --widen)"),
    widen: bool = typer.Option(False, help="retrieve on the window widened by a month each side"),
    inside: bool = typer.Option(False, help="second round: only the unanchored frames, with photos from inside each frame's interval"),
    model: str = typer.Option(None, help="Claude model id"),
    alias: str = typer.Option(None, "--as", help="name the facts/verdicts/assignments files differently (a second window for one roll)"),
    yes: bool = typer.Option(False, "--yes", "-y", help="spend the money without asking"),
) -> None:
    """Ask Claude which candidate, if any, shows each frame's occasion. Costs money — says how much first.

    `--inside` is the iterate step: after a first round and an align, the anchored frames bound
    every other frame's interval, and the photos inside that interval are the only ones it
    can be. Facts ("same day as frame 3") narrow the intervals first, for free.
    """
    from filmgeo.align import pipeline
    from filmgeo.align.checks import new_candidates
    from filmgeo.verify import claude

    model = model or claude.DEFAULT_MODEL
    r = pipeline.run(roll, k=k, widen=widen, alias=alias, cap=cap)
    _require_readable(r.frames[0].path, r.pool[0].derivative)
    existing = r.verdicts
    todo = []
    for f, a in zip(r.frames[: limit or None], r.solution.assignments):
        if inside:
            if a.source in ("anchored", "locked", "skipped"):
                continue
            shown = [c.asset.uuid for c in r.possible.get(f.number, [])[:k]]
            if not shown:
                console.print(f"  frame {f.number}: no photos inside {a.t_lo:%m-%d %H:%M} .. {a.t_hi:%m-%d %H:%M}")
                continue
        else:
            shown = [c.asset.uuid for c in r.candidates[f.number][:k]]
        if only_new and f.number in existing:
            fresh = new_candidates({f.number: existing[f.number].candidates}, {f.number: shown})[f.number]
            if not fresh:
                continue
        todo.append((f, shown))
    n_images = sum(len(shown) for _, shown in todo)
    est = 0.035 * n_images / 6        # $0.035/frame at k=6 on claude-opus-5 (M1), linear in images shown
    console.print(f"[bold]{r.key}[/]: {len(todo)} frames, {n_images} candidates{' inside their intervals' if inside else ''} on {model} "
                  f"— about [bold]${est:.2f}[/] ({len(existing)} already verified)")
    if not todo:
        return
    if not yes and not typer.confirm("Spend it?"):
        raise typer.Exit(0)
    client = claude.make_client()
    by_uuid = {a.uuid: a for a in r.pool}
    out: dict[int, pipeline.Verdict] = {}
    with console.status("verifying..."):
        for f, shown in todo:
            refs = [claude.CandidateRef(u, by_uuid[u].derivative, by_uuid[u].date) for u in shown]
            v = claude.verify_frame(client, f.path, refs, model=model)
            if v is None:
                console.print(f"  frame {f.number}: no verdict (refusal or unreadable image)")
                continue
            match = shown[v.match - 1] if v.match and 1 <= v.match <= len(shown) else None
            out[f.number] = pipeline.Verdict(shown, match, v.confidence, v.evidence, v.clues.model_dump())
            console.print(f"  frame {f.number}: {'match ' + by_uuid[match].date.strftime('%m-%d %H:%M') if match else 'none':22} "
                          f"{v.confidence:.2f}  {v.evidence[:70]}")
    p = pipeline.save_verdicts(r.key, out, {"roll": r.key, "model": model, "k": k, "cap": cap})
    console.print(f"{len(out)} verdicts -> {p}")


@app.command()
def outing(
    roll: str = typer.Argument(..., help="scan folder, or a hand-tagged roll key"),
    model: str = typer.Option(None, help="Claude model id"),
    alias: str = typer.Option(None, "--as", help="name the facts/outings files differently"),
    yes: bool = typer.Option(False, "--yes", "-y", help="spend the money without asking"),
) -> None:
    """One Claude call per roll: group the frames into outings from a contact sheet. Costs money."""
    from filmgeo.align import pipeline
    from filmgeo.verify import claude, outing as op

    model = model or op.DEFAULT_MODEL
    r = pipeline.run(roll, alias=alias)
    _require_readable(r.frames[0].path)
    sheet = op.contact_sheet([f.path for f in r.frames], [f.number for f in r.frames], Path("reports") / f"sheet_{r.key}.jpg")
    console.print(f"[bold]{r.key}[/]: one call with a {len(r.frames)}-frame contact sheet on {model} — about [bold]$0.15[/]")
    if not yes and not typer.confirm("Spend it?"):
        raise typer.Exit(0)
    answer = op.ask(claude.make_client(), sheet, len(r.frames), op.events_summary(r.events), r.facts.camera, model)
    if answer is None:
        console.print("[red]no answer[/] (refusal)")
        raise typer.Exit(1)
    o = op.Outings.from_answer(r.key, answer, model)
    p = o.save()
    for i, g in enumerate(o.groups, 1):
        console.print(f"  outing {i}: frames {g['frames'][0]}–{g['frames'][-1]} ({len(g['frames'])})  {g['confidence']:.2f}  {g['description']}")
    if o.out_of_sequence:
        console.print(f"  [yellow]out of sequence:[/] {o.out_of_sequence}")
    if o.notes:
        console.print(f"  {o.notes}")
    console.print(f"{len(o.groups)} outings, {len(o.same_outing_pairs(len(r.frames)))} same-outing pairs -> {p}")


@app.command()
def align(
    roll: str = typer.Argument(..., help="scan folder, or a hand-tagged roll key"),
    pad_days: int = typer.Option(2, help="fallback padding around a hand-tagged roll's true range"),
    widen: bool = typer.Option(False, help="widen the window by a month each side"),
    alias: str = typer.Option(None, "--as", help="name the facts/verdicts/assignments files differently (a second window for one roll)"),
    out: Path = typer.Option(Path("reports"), help="where the HTML report goes"),
) -> None:
    """Solve a roll: time, interval, location and confidence per frame -> JSON + HTML report."""
    from filmgeo.align import pipeline, report as arep

    r = pipeline.run(roll, pad_days=pad_days, widen=widen, alias=alias)
    sol = r.solution
    table = Table(title=f"{r.key}: {r.window.start:%Y-%m-%d} .. {r.window.end:%Y-%m-%d} ({r.window_source})")
    for col in ("#", "source", "time", "interval", "conf", "location", "truth"):
        table.add_column(col)
    for f, a in zip(r.frames, sol.assignments):
        loc = f"{a.lat:.4f},{a.lon:.4f}" if a.location == "ok" else f"ambiguous ({len(a.clusters)})" if a.location == "ambiguous" else "-"
        truth = ""
        if f.truth:
            inside = a.t_lo - timedelta(minutes=2) <= f.truth <= a.t_hi + timedelta(minutes=2)
            truth = f"{f.truth:%m-%d %H:%M} {'ok' if inside else 'OUT'}"
        table.add_row(str(f.number), a.source, f"{a.time:%m-%d %H:%M}", arep.interval_text(a), f"{a.confidence:.2f}", loc, truth)
    console.print(table)
    console.print(f"anchored {sol.anchored}/{r.n_frames}, verified {len(r.verdicts)} · window check: "
                  + ("[red]doubtful[/] — " if r.check.doubtful else "") + r.check.reason
                  + (" · [red]possibly reverse-wound[/]" if r.reverse.suspect else ""))
    console.print("best days: " + ", ".join(f"{d:%a %-d %b} {m:.1f}" for d, m in r.check.best_days))
    jp = pipeline.save(r)
    hp = arep.write(out / f"align_{r.key}.html", r)
    console.print(f"wrote {jp} and {hp}")


@app.command()
def write(
    roll: str = typer.Argument(..., help="roll key (the assignments file name), e.g. 00007044 or a scan folder"),
    folder: Path = typer.Option(None, help="the scan folder to write into; default: where the roll was aligned from"),
    alias: str = typer.Option(None, "--as", help="the assignments/facts files are named this instead of the roll"),
    dry_run: bool = typer.Option(True, "--dry-run/--write", help="show the plan (default) or run exiftool"),
    force: bool = typer.Option(False, help="write frames the sidecar says are already written as they stand"),
    yes: bool = typer.Option(False, "--yes", "-y", help="write without asking"),
) -> None:
    """Write the confirmed frames' dates, offsets, GPS and keywords into the scan files.

    Only frames confirmed in the review UI are written; exiftool keeps `<name>_original` beside
    each file it touches. The argfile it runs is saved under `.filmgeo/writes/` either way.
    """
    from filmgeo.align.overrides import RollOverrides
    from filmgeo.align.pipeline import load_verdicts
    from filmgeo.signals.user_facts import RollFacts
    from filmgeo.write import exiftool as w, ops, sidecar

    key, _ = _roll_key(roll)
    if alias:
        key = alias
    try:
        a = w.load_assignments(key)
        target = folder or (Path(roll) if Path(roll).expanduser().is_dir() else None)
        if target is None and Path(str(a.get("origin") or "")).expanduser().is_dir():
            target = Path(a["origin"]).expanduser()
        files = w.scan_files(target) if target else None
        current = w.current_tags(files) if files else None
        written = sidecar.written_frames(target) if target else None
        facts = RollFacts.load(key)
        p = w.plan(key, target, a, facts, files=files, current=current, written=written, force=force)
    except w.WriteError as e:
        console.print(f"[red]{e}[/]")
        raise typer.Exit(2)

    table = Table(title=f"{key}: {len(p.frames)} of {p.n_total} frames to write -> {p.folder}")
    for col in ("#", "file", "now", "new local time", "offset", "GPS", "keywords", "action"):
        table.add_column(col)
    rows = {f.number: f for f in p.frames}
    skips = {s.number: s for s in p.skipped}
    for n in sorted({*rows, *skips}):
        if n in rows:
            f = rows[n]
            gps = f"{f.lat:.5f}, {f.lon:.5f}" if f.lat is not None else "[yellow]—[/]"
            prov = " ".join(k.removeprefix(w.PROVENANCE_PREFIX) for k in f.keywords if k.startswith(w.PROVENANCE_PREFIX))
            table.add_row(str(n), f.path.name, f.current or "[dim]none[/]", f.local, f.offset, gps, prov, "[green]write[/]")
        else:
            s = skips[n]
            table.add_row(str(n), s.path.name if s.path else "?", "", "", "", "", "", f"[dim]{s.why}[/]")
    console.print(table)
    argfile = w.save_argfile(p)
    console.print(f"argfile: {argfile}" + (f"  · keywords: {', '.join(k for k in p.frames[0].keywords if not k.startswith(w.PROVENANCE_PREFIX))}" if p.frames else ""))
    if not p.frames:
        unchanged = sum(s.why == "unchanged" for s in p.skipped)
        console.print("[green]nothing to write[/] — every confirmed frame is already written as it stands (--force to write anyway)" if unchanged
                      else "[yellow]nothing to write[/] — confirm frames in the review UI first")
        return
    if dry_run:
        console.print("dry run; add [bold]--write[/] to run exiftool")
        return
    if not yes and not typer.confirm(f"Write {len(p.frames)} files in {p.folder}? Originals are copied to {p.folder / ops.BACKUP_DIR} first"):
        raise typer.Exit(0)
    verdicts = {n: v.__dict__ for n, v in load_verdicts(key).items()}
    res = ops.write_roll(p, assignments=a, verdicts=verdicts, facts=facts, overrides=RollOverrides.load(key))
    for line in res.warnings:
        console.print(f"[yellow]exiftool:[/] {line}")
    vt = Table(title=f"read back: {sum(c.ok for c in res.checks)} of {len(res.checks)} verified")
    for col in ("#", "file", "result"):
        vt.add_column(col)
    for c in res.checks:
        vt.add_row(str(c.number), c.file, "[green]ok[/]" if c.ok else "[red]" + "; ".join(c.problems) + "[/]")
    console.print(vt)
    console.print(f"backed up {len(res.backed_up)} new originals to {p.folder / ops.BACKUP_DIR} · record {res.record}"
                  + (f" · sidecar {res.sidecar}" if res.sidecar else ""))
    if not res.ok:
        console.print("[red]some frames did not read back as written[/] — `filmgeo restore` puts the files back")
        raise typer.Exit(1)


@app.command()
def restore(folder: Path = typer.Argument(..., help="the scan folder"), yes: bool = typer.Option(False, "--yes", "-y")) -> None:
    """Put a roll's scans back as they were before filmgeo wrote them: exiftool's _original first, the backup folder second."""
    from filmgeo.write import ops

    if not yes and not typer.confirm(f"Restore every scan in {folder} from its _original or {folder / ops.BACKUP_DIR}?"):
        raise typer.Exit(0)
    try:
        done = ops.restore(folder)
    except w_error() as e:
        console.print(f"[red]{e}[/]")
        raise typer.Exit(2)
    for r in done:
        console.print(f"  {r.file}: {r.how}")
    console.print(f"restored {sum(r.how != 'nothing to restore' for r in done)} of {len(done)}")


@app.command()
def clear(
    folder: Path = typer.Argument(..., help="the scan folder"),
    everything: bool = typer.Option(False, "--all", help="also remove the written dates, offsets, GPS and Make/Model (descriptive keywords stay)"),
    yes: bool = typer.Option(False, "--yes", "-y"),
) -> None:
    """Remove filmgeo's provenance keywords from a roll's scans (and with --all, everything it wrote)."""
    from filmgeo.write import ops

    what = "the filmgeo: keywords and the written dates, GPS and camera" if everything else "the filmgeo: keywords"
    if not yes and not typer.confirm(f"Remove {what} from every scan in {folder}?"):
        raise typer.Exit(0)
    removed = ops.clear(folder, everything=everything)
    for name, items in removed.items():
        console.print(f"  {name}: removed {', '.join(items)}")
    console.print(f"cleared {len(removed)} files" if removed else "nothing to clear")


def w_error():
    from filmgeo.write.exiftool import WriteError

    return WriteError


@app.command()
def serve(
    rolls: list[str] = typer.Argument(None, help="scan folders or roll keys to open, besides what .filmgeo/ already knows"),
    host: str = typer.Option("127.0.0.1", help="bind address; keep it local, nothing authenticates"),
    port: int = typer.Option(8765),
    open_browser: bool = typer.Option(True, "--open/--no-open", help="open the review UI in the default browser"),
) -> None:
    """Run the local review API (and the web UI once it is built) on 127.0.0.1.

    Needs `--extra api`; run it from Terminal.app so Photos derivatives are readable.
    """
    from filmgeo.api.app import serve as do_serve
    from filmgeo.api.state import Store

    origins: dict[str, str] = {}
    for roll in rolls or []:
        key, _ = _roll_key(roll)
        origins[key] = roll
    do_serve(Store(origins=origins), host=host, port=port, open_browser=open_browser)


if __name__ == "__main__":
    app()
