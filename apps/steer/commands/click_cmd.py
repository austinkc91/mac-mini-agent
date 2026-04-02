"""click — Click an element by ID, label, or coordinates."""

import json

import click

from modules import mouse_control, element_store, screen_capture, keyboard
from modules.errors import SteerError


@click.command("click")
@click.option("--on", "target", default=None, help="Element ID (B1) or label text")
@click.option("-x", type=float, default=None, help="X coordinate")
@click.option("-y", type=float, default=None, help="Y coordinate")
@click.option("--snapshot", default=None, help="Snapshot ID to resolve element from")
@click.option("--screen", "screen_idx", type=int, default=None, help="Screen index for coordinate translation")
@click.option("--double", is_flag=True, help="Double-click")
@click.option("--right", is_flag=True, help="Right-click")
@click.option("--middle", is_flag=True, help="Middle-click")
@click.option("--modifier", default=None, help="Modifier keys: ctrl, shift, alt, super (combine with +)")
@click.option("--json", "as_json", is_flag=True, help="Output JSON")
def click_cmd(target, x, y, snapshot, screen_idx, double, right, middle, modifier, as_json):
    """Click an element by ID, label, or coordinates."""
    try:
        if right and middle:
            raise click.UsageError("Cannot combine --right and --middle")

        label = ""
        if target:
            el = element_store.resolve(target, snap=snapshot)
            px, py = element_store.center_of(el)
            label = f"{el.get('id', '?')} \"{el.get('label', '')}\""
        elif x is not None and y is not None:
            if screen_idx is not None:
                info = screen_capture.screen_info(screen_idx)
                if info:
                    px = x + info.origin_x
                    py = y + info.origin_y
                else:
                    raise click.UsageError(
                        f"Screen index {screen_idx} not found. Use 'steer screens' to list available displays."
                    )
            else:
                px, py = x, y
        else:
            raise click.UsageError("Provide --on <element> or -x/-y coordinates")

        # Map button: 1=left, 2=middle, 3=right
        button = 3 if right else (2 if middle else 1)
        count = 2 if double else 1
        mods = keyboard.parse_modifiers(modifier) if modifier else None

        mouse_control.click(x=px, y=py, button=button, count=count, modifiers=mods)

        if as_json:
            click.echo(json.dumps({
                "action": "click", "x": int(px), "y": int(py),
                "label": label, "ok": True,
            }))
        else:
            mod_str = f"[{modifier}] " if modifier else ""
            verb = "Double-clicked" if double else ("Right-clicked" if right else ("Middle-clicked" if middle else "Clicked"))
            tgt = label if label else f"({int(px)}, {int(py)})"
            if label:
                tgt += f" at ({int(px)}, {int(py)})"
            click.echo(f"{mod_str}{verb} {tgt}")

    except SteerError as e:
        if as_json:
            click.echo(json.dumps({"error": str(e), "ok": False}))
        else:
            click.echo(f"Error: {e}", err=True)
        raise SystemExit(1)
