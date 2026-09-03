"""Command-line entry point. Subcommands arrive milestone by milestone (see PLAN.md)."""

from __future__ import annotations

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
def report(roll_key: str, pad_days: int = 2, k: int = 8, out: Path = Path(".filmgeo/report")) -> None:
    """Write the contact sheet for one hand-tagged roll, from cached vectors."""
    import numpy as np

    from filmgeo import eval_set, events as ev, report as rep, retrieve
    from filmgeo.config import SAME_MOMENT
    from filmgeo.embed.cache import VectorCache
    from filmgeo.photos import library

    assets = library.load()
    roll = next((r.clean() for r in eval_set.rolls(assets) if r.key == roll_key), None)
    if roll is None:
        raise typer.BadParameter(f"no hand-tagged roll {roll_key}")

    pool = library.candidates(assets, roll.start, roll.end, pad_days=pad_days)
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
                  f"pool {len(pool)} photos, +/-{pad_days}d | recall@{k} {hits}/{len(rows)}"),
    )
    console.print(f"wrote {path}  (recall@{k} {hits}/{len(rows)})")


if __name__ == "__main__":
    app()
