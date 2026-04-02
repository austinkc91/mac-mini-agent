"""hotkey — Press a key combination."""

import json

import click

from modules import keyboard


@click.command("hotkey")
@click.argument("combo")
@click.option("--json", "as_json", is_flag=True, help="Output JSON")
def hotkey_cmd(combo, as_json):
    """Press a key combination: ctrl+s, alt+tab, return, escape, etc."""
    keyboard.hotkey(combo)

    if as_json:
        click.echo(json.dumps({"action": "hotkey", "combo": combo, "ok": True}))
    else:
        click.echo(f"Pressed {combo}")
