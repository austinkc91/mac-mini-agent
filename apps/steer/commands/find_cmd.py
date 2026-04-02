"""find — Search elements by text in a snapshot."""

import json
import subprocess
import sys

import click

from modules import element_store
from modules.errors import SteerError


def _take_fresh_snapshot():
    """Take a fresh snapshot by running 'see --json' and return (snap_id, elements)."""
    result = subprocess.run(
        [sys.executable, "main.py", "see", "--json"],
        capture_output=True, text=True, timeout=15,
    )
    if result.returncode != 0:
        return None
    try:
        data = json.loads(result.stdout)
        snap_id = data.get("snapshot", "")
        # After see runs, element_store will have the snapshot on disk
        els = element_store.load(snap_id)
        if els:
            return snap_id, els
    except (json.JSONDecodeError, KeyError):
        pass
    # Fallback: get latest from store (see should have saved it)
    return element_store.latest()


def _match_element(el, query_lower, exact):
    """Check if an element matches the query across all text fields."""
    fields = [
        el.get("label", "") or "",
        el.get("value", "") or "",
        el.get("name", "") or "",
    ]
    for field in fields:
        fl = field.lower()
        if exact:
            if fl == query_lower:
                return True
        else:
            if query_lower in fl:
                return True
    return False


@click.command("find")
@click.argument("query")
@click.option("--snapshot", default=None, help="Snapshot ID to search in")
@click.option("--exact", is_flag=True, help="Exact match only")
@click.option("--refresh", is_flag=True, help="Take a fresh snapshot before searching")
@click.option("--json", "as_json", is_flag=True, help="Output JSON")
def find_cmd(query, snapshot, exact, refresh, as_json):
    """Search elements by text in the latest snapshot."""
    try:
        if snapshot:
            els = element_store.load(snapshot)
            if els is None:
                from modules.errors import NoSnapshot
                raise NoSnapshot()
            snap_id = snapshot
        elif refresh:
            result = _take_fresh_snapshot()
            if result is None:
                from modules.errors import NoSnapshot
                raise NoSnapshot()
            snap_id, els = result
        else:
            result = element_store.latest()
            if result is None:
                # Auto-refresh if no snapshot exists
                result = _take_fresh_snapshot()
                if result is None:
                    from modules.errors import NoSnapshot
                    raise NoSnapshot()
            snap_id, els = result

        lq = query.lower()
        matches = [e for e in els if _match_element(e, lq, exact)]

        if as_json:
            click.echo(json.dumps({
                "ok": True,
                "snapshot": snap_id,
                "query": query,
                "count": len(matches),
                "matches": matches,
            }))
        else:
            click.echo(f"snapshot: {snap_id}")
            click.echo(f'query: "{query}"')
            click.echo(f"matches: {len(matches)}")
            click.echo("")
            if not matches:
                click.echo("  (no matches)")
            else:
                for el in matches:
                    lbl = el.get("label", "") or el.get("value", "") or ""
                    t = lbl[:50]
                    eid = el.get("id", "?").ljust(6)
                    erole = el.get("role", "?").ljust(14)
                    click.echo(f'  {eid} {erole} "{t}"  ({el["x"]},{el["y"]} {el["width"]}x{el["height"]})')

    except SteerError as e:
        if as_json:
            click.echo(json.dumps({"error": str(e), "ok": False}))
        else:
            click.echo(f"Error: {e}", err=True)
        raise SystemExit(1)
