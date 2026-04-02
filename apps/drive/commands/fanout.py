"""Parallel command execution across multiple sessions."""
import concurrent.futures

import click

from modules import sentinel
from modules.errors import DriveError
from modules.output import emit


def _exec_one(session: str, cmd: str, timeout: float) -> dict:
    """Execute in one session, return result dict. Never raises."""
    try:
        exit_code, output = sentinel.run_and_wait(session, cmd, timeout=timeout)
        return {
            "session": session,
            "ok": exit_code == 0,
            "exit_code": exit_code,
            "output": output,
        }
    except DriveError as e:
        return {
            "session": session,
            "ok": False,
            "error": e.code,
            "message": e.message,
        }


@click.command()
@click.argument("cmd")
@click.option("--targets", required=True, help="Comma-separated session names.")
@click.option("--timeout", default=30.0, help="Max seconds per session.")
@click.option("--json", "as_json", is_flag=True, help="Output JSON.")
def fanout(cmd: str, targets: str, timeout: float, as_json: bool):
    """Run a command in parallel across multiple sessions.

    Uses sentinel-based completion detection per session.
    """
    MAX_FANOUT_WORKERS = 16

    session_names = [s.strip() for s in targets.split(",") if s.strip()]
    if not session_names:
        click.echo("Error: No targets specified.", err=True)
        raise SystemExit(1)
    if len(session_names) > 50:
        click.echo("Error: Too many targets (max 50).", err=True)
        raise SystemExit(1)

    results = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=min(len(session_names), MAX_FANOUT_WORKERS)) as pool:
        futures = {
            pool.submit(_exec_one, name, cmd, timeout): name
            for name in session_names
        }
        for future in concurrent.futures.as_completed(futures):
            results.append(future.result())

    order = {name: i for i, name in enumerate(session_names)}
    results.sort(key=lambda r: order.get(r["session"], 999))

    all_ok = all(r.get("ok", False) for r in results)

    if as_json:
        emit(
            {"ok": all_ok, "command": cmd, "results": results},
            json=True,
            human_lines="",
        )
    else:
        for r in results:
            status = "ok" if r.get("ok") else "FAIL"
            click.echo(f"--- {r['session']} [{status}] ---")
            if "output" in r:
                click.echo(r["output"])
            elif "message" in r:
                click.echo(f"  Error: {r['message']}")
            click.echo()

    if not all_ok:
        raise SystemExit(1)
