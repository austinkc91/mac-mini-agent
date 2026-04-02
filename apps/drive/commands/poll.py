"""Poll session output for a regex pattern match."""
import re
import time

import click

from modules import session_manager
from modules.errors import DriveError, PatternNotFoundError
from modules.output import emit, emit_error


@click.command()
@click.argument("session")
@click.option("--until", "pattern", required=True, help="Regex pattern to match.")
@click.option("--timeout", default=30.0, help="Max seconds to wait.")
@click.option("--interval", default=0.5, help="Seconds between polls.")
@click.option("--pane", default=None, help="Target pane index.")
@click.option("--json", "as_json", is_flag=True, help="Output JSON.")
def poll(
    session: str,
    pattern: str,
    timeout: float,
    interval: float,
    pane: str | None,
    as_json: bool,
):
    """Wait for session output to match a regex pattern."""
    try:
        compiled = re.compile(pattern)
    except re.error as e:
        click.echo(f"Error: Invalid regex: {e}", err=True)
        raise SystemExit(1)

    try:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            content = session_manager.capture_pane(session, pane=pane, start_line=-200)
            match = compiled.search(content)
            if match:
                emit(
                    {
                        "ok": True,
                        "session": session,
                        "pattern": pattern,
                        "match": match.group(0),
                        "content": content,
                    },
                    json=as_json,
                    human_lines=[f"Pattern matched: {match.group(0)}", content],
                )
                return
            time.sleep(interval)

        raise PatternNotFoundError(pattern, session, timeout)
    except DriveError as e:
        emit_error(e, json=as_json)
