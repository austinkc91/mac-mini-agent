"""ocr — Extract text from screen via OCR."""

import json
import os
import uuid

import click

from modules import ocr, screen_capture, element_store
from modules.app_control import find_app, frontmost
from modules.errors import SteerError


@click.command("ocr")
@click.option("--image", default=None, help="Path to a screenshot PNG")
@click.option("--app", default=None, help="Target app name (default: frontmost)")
@click.option("--screen", "screen_idx", type=int, default=None, help="Screen index")
@click.option("--confidence", type=float, default=0.5, help="Minimum confidence 0.0-1.0")
@click.option("--store", is_flag=True, help="Save OCR results as snapshot for click --on")
@click.option("--json", "as_json", is_flag=True, help="Output JSON")
def ocr_cmd(image, app, screen_idx, confidence, store, as_json):
    """Extract text from a screenshot via OCR."""
    try:
        if image:
            image_path = image
            app_name = os.path.splitext(os.path.basename(image))[0]
        elif screen_idx is not None and app is None:
            image_path = screen_capture.capture_screen(screen_idx)
            app_name = f"screen-{screen_idx}"
        elif app:
            image_path = screen_capture.capture_app(app)
            app_name = app
        else:
            front = frontmost()
            if front:
                app_name = front["name"]
                image_path = screen_capture.capture_app(app_name)
            else:
                image_path = screen_capture.capture_display()
                app_name = "desktop"

        results = ocr.recognize(image_path, minimum_confidence=confidence)
        snap_id = None

        if store:
            snap_id = uuid.uuid4().hex[:8]
            os.makedirs(element_store.STORE_DIR, exist_ok=True)
            # Copy screenshot to store
            import shutil
            store_path = os.path.join(element_store.STORE_DIR, f"{snap_id}.png")
            if image_path != store_path:
                shutil.copy2(image_path, store_path)
            elements = ocr.to_elements(results)
            element_store.save(snap_id, elements)

        if as_json:
            out = {
                "app": app_name,
                "count": len(results),
                "results": [r.to_dict() for r in results],
            }
            if snap_id:
                out["snapshot"] = snap_id
            click.echo(json.dumps(out))
        else:
            click.echo(f"app: {app_name}")
            click.echo(f"text regions: {len(results)}")
            if snap_id:
                click.echo(f"snapshot: {snap_id}")
            click.echo("")
            for r in results:
                t = r.text[:60]
                click.echo(f'  "{t}"  ({r.x},{r.y} {r.width}x{r.height})  conf:{r.confidence:.2f}')

    except SteerError as e:
        if as_json:
            click.echo(json.dumps({"error": str(e), "ok": False}))
        else:
            click.echo(f"Error: {e}", err=True)
        raise SystemExit(1)
