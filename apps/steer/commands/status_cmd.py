"""status — Health check for steer automation."""

import json
import os

import click


@click.command("status")
@click.option("--json", "as_json", is_flag=True, help="Output JSON")
def status_cmd(as_json):
    """Quick health check: active app, screens, accessibility, OCR."""
    try:
        result = {}

        # Session info (macOS doesn't have Session 0/1 distinction)
        result["session"] = os.getsid(os.getpid()) if hasattr(os, "getsid") else 1

        # Active app
        try:
            from modules.app_control import frontmost
            front = frontmost()
            result["activeApp"] = front["name"] if front else None
        except Exception:
            result["activeApp"] = None

        # Screen count and info
        try:
            from modules.screen_capture import list_screens
            screens = list_screens()
            result["screenCount"] = len(screens)
            result["screens"] = [
                {"index": s.index, "resolution": f"{s.width}x{s.height}",
                 "origin": f"({s.origin_x},{s.origin_y})", "main": s.is_main}
                for s in screens
            ]
        except Exception:
            result["screenCount"] = 0
            result["screens"] = []

        # Accessibility available
        try:
            from modules.accessibility import is_available
            result["accessibility"] = is_available()
        except Exception:
            result["accessibility"] = False

        # Tesseract OCR available
        try:
            import shutil
            result["tesseract"] = shutil.which("tesseract") is not None
        except Exception:
            result["tesseract"] = False

        # Dialog check
        try:
            from modules.accessibility import detect_dialogs
            dialogs = detect_dialogs()
            result["dialogs"] = len(dialogs)
            if dialogs:
                result["dialogTitles"] = [d["title"] for d in dialogs]
        except Exception:
            result["dialogs"] = 0

        # Overall health
        result["ok"] = (
            result.get("screenCount", 0) > 0
            and result.get("accessibility", False)
        )

        if as_json:
            click.echo(json.dumps(result))
        else:
            ok = "OK" if result["ok"] else "DEGRADED"
            click.echo(f"steer status: {ok}")
            click.echo(f"  active app: {result.get('activeApp', 'none')}")
            click.echo(f"  screens: {result.get('screenCount', 0)}")
            for s in result.get("screens", []):
                main = " (main)" if s["main"] else ""
                click.echo(f"    [{s['index']}] {s['resolution']} at {s['origin']}{main}")
            click.echo(f"  accessibility: {'yes' if result.get('accessibility') else 'NO'}")
            click.echo(f"  tesseract: {'yes' if result.get('tesseract') else 'NO'}")
            dialogs = result.get("dialogs", 0)
            if dialogs:
                click.echo(f"  dialogs: {dialogs} ACTIVE")
                for t in result.get("dialogTitles", []):
                    click.echo(f'    - "{t}"')
            else:
                click.echo("  dialogs: none")

    except Exception as e:
        if as_json:
            click.echo(json.dumps({"error": str(e), "ok": False}))
        else:
            click.echo(f"Error: {e}", err=True)
        raise SystemExit(1)
