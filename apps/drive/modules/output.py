"""Output formatting for dual JSON/human mode."""
import json as json_mod
import sys

import click

from modules.errors import DriveError


def emit(data: dict, *, json: bool, human_lines: list[str] | str) -> None:
    """Print output in JSON or human-readable format."""
    if json:
        click.echo(json_mod.dumps(data, separators=(",", ":")))
    else:
        if isinstance(human_lines, str):
            click.echo(human_lines)
        else:
            for line in human_lines:
                click.echo(line)


def emit_error(err: DriveError, *, json: bool) -> None:
    """Print an error in JSON or human-readable format, then exit 1."""
    if json:
        click.echo(json_mod.dumps(err.to_dict(), separators=(",", ":")))
    else:
        click.echo(f"Error: {err.message}", err=True)
    sys.exit(1)
