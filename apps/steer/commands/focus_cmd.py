"""focus — Show the currently focused UI element."""

import json

import click

from modules import accessibility, app_control
from modules.errors import SteerError


@click.command("focus")
@click.option("--app", default=None, help="Target app name (default: frontmost)")
@click.option("--json", "as_json", is_flag=True, help="Output JSON")
def focus_cmd(app, as_json):
    """Show the currently focused UI element."""
    try:
        if app:
            app_name = app
        else:
            front = app_control.frontmost()
            if front:
                app_name = front["name"]
            else:
                if as_json:
                    click.echo(json.dumps({"app": None, "focused": None}))
                else:
                    click.echo("app: (none)")
                    click.echo("focused: (none)")
                return

        el = accessibility.focused_element(app_name)

        if el is None:
            if as_json:
                click.echo(json.dumps({"app": app_name, "focused": None}))
            else:
                click.echo(f"app: {app_name}")
                click.echo("focused: (none)")
            return

        if as_json:
            click.echo(json.dumps({"app": app_name, "focused": el}))
        else:
            lbl = el.get("label", "") or el.get("value", "") or "(no label)"
            click.echo(f"app: {app_name}")
            click.echo(f'focused: {el["role"]} "{lbl}"  ({el["x"]},{el["y"]} {el["width"]}x{el["height"]})')
            if el.get("value") and el.get("label"):
                click.echo(f'  value: "{str(el["value"])[:80]}"')

    except SteerError as e:
        if as_json:
            click.echo(json.dumps({"error": str(e), "ok": False}))
        else:
            click.echo(f"Error: {e}", err=True)
        raise SystemExit(1)
