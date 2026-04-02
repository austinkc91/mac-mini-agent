"""Instrumented command execution with sentinel-based completion detection."""
import click

from modules import sentinel
from modules.errors import DriveError
from modules.output import emit, emit_error


@click.command()
@click.argument("session")
@click.argument("cmd")
@click.option("--timeout", default=30.0, help="Max seconds to wait. 0 = no limit.")
@click.option("--pane", default=None, help="Target pane index.")
@click.option("--json", "as_json", is_flag=True, help="Output JSON.")
def run(session: str, cmd: str, timeout: float, pane: str | None, as_json: bool):
    """Run a command in a session and wait for completion.

    Uses a sentinel protocol to reliably detect when the command
    finishes and capture its exit code.
    """
    try:
        exit_code, output = sentinel.run_and_wait(
            session, cmd, pane=pane, timeout=timeout
        )
        data = {
            "ok": exit_code == 0,
            "session": session,
            "command": cmd,
            "exit_code": exit_code,
            "output": output,
        }
        human = output if exit_code == 0 else f"[exit {exit_code}]\n{output}"
        emit(data, json=as_json, human_lines=human)
        if exit_code != 0:
            raise SystemExit(exit_code)
    except DriveError as e:
        emit_error(e, json=as_json)
