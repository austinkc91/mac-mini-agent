"""read-text — Read text content from UI elements via accessibility tree.

More reliable than OCR for reading app content. Reads directly from the
accessibility tree with 100% accuracy.
"""

import json

import click

from modules import accessibility, app_control
from modules.errors import SteerError


@click.command("read-text")
@click.option("--app", default=None, help="Target app name (default: frontmost)")
@click.option("--role", default=None, help="Filter by element role (e.g. 'text', 'label')")
@click.option("--json", "as_json", is_flag=True, help="Output JSON")
def read_text_cmd(app, role, as_json):
    """Read text content from UI elements via the accessibility tree.

    More reliable than OCR. Returns all text values from interactive elements.
    Use --role to filter (e.g. --role text for editable fields, --role label for static text).
    """
    try:
        if app:
            app_name = app
        else:
            front = app_control.frontmost()
            if front:
                app_name = front["name"]
            else:
                if as_json:
                    click.echo(json.dumps({"app": None, "texts": [], "count": 0}))
                else:
                    click.echo("No active app")
                return

        elements = accessibility.walk(app_name)

        # Also get focused element value
        focused = accessibility.focused_element(app_name)
        focused_text = None
        if focused and focused.get("value"):
            focused_text = focused["value"]

        # Collect text from elements
        texts = []
        for el in elements:
            label = el.get("label", "")
            value = el.get("value", "")
            el_role = el.get("role", "")

            if role and role.lower() not in el_role.lower():
                continue

            if label or value:
                entry = {
                    "id": el.get("id", ""),
                    "role": el_role,
                    "label": label,
                }
                if value:
                    entry["value"] = value
                texts.append(entry)

        if as_json:
            out = {
                "app": app_name,
                "count": len(texts),
                "texts": texts,
            }
            if focused_text:
                out["focusedValue"] = focused_text
            click.echo(json.dumps(out))
        else:
            click.echo(f"app: {app_name}")
            click.echo(f"text elements: {len(texts)}")
            if focused_text:
                preview = focused_text[:200]
                click.echo(f'focused value: "{preview}"')
            click.echo("")
            for t in texts:
                eid = t.get("id", "?").ljust(6)
                erole = t.get("role", "?").ljust(14)
                label = t.get("label", "")[:50]
                click.echo(f'  {eid} {erole} "{label}"')
                if t.get("value"):
                    val_preview = t["value"][:100]
                    click.echo(f'         value: "{val_preview}"')

    except SteerError as e:
        if as_json:
            click.echo(json.dumps({"error": str(e), "ok": False}))
        else:
            click.echo(f"Error: {e}", err=True)
        raise SystemExit(1)
