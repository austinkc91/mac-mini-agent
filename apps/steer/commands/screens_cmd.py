"""screens — List connected displays."""

import json

import click

from modules import screen_capture


@click.command("screens")
@click.option("--json", "as_json", is_flag=True, help="Output JSON")
def screens_cmd(as_json):
    """List connected displays with index, resolution, and position."""
    screens = screen_capture.list_screens()

    if as_json:
        click.echo(json.dumps([s.to_dict() for s in screens]))
    else:
        for s in screens:
            main = " (main)" if s.is_main else ""
            click.echo(
                f"  {s.index}  {s.name:<30} {s.width}x{s.height}  "
                f"at ({s.origin_x},{s.origin_y})  scale:{s.scale_factor}{main}"
            )
