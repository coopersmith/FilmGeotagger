"""Command-line entry point. Subcommands arrive milestone by milestone (see PLAN.md)."""

import typer

from filmgeo import __version__

app = typer.Typer(help="Film Roll Geotagger", no_args_is_help=True)


@app.command()
def version() -> None:
    """Print the engine version."""
    typer.echo(__version__)


if __name__ == "__main__":
    app()
