"""window — Manage app windows."""

import json

import click

from modules import window_control
from modules.errors import SteerError


@click.command("window")
@click.argument("action")
@click.argument("app")
@click.option("-x", type=float, default=None, help="X position (for move)")
@click.option("-y", type=float, default=None, help="Y position (for move)")
@click.option("--width", "-w", type=float, default=None, help="Width (for resize)")
@click.option("--height", "-h", "h", type=float, default=None, help="Height (for resize)")
@click.option("--json", "as_json", is_flag=True, help="Output JSON")
def window_cmd(action, app, x, y, width, h, as_json):
    """Manage app windows: move, resize, minimize, fullscreen, close."""
    try:
        action = action.lower()

        if action == "list":
            windows = window_control.list_windows(app)
            if as_json:
                click.echo(json.dumps([w.to_dict() for w in windows]))
            else:
                for i, w in enumerate(windows):
                    title = w.title or "(untitled)"
                    flags = []
                    if w.is_minimized:
                        flags.append("minimized")
                    if w.is_fullscreen:
                        flags.append("fullscreen")
                    extra = f"  [{', '.join(flags)}]" if flags else ""
                    click.echo(f'  {i}  "{title}"  ({w.x},{w.y} {w.width}x{w.height}){extra}')

        elif action == "move":
            if x is None or y is None:
                raise click.UsageError("move requires -x and -y")
            window_control.move(app, x, y)
            if as_json:
                click.echo(json.dumps({"action": "move", "app": app, "x": int(x), "y": int(y), "ok": True}))
            else:
                click.echo(f"Moved {app} to ({int(x)}, {int(y)})")

        elif action == "resize":
            if width is None or h is None:
                raise click.UsageError("resize requires --width and --height")
            window_control.resize(app, width, h)
            if as_json:
                click.echo(json.dumps({"action": "resize", "app": app, "width": int(width), "height": int(h), "ok": True}))
            else:
                click.echo(f"Resized {app} to {int(width)}x{int(h)}")

        elif action == "minimize":
            window_control.minimize(app)
            if as_json:
                click.echo(json.dumps({"action": "minimize", "app": app, "ok": True}))
            else:
                click.echo(f"Minimized {app}")

        elif action == "restore":
            window_control.minimize(app, flag=False)
            if as_json:
                click.echo(json.dumps({"action": "restore", "app": app, "ok": True}))
            else:
                click.echo(f"Restored {app}")

        elif action == "fullscreen":
            window_control.fullscreen(app)
            if as_json:
                click.echo(json.dumps({"action": "fullscreen", "app": app, "ok": True}))
            else:
                click.echo(f"Toggled fullscreen for {app}")

        elif action == "close":
            window_control.close(app)
            if as_json:
                click.echo(json.dumps({"action": "close", "app": app, "ok": True}))
            else:
                click.echo(f"Closed {app} window")

        else:
            raise click.UsageError("Action must be: list, move, resize, minimize, restore, fullscreen, close")

    except SteerError as e:
        if as_json:
            click.echo(json.dumps({"error": str(e), "ok": False}))
        else:
            click.echo(f"Error: {e}", err=True)
        raise SystemExit(1)
