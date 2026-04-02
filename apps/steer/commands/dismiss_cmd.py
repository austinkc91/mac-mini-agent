"""dismiss — Detect and dismiss modal dialogs."""

import json

import click

from modules import accessibility, mouse_control
from modules.errors import SteerError


@click.command("dismiss")
@click.option("--button", default=None, help="Preferred button to click (e.g. 'OK', 'Cancel', 'Close')")
@click.option("--json", "as_json", is_flag=True, help="Output JSON")
def dismiss_cmd(button, as_json):
    """Detect and dismiss modal dialog windows.

    Scans for popup/dialog windows and clicks a dismiss button.
    Without --button, prefers: Close > Cancel > No > OK.
    """
    try:
        dialogs = accessibility.detect_dialogs()

        if not dialogs:
            if as_json:
                click.echo(json.dumps({"action": "dismiss", "found": 0, "dismissed": 0, "ok": True}))
            else:
                click.echo("No dialogs found")
            return

        dismissed = 0
        dismissed_list = []

        # Priority order for auto-dismissing (safest first)
        safe_buttons = ["close", "cancel", "no", "don't save", "ok", "yes", "abort", "retry"]

        for dialog in dialogs:
            buttons = dialog.get("buttons", [])
            if not buttons:
                continue

            target_btn = None

            if button:
                # User specified a button preference
                btn_lower = button.lower()
                for b in buttons:
                    if b["label"].lower() == btn_lower:
                        target_btn = b
                        break
            else:
                # Auto-select safest dismiss button
                for preferred in safe_buttons:
                    for b in buttons:
                        if b["label"].lower() == preferred:
                            target_btn = b
                            break
                    if target_btn:
                        break

            if target_btn and target_btn["width"] > 0:
                cx = target_btn["x"] + target_btn["width"] // 2
                cy = target_btn["y"] + target_btn["height"] // 2
                mouse_control.click(x=cx, y=cy)
                dismissed += 1
                dismissed_list.append({
                    "dialog": dialog["title"],
                    "button": target_btn["label"],
                })

        if as_json:
            click.echo(json.dumps({
                "action": "dismiss",
                "found": len(dialogs),
                "dismissed": dismissed,
                "details": dismissed_list,
                "ok": True,
            }))
        else:
            click.echo(f"Found {len(dialogs)} dialog(s), dismissed {dismissed}")
            for d in dismissed_list:
                click.echo(f'  "{d["dialog"]}" -> clicked "{d["button"]}"')

    except SteerError as e:
        if as_json:
            click.echo(json.dumps({"error": str(e), "ok": False}))
        else:
            click.echo(f"Error: {e}", err=True)
        raise SystemExit(1)
