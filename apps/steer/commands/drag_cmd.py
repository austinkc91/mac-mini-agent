"""drag — Drag from one point to another."""

import json

import click

from modules import mouse_control, element_store, screen_capture, keyboard
from modules.errors import SteerError


@click.command("drag")
@click.option("--from", "from_el", default=None, help="Source element ID or label")
@click.option("--from-x", type=float, default=None, help="Source X coordinate")
@click.option("--from-y", type=float, default=None, help="Source Y coordinate")
@click.option("--to", "to_el", default=None, help="Destination element ID or label")
@click.option("--to-x", type=float, default=None, help="Destination X coordinate")
@click.option("--to-y", type=float, default=None, help="Destination Y coordinate")
@click.option("--snapshot", default=None, help="Snapshot ID")
@click.option("--screen", "screen_idx", type=int, default=None, help="Screen index")
@click.option("--modifier", default=None, help="Modifier keys")
@click.option("--steps", type=int, default=20, help="Intermediate drag points")
@click.option("--json", "as_json", is_flag=True, help="Output JSON")
def drag_cmd(from_el, from_x, from_y, to_el, to_x, to_y, snapshot, screen_idx, modifier, steps, as_json):
    """Drag from one element/point to another."""
    try:
        from_label = ""
        to_label = ""

        if from_el:
            el = element_store.resolve(from_el, snap=snapshot)
            sx, sy = element_store.center_of(el)
            from_label = f"{el.get('id', '?')} \"{el.get('label', '')}\""
        elif from_x is not None and from_y is not None:
            sx, sy = from_x, from_y
        else:
            raise click.UsageError("Provide --from <element> or --from-x/--from-y")

        if to_el:
            el = element_store.resolve(to_el, snap=snapshot)
            dx, dy = element_store.center_of(el)
            to_label = f"{el.get('id', '?')} \"{el.get('label', '')}\""
        elif to_x is not None and to_y is not None:
            dx, dy = to_x, to_y
        else:
            raise click.UsageError("Provide --to <element> or --to-x/--to-y")

        mods = keyboard.parse_modifiers(modifier) if modifier else None
        mouse_control.drag(from_x=sx, from_y=sy, to_x=dx, to_y=dy, steps=steps, modifiers=mods)

        if as_json:
            click.echo(json.dumps({
                "action": "drag",
                "fromX": int(sx), "fromY": int(sy),
                "toX": int(dx), "toY": int(dy),
                "ok": True,
            }))
        else:
            mod_str = f"[{modifier}] " if modifier else ""
            src = from_label or f"({int(sx)}, {int(sy)})"
            dst = to_label or f"({int(dx)}, {int(dy)})"
            click.echo(f"{mod_str}Dragged {src} → {dst}")

    except SteerError as e:
        if as_json:
            click.echo(json.dumps({"error": str(e), "ok": False}))
        else:
            click.echo(f"Error: {e}", err=True)
        raise SystemExit(1)
