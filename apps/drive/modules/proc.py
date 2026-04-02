"""Process management.

All process operations flow through this module.
Command files import from here; they never call psutil directly.
"""
import logging
import os
import signal
import time
from dataclasses import dataclass, field

import psutil

from modules.errors import (
    DriveError,
    ProcessNotFoundError,
    KillPermissionError,
)
from modules import session_manager

logger = logging.getLogger(__name__)


@dataclass
class ProcessInfo:
    pid: int
    ppid: int
    name: str
    command: str
    cpu: float
    memory_mb: float
    elapsed: str
    state: str
    cwd: str = ""
    session: str | None = None

    def to_dict(self) -> dict:
        d = {
            "pid": self.pid,
            "ppid": self.ppid,
            "name": self.name,
            "command": self.command,
            "cwd": self.cwd,
            "cpu": self.cpu,
            "memory_mb": self.memory_mb,
            "elapsed": self.elapsed,
            "state": self.state,
        }
        if self.session is not None:
            d["session"] = self.session
        return d


@dataclass
class KillResult:
    killed: list[int] = field(default_factory=list)
    failed: list[dict] = field(default_factory=list)
    signal: int = 15

    def to_dict(self) -> dict:
        return {
            "ok": len(self.failed) == 0,
            "action": "kill",
            "killed": self.killed,
            "signal": self.signal,
            "failed": self.failed,
        }


def _format_elapsed(seconds: float) -> str:
    """Format elapsed seconds as human-readable string."""
    s = int(seconds)
    if s < 60:
        return f"{s}s"
    if s < 3600:
        return f"{s // 60}m{s % 60:02d}s"
    h = s // 3600
    m = (s % 3600) // 60
    return f"{h}h{m:02d}m"


def _proc_info(p: psutil.Process, session_map: dict[int, str] | None = None) -> ProcessInfo | None:
    """Extract ProcessInfo from a psutil.Process."""
    try:
        with p.oneshot():
            info = p.as_dict(attrs=[
                "pid", "ppid", "name", "cmdline", "cpu_percent",
                "memory_info", "create_time", "status", "cwd",
            ])
    except (psutil.NoSuchProcess, psutil.AccessDenied):
        return None

    cmdline = info.get("cmdline") or []
    command = " ".join(cmdline) if cmdline else info.get("name", "")
    mem = info.get("memory_info")
    memory_mb = round(mem.rss / (1024 * 1024), 1) if mem else 0.0
    create_time = info.get("create_time", 0)
    elapsed = _format_elapsed(time.time() - create_time) if create_time else "0s"

    session = None
    if session_map:
        session = session_map.get(info["pid"])

    cwd = info.get("cwd") or ""

    return ProcessInfo(
        pid=info["pid"],
        ppid=info["ppid"] or 0,
        name=info.get("name", ""),
        command=command,
        cpu=info.get("cpu_percent", 0.0),
        memory_mb=memory_mb,
        elapsed=elapsed,
        state=info.get("status", "unknown"),
        cwd=cwd,
        session=session,
    )


def list_processes(
    *,
    name: str | None = None,
    parent: int | None = None,
    session: str | None = None,
    cwd: str | None = None,
) -> list[ProcessInfo]:
    """List processes filtered by name, parent PID, session, or working directory."""
    session_map = session_manager.session_pid_map()

    session_pids: set[int] | None = None
    if session is not None:
        root_pids = session_manager.get_session_pids(session)
        session_pids = set(root_pids)
        for root_pid in root_pids:
            try:
                root_proc = psutil.Process(root_pid)
                for child in root_proc.children(recursive=True):
                    session_pids.add(child.pid)
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                pass

    # Get current user's UID for filtering
    current_uid = os.getuid()

    results: list[ProcessInfo] = []
    for p in psutil.process_iter():
        try:
            # Filter by current user's UID
            try:
                uids = p.uids()
                if uids.real != current_uid:
                    continue
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                continue
        except Exception:
            continue

        if parent is not None:
            try:
                if p.ppid() != parent:
                    continue
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                continue

        if session_pids is not None and p.pid not in session_pids:
            continue

        info = _proc_info(p, session_map)
        if info is None:
            continue

        if name is not None:
            name_lower = name.lower()
            if name_lower not in info.name.lower() and name_lower not in info.command.lower():
                continue

        if cwd is not None:
            if not info.cwd.startswith(cwd):
                continue

        results.append(info)

    results.sort(key=lambda p: p.pid)
    return results


def kill_process(
    pid: int | None = None,
    *,
    name: str | None = None,
    sig: int = 15,
    tree: bool = False,
    graceful_timeout: float = 5.0,
) -> KillResult:
    """Kill process(es) by PID or name."""
    result = KillResult(signal=sig)
    current_uid = os.getuid()

    targets: list[int] = []
    if pid is not None:
        targets.append(pid)
    elif name is not None:
        for p in psutil.process_iter(["pid", "name", "cmdline"]):
            try:
                uids = p.uids()
                if uids.real != current_uid:
                    continue
                cmdline = " ".join(p.cmdline() or [])
                if name.lower() in p.name().lower() or name.lower() in cmdline.lower():
                    targets.append(p.pid)
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                continue

    if not targets:
        raise ProcessNotFoundError(pid=pid, name=name)

    all_pids: list[int] = []
    for target_pid in targets:
        if target_pid <= 1:  # System processes
            continue
        if target_pid == os.getpid():
            continue

        all_pids.append(target_pid)
        if tree:
            try:
                proc = psutil.Process(target_pid)
                children = proc.children(recursive=True)
                for child in reversed(children):
                    if child.pid not in all_pids:
                        all_pids.append(child.pid)
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                pass

    procs_to_kill: list[psutil.Process] = []
    for p in all_pids:
        try:
            procs_to_kill.append(psutil.Process(p))
        except psutil.NoSuchProcess:
            result.killed.append(p)

    for proc in procs_to_kill:
        try:
            proc.terminate()
        except psutil.NoSuchProcess:
            result.killed.append(proc.pid)
        except psutil.AccessDenied:
            result.failed.append({"pid": proc.pid, "error": "permission_denied"})

    gone, alive = psutil.wait_procs(procs_to_kill, timeout=graceful_timeout)
    for p in gone:
        if p.pid not in result.killed:
            result.killed.append(p.pid)

    if alive:
        for proc in alive:
            try:
                proc.kill()
                result.killed.append(proc.pid)
            except psutil.NoSuchProcess:
                result.killed.append(proc.pid)
            except psutil.AccessDenied:
                result.failed.append({"pid": proc.pid, "error": "permission_denied"})

    return result


def process_tree(pid: int) -> dict:
    """Build a process tree rooted at the given PID."""
    try:
        proc = psutil.Process(pid)
    except psutil.NoSuchProcess:
        raise ProcessNotFoundError(pid=pid)

    def _build_node(p: psutil.Process) -> dict:
        try:
            name = p.name()
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            name = "unknown"
        children = []
        try:
            for child in p.children():
                children.append(_build_node(child))
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            pass
        return {"pid": p.pid, "name": name, "children": children}

    return _build_node(proc)


def process_snapshot(pids: list[int]) -> list[ProcessInfo]:
    """Get detailed resource snapshot for specific PIDs."""
    results: list[ProcessInfo] = []
    session_map = session_manager.session_pid_map()
    for pid in pids:
        try:
            p = psutil.Process(pid)
            p.cpu_percent()
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            continue

    time.sleep(0.1)

    for pid in pids:
        try:
            p = psutil.Process(pid)
            info = _proc_info(p, session_map)
            if info:
                info.cpu = p.cpu_percent()
                results.append(info)
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            continue

    return results
