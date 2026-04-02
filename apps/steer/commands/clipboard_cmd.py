"""clipboard — Read or write the system clipboard."""

import json

import click

from modules import clipboard_control
from modules.errors import SteerError


@click.command("clipboard")
@click.argument("action")
@click.argument("text", default=None, required=False)
@click.option("--type", "content_type", default="text", help="Content type: text | image")
@click.option("--file", "file_path", default=None, help="File path for image read/write")
@click.option("--json", "as_json", is_flag=True, help="Output JSON")
def clipboard_cmd(action, text, content_type, file_path, as_json):
    """Read or write the system clipboard."""
    try:
        action = action.lower()
        if action == "get":
            action = "read"
        elif action in ("set", "copy"):
            action = "write"

        if action == "read":
            if content_type == "text":
                content = clipboard_control.read_text()
                if as_json:
                    click.echo(json.dumps({
                        "action": "read", "type": "text",
                        "content": content or "", "ok": True,
                    }))
                else:
                    click.echo(content or "(clipboard empty)")

            elif content_type == "image":
                path = clipboard_control.read_image(save_to=file_path)
                if as_json:
                    click.echo(json.dumps({
                        "action": "read", "type": "image",
                        "file": path, "ok": True,
                    }))
                else:
                    click.echo(f"Saved clipboard image to {path}")
            else:
                raise click.UsageError("Type must be: text, image")

        elif action == "write":
            if content_type == "text":
                if not text:
                    raise click.UsageError("Provide text to write")
                clipboard_control.write_text(text)
                if as_json:
                    click.echo(json.dumps({
                        "action": "write", "type": "text",
                        "content": text, "ok": True,
                    }))
                else:
                    preview = text[:80] + ("..." if len(text) > 80 else "")
                    click.echo(f'Copied to clipboard: "{preview}"')

            elif content_type == "image":
                if not file_path:
                    raise click.UsageError("Provide --file path for image write")
                clipboard_control.write_image(file_path)
                if as_json:
                    click.echo(json.dumps({
                        "action": "write", "type": "image",
                        "file": file_path, "ok": True,
                    }))
                else:
                    click.echo(f"Copied image to clipboard from {file_path}")
            else:
                raise click.UsageError("Type must be: text, image")

        else:
            raise click.UsageError("Action must be: read, write")

    except SteerError as e:
        if as_json:
            click.echo(json.dumps({"error": str(e), "ok": False}))
        else:
            click.echo(f"Error: {e}", err=True)
        raise SystemExit(1)
