"""wait — Wait for an app to launch or a UI element to appear."""

import json
import time

import click

from modules import accessibility, app_control
from modules.errors import SteerError, WaitTimeout


@click.command("wait")
@click.option("--for", "wait_for", default=None, help="Element label or ID to wait for")
@click.option("--app", default=None, help="App name")
@click.option("--timeout", type=float, default=10, help="Max seconds to wait")
@click.option("--interval", type=float, default=0.5, help="Poll interval in seconds")
@click.option("--json", "as_json", is_flag=True, help="Output JSON")
def wait_cmd(wait_for, app, timeout, interval, as_json):
    """Wait for an app to launch or a UI element to appear."""
    try:
        if app is None and wait_for is None:
            raise click.UsageError("Provide --app, --for, or both")

        deadline = time.monotonic() + timeout

        if app and wait_for is None:
            # Wait for app to appear
            while time.monotonic() < deadline:
                if app_control.find_app(app) is not None:
                    if as_json:
                        click.echo(json.dumps({"action": "wait", "condition": "app", "app": app, "ok": True}))
                    else:
                        click.echo(f"Found {app}")
                    return
                time.sleep(interval)
            if as_json:
                click.echo(json.dumps({"action": "wait", "condition": "app", "app": app, "ok": False, "error": "timeout"}))
            raise WaitTimeout(f"app {app}", timeout)

        elif wait_for:
            # Wait for element to appear
            lq = wait_for.lower()
            while time.monotonic() < deadline:
                if app:
                    target_name = app
                else:
                    front = app_control.frontmost()
                    target_name = front["name"] if front else None

                if target_name:
                    elements = accessibility.walk(target_name)
                    match = None
                    for el in elements:
                        if el.get("id", "").lower() == lq:
                            match = el
                            break
                        if el.get("label", "").lower() == lq:
                            match = el
                            break
                        if lq in el.get("label", "").lower():
                            match = el
                            break

                    if match:
                        if as_json:
                            click.echo(json.dumps({
                                "action": "wait", "condition": "element",
                                "id": match.get("id", ""),
                                "label": match.get("label", ""),
                                "app": target_name, "ok": True,
                            }))
                        else:
                            click.echo(f'Found {match.get("id", "")} "{match.get("label", "")}" in {target_name}')
                        return

                time.sleep(interval)

            ctx = app or "frontmost"
            if as_json:
                click.echo(json.dumps({
                    "action": "wait", "condition": "element",
                    "for": wait_for, "app": ctx,
                    "ok": False, "error": "timeout",
                }))
            raise WaitTimeout(f'element "{wait_for}" in {ctx}', timeout)

    except WaitTimeout:
        raise SystemExit(1)
    except SteerError as e:
        if as_json:
            click.echo(json.dumps({"error": str(e), "ok": False}))
        else:
            click.echo(f"Error: {e}", err=True)
        raise SystemExit(1)
