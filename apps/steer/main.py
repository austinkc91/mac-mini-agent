"""steer — macOS GUI automation CLI for AI agents."""

import os
import sys

# Suppress uv VIRTUAL_ENV mismatch warning that corrupts JSON output
os.environ.pop("VIRTUAL_ENV", None)

import click

from modules.tools import ensure_display

# Verify display is available
try:
    ensure_display()
except Exception:
    pass

from commands.see import see
from commands.click_cmd import click_cmd
from commands.type_cmd import type_cmd
from commands.hotkey_cmd import hotkey_cmd
from commands.scroll_cmd import scroll_cmd
from commands.drag_cmd import drag_cmd
from commands.apps_cmd import apps_cmd
from commands.screens_cmd import screens_cmd
from commands.window_cmd import window_cmd
from commands.ocr_cmd import ocr_cmd
from commands.focus_cmd import focus_cmd
from commands.find_cmd import find_cmd
from commands.clipboard_cmd import clipboard_cmd
from commands.wait_cmd import wait_cmd
from commands.dismiss_cmd import dismiss_cmd
from commands.status_cmd import status_cmd
from commands.read_cmd import read_text_cmd


@click.group()
@click.version_option("0.3.0", prog_name="steer")
def cli():
    """macOS GUI automation CLI for AI agents. Eyes and hands on your desktop."""
    pass


cli.add_command(see)
cli.add_command(click_cmd)
cli.add_command(type_cmd)
cli.add_command(hotkey_cmd)
cli.add_command(scroll_cmd)
cli.add_command(drag_cmd)
cli.add_command(apps_cmd)
cli.add_command(screens_cmd)
cli.add_command(window_cmd)
cli.add_command(ocr_cmd)
cli.add_command(focus_cmd)
cli.add_command(find_cmd)
cli.add_command(clipboard_cmd)
cli.add_command(wait_cmd)
cli.add_command(dismiss_cmd)
cli.add_command(status_cmd)
cli.add_command(read_text_cmd)


if __name__ == "__main__":
    cli()
