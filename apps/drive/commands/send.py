"""Raw send-keys for interactive tools."""
import click

from modules import session_manager
from modules.errors import DriveError
from modules.output import emit, emit_error


@click.command()
@click.argument("session")
@click.argument("text")
@click.option("--pane", default=None, help="Target pane index.")
@click.option("--enter/--no-enter", default=True, help="Append Enter key (default: yes).")
@click.option("--json", "as_json", is_flag=True, help="Output JSON.")
def send(session: str, text: str, pane: str | None, enter: bool, as_json: bool):
    """Send raw keystrokes to a session.

    Unlike 'run', this does NOT wait for completion. Use for
    interactive tools (vim, ipython, etc.) where sentinel
    detection would interfere.
    """
    try:
        session_manager.send_keys(session, text, pane=pane, enter=enter, literal=True)
        emit(
            {"ok": True, "action": "send", "session": session, "text": text, "enter": enter},
            json=as_json,
            human_lines=f"Sent to {session}: {text}" + (" [Enter]" if enter else ""),
        )
    except DriveError as e:
        emit_error(e, json=as_json)
