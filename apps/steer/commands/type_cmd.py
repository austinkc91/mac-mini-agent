"""type — Type text into the focused element."""

import json
import time

import click

from modules import keyboard, mouse_control, element_store
from modules.errors import SteerError


@click.command("type")
@click.argument("text")
@click.option("--into", default=None, help="Target element ID or label — clicks to focus first")
@click.option("--snapshot", default=None, help="Snapshot ID")
@click.option("--screen", "screen_idx", type=int, default=None, help="Screen index")
@click.option("--clear", is_flag=True, help="Clear field first (Ctrl+A, Delete)")
@click.option("--json", "as_json", is_flag=True, help="Output JSON")
def type_cmd(text, into, snapshot, screen_idx, clear, as_json):
    """Type text into the focused element, or click a target first."""
    try:
        if into:
            el = element_store.resolve(into, snap=snapshot)
            cx, cy = element_store.center_of(el)
            mouse_control.click(x=cx, y=cy)
            time.sleep(0.1)

        if clear:
            keyboard.hotkey("ctrl+a")
            time.sleep(0.02)
            keyboard.hotkey("delete")
            time.sleep(0.02)

        keyboard.type_text(text)

        if as_json:
            escaped = text.replace('"', '\\"')
            click.echo(json.dumps({"action": "type", "text": text, "ok": True}))
        else:
            into_msg = f" into {into}" if into else ""
            click.echo(f'Typed "{text}"{into_msg}')

    except SteerError as e:
        if as_json:
            click.echo(json.dumps({"error": str(e), "ok": False}))
        else:
            click.echo(f"Error: {e}", err=True)
        raise SystemExit(1)
