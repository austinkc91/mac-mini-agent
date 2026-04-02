"""scroll — Scroll in a direction."""

import json

import click

from modules import mouse_control


@click.command("scroll")
@click.argument("direction")
@click.argument("lines", type=int, default=3)
@click.option("-x", type=int, default=None, help="X position to scroll at")
@click.option("-y", type=int, default=None, help="Y position to scroll at")
@click.option("--json", "as_json", is_flag=True, help="Output JSON")
def scroll_cmd(direction, lines, x, y, as_json):
    """Scroll in a direction by N lines. Moves mouse to (x,y) first, or center of screen."""
    mouse_control.scroll(direction, lines, x=x, y=y)

    if as_json:
        click.echo(json.dumps({
            "action": "scroll", "direction": direction,
            "lines": lines, "ok": True,
        }))
    else:
        click.echo(f"Scrolled {direction} {lines} lines")
