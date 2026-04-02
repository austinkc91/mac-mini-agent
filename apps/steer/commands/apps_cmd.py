"""apps — List, launch, or activate applications."""

import json

import click

from modules import app_control
from modules.errors import SteerError


@click.command("apps")
@click.argument("action", default="list")
@click.argument("name", default=None, required=False)
@click.option("--json", "as_json", is_flag=True, help="Output JSON")
def apps_cmd(action, name, as_json):
    """List running apps, launch, or activate by name."""
    try:
        action = action.lower()

        if action == "list":
            apps = app_control.list_apps()
            if as_json:
                click.echo(json.dumps(apps))
            else:
                for a in apps:
                    star = " *" if a["isActive"] else ""
                    click.echo(f"  {a['name']:<25} pid:{a['pid']}{star}")

        elif action == "launch":
            if not name:
                raise click.UsageError("Provide app name")
            app_control.launch(name)
            if as_json:
                click.echo(json.dumps({"action": "launch", "app": name, "ok": True}))
            else:
                click.echo(f"Launched {name}")

        elif action in ("activate", "focus"):
            if not name:
                raise click.UsageError("Provide app name")
            app_control.activate(name)
            if as_json:
                click.echo(json.dumps({"action": "activate", "app": name, "ok": True}))
            else:
                click.echo(f"Activated {name}")

        else:
            raise click.UsageError("Action must be: list, launch, activate")

    except SteerError as e:
        if as_json:
            click.echo(json.dumps({"error": str(e), "ok": False}))
        else:
            click.echo(f"Error: {e}", err=True)
        raise SystemExit(1)
