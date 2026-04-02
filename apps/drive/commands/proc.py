"""Process management: list, kill, tree, top."""
import click

from modules import proc, session_manager
from modules.errors import DriveError
from modules.output import emit, emit_error


@click.group()
def proc_cmd():
    """Manage processes."""
    pass


@proc_cmd.command("list")
@click.option("--name", default=None, help="Filter by process name (case-insensitive substring).")
@click.option("--session", default=None, help="Filter by session name.")
@click.option("--parent", default=None, type=int, help="Filter by parent PID.")
@click.option("--cwd", default=None, help="Filter by working directory (prefix match).")
@click.option("--json", "as_json", is_flag=True, help="Output JSON.")
def list_cmd(name: str | None, session: str | None, parent: int | None, cwd: str | None, as_json: bool):
    """List processes owned by the current user."""
    try:
        processes = proc.list_processes(name=name, parent=parent, session=session, cwd=cwd)
        data = {
            "ok": True,
            "count": len(processes),
            "processes": [p.to_dict() for p in processes],
        }
        if as_json:
            emit(data, json=True, human_lines="")
        else:
            if not processes:
                click.echo("No matching processes.")
            else:
                for p in processes:
                    sess = f"  [{p.session}]" if p.session else ""
                    cpu = f"{p.cpu:>5.1f}%" if p.cpu is not None else "    -  "
                    cmd = "..." + p.command[-60:] if len(p.command) > 60 else p.command
                    click.echo(
                        f"  {p.pid:<8} {p.name:<20} {cpu}  "
                        f"{p.memory_mb:>7.1f}MB  {p.elapsed:>8}  {p.state}{sess}"
                        f"\n           {cmd}"
                    )
    except DriveError as e:
        emit_error(e, json=as_json)


@proc_cmd.command()
@click.argument("pid", required=False, type=int, default=None)
@click.option("--name", default=None, help="Kill all processes matching name.")
@click.option("--signal", "sig", default=15, type=int, help="Signal number (default: 15/SIGTERM).")
@click.option("--force", is_flag=True, help="Force kill immediately, skip graceful shutdown.")
@click.option("--tree", "tree", is_flag=True, help="Kill process and all its children.")
@click.option("--json", "as_json", is_flag=True, help="Output JSON.")
def kill(pid: int | None, name: str | None, sig: int, force: bool, tree: bool, as_json: bool):
    """Kill a process by PID or name."""
    if force:
        sig = 9

    if pid is None and name is None:
        emit_error(
            DriveError("Provide a PID argument or --name to kill"),
            json=as_json,
        )
        return

    try:
        result = proc.kill_process(pid=pid, name=name, sig=sig, tree=tree)
        data = result.to_dict()
        if as_json:
            emit(data, json=True, human_lines="")
        else:
            killed_str = ", ".join(str(p) for p in result.killed)
            click.echo(f"Killed: {killed_str}" if killed_str else "No processes killed.")
            if result.failed:
                for f in result.failed:
                    click.echo(f"  Failed: PID {f['pid']} ({f['error']})")
    except DriveError as e:
        emit_error(e, json=as_json)


@proc_cmd.command()
@click.argument("pid", required=False, type=int, default=None)
@click.option("--session", default=None, help="Resolve root PID from session instead.")
@click.option("--json", "as_json", is_flag=True, help="Output JSON.")
def tree(pid: int | None, session: str | None, as_json: bool):
    """Show process tree rooted at a PID or session."""
    if pid is None and session is None:
        emit_error(DriveError("Provide a PID argument or --session"), json=as_json)
        return

    try:
        if session is not None:
            pids = session_manager.get_session_pids(session)
            if not pids:
                from modules.errors import ProcessNotFoundError
                raise ProcessNotFoundError(name=f"session:{session}")
            pid = pids[0]

        tree_data = proc.process_tree(pid)
        data = {"ok": True, "root": pid, "tree": tree_data}

        if as_json:
            emit(data, json=True, human_lines="")
        else:
            def _print_tree(node: dict, indent: int = 0) -> None:
                prefix = "  " * indent + ("|- " if indent > 0 else "")
                click.echo(f"{prefix}{node['pid']} {node['name']}")
                for child in node.get("children", []):
                    _print_tree(child, indent + 1)
            _print_tree(tree_data)
    except DriveError as e:
        emit_error(e, json=as_json)


@proc_cmd.command()
@click.option("--pid", "pids", default=None, help="Comma-separated PIDs.")
@click.option("--session", default=None, help="Get snapshot for all processes in a session.")
@click.option("--json", "as_json", is_flag=True, help="Output JSON.")
def top(pids: str | None, session: str | None, as_json: bool):
    """One-shot resource snapshot for specific PIDs or a session."""
    try:
        pid_list: list[int] = []

        if session is not None:
            root_pids = session_manager.get_session_pids(session)
            for rp in root_pids:
                pid_list.append(rp)
                try:
                    import psutil
                    p = psutil.Process(rp)
                    for child in p.children(recursive=True):
                        pid_list.append(child.pid)
                except Exception:
                    pass
        elif pids is not None:
            pid_list = [int(p.strip()) for p in pids.split(",") if p.strip().isdigit()]

        if not pid_list:
            emit_error(DriveError("Provide --pid or --session"), json=as_json)
            return

        snapshot = proc.process_snapshot(pid_list)
        data = {
            "ok": True,
            "snapshot": [p.to_dict() for p in snapshot],
        }
        if as_json:
            emit(data, json=True, human_lines="")
        else:
            if not snapshot:
                click.echo("No processes found.")
            else:
                for p in snapshot:
                    cpu = f"{p.cpu:>5.1f}%" if p.cpu is not None else "    -  "
                    click.echo(
                        f"  {p.pid:<8} {p.name:<20} {cpu}  "
                        f"{p.memory_mb:>7.1f}MB  {p.elapsed:>8}  {p.state}"
                    )
    except DriveError as e:
        emit_error(e, json=as_json)
