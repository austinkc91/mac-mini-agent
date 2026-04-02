"""see — Capture screenshot + accessibility/OCR element tree."""

import json
import os
import uuid

import click

from modules import screen_capture, accessibility, ocr, element_store
from modules.errors import SteerError


@click.command()
@click.option("--app", default=None, help="Target app name (default: frontmost)")
@click.option("--screen", "screen_idx", type=int, default=None, help="Screen index to capture")
@click.option("--ocr", "use_ocr", is_flag=True, help="Run OCR when accessibility tree is empty")
@click.option("--role", default=None, help="Filter elements by role")
@click.option("--json", "as_json", is_flag=True, help="Output JSON")
def see(app, screen_idx, use_ocr, role, as_json):
    """Capture screenshot + accessibility tree. Returns element map."""
    try:
        snap_id = uuid.uuid4().hex[:8]
        screenshot_dir = element_store.STORE_DIR
        os.makedirs(screenshot_dir, exist_ok=True)
        screenshot_path = os.path.join(screenshot_dir, f"{snap_id}.png")
        elements = []
        windows = []
        app_name = ""

        if screen_idx is not None and app is None:
            screen_capture.capture_screen(screen_idx, screenshot_path)
            app_name = f"screen-{screen_idx}"
        elif app:
            # Get windows once, reuse for capture and bounds
            from modules.app_control import find_app_windows
            app_windows = find_app_windows(app)
            if app_windows:
                screen_capture.capture_window(app_windows[0]["id"], screenshot_path)
            else:
                screen_capture.capture_display(screenshot_path)
            app_name = app
            elements = accessibility.walk(app)
            windows = [{"windowX": w["x"], "windowY": w["y"],
                        "windowWidth": w["width"], "windowHeight": w["height"],
                        "windowTitle": w.get("title"), "windowID": w["id"]}
                       for w in app_windows if w["width"] > 1 and w["height"] > 1]

            if not elements and use_ocr:
                ocr_results = ocr.recognize(screenshot_path)
                elements = ocr.to_elements(ocr_results)
        else:
            # Frontmost app
            from modules.app_control import frontmost, find_app_windows
            front = frontmost()
            if front:
                app_name = front["name"]
                app_windows = find_app_windows(app_name)
                if app_windows:
                    screen_capture.capture_window(app_windows[0]["id"], screenshot_path)
                else:
                    screen_capture.capture_display(screenshot_path)
                elements = accessibility.walk(app_name)
                windows = [{"windowX": w["x"], "windowY": w["y"],
                            "windowWidth": w["width"], "windowHeight": w["height"],
                            "windowTitle": w.get("title"), "windowID": w["id"]}
                           for w in app_windows if w["width"] > 1 and w["height"] > 1]

                if not elements and use_ocr:
                    ocr_results = ocr.recognize(screenshot_path)
                    elements = ocr.to_elements(ocr_results)
            else:
                screen_capture.capture_display(screenshot_path)
                app_name = "desktop"

        if elements:
            element_store.save(snap_id, elements)

        displayed = elements
        if role:
            role_lower = role.lower()
            displayed = [e for e in elements if role_lower in e.get("role", "").lower()]

        if as_json:
            out = {
                "snapshot": snap_id,
                "app": app_name,
                "screenshot": screenshot_path,
                "count": len(displayed),
                "windows": windows,
                "elements": displayed,
            }
            click.echo(json.dumps(out))
        else:
            click.echo(f"snapshot: {snap_id}")
            click.echo(f"app: {app_name}")
            click.echo(f"screenshot: {screenshot_path}")
            role_note = f" (filtered by {role})" if role else ""
            click.echo(f"elements: {len(displayed)}{role_note}")
            for w in windows:
                title = w.get("windowTitle", "")
                click.echo(f"  window: ({w['windowX']},{w['windowY']}) {w['windowWidth']}x{w['windowHeight']}  \"{title}\"")
            click.echo("")
            if not displayed and screen_idx is not None:
                click.echo("  (full screen capture — no element tree)")
            elif not displayed:
                click.echo("  (no interactive elements found for this app)")
            else:
                for el in displayed:
                    lbl = el.get("label", "") or el.get("value", "") or ""
                    t = lbl[:40]
                    eid = el.get("id", "?").ljust(6)
                    erole = el.get("role", "?").ljust(14)
                    click.echo(f"  {eid} {erole} \"{t}\"  ({el['x']},{el['y']} {el['width']}x{el['height']})")

    except SteerError as e:
        if as_json:
            click.echo(json.dumps({"error": str(e), "ok": False}))
        else:
            click.echo(f"Error: {e}", err=True)
        raise SystemExit(1)
