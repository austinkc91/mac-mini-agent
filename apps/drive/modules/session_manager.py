"""macOS session manager using tmux for terminal automation.

Each session is a tmux session with:
- Named sessions for easy management
- Output capture via tmux capture-pane
- State tracked in a JSON registry file

This provides the same API surface as the Windows PowerShell version:
- create/list/kill sessions
- send keystrokes (commands via tmux send-keys)
- capture output (tmux capture-pane)
"""
import fcntl
import json
import os
import re
import shutil
import subprocess
import time
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path

from modules.errors import (
    SessionExistsError,
    SessionNotFoundError,
    SessionCommandError,
)

# Session state directory
_SESSION_DIR = os.path.join(os.environ.get("TMPDIR", "/tmp"), "drive")
_REGISTRY_FILE = os.path.join(_SESSION_DIR, "sessions.json")
_LOCK_FILE = os.path.join(_SESSION_DIR, "sessions.lock")

# In-memory registry cache
_registry_cache: dict | None = None


@dataclass
class SessionInfo:
    name: str
    windows: int
    created: str
    attached: bool

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "windows": self.windows,
            "created": self.created,
            "attached": self.attached,
        }


def _ensure_dir():
    os.makedirs(_SESSION_DIR, exist_ok=True)


def _require_tmux():
    """Verify tmux is installed."""
    if shutil.which("tmux") is None:
        raise SessionCommandError(
            cmd=["tmux"],
            stderr="tmux not found. Install with: brew install tmux",
        )


@contextmanager
def _registry_lock(timeout: float = 5.0):
    """Acquire an exclusive file lock for registry access using fcntl."""
    _ensure_dir()
    deadline = time.monotonic() + timeout
    fh = open(_LOCK_FILE, "w")
    try:
        while True:
            try:
                fcntl.flock(fh.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except (OSError, IOError):
                if time.monotonic() >= deadline:
                    raise TimeoutError("Could not acquire registry lock")
                time.sleep(0.05)
        try:
            yield
        finally:
            try:
                fcntl.flock(fh.fileno(), fcntl.LOCK_UN)
            except (OSError, IOError):
                pass
    finally:
        fh.close()


def _load_registry() -> dict:
    """Load session registry from disk."""
    global _registry_cache
    if _registry_cache is not None:
        return _registry_cache
    if not os.path.exists(_REGISTRY_FILE):
        _registry_cache = {}
        return _registry_cache
    try:
        with open(_REGISTRY_FILE) as f:
            _registry_cache = json.load(f)
    except (json.JSONDecodeError, OSError):
        _registry_cache = {}
    return _registry_cache


def _save_registry(registry: dict):
    """Save session registry to disk."""
    global _registry_cache
    _registry_cache = registry
    _ensure_dir()
    tmp = _REGISTRY_FILE + ".tmp"
    with open(tmp, "w") as f:
        json.dump(registry, f, separators=(",", ":"))
    Path(tmp).replace(_REGISTRY_FILE)


def _log_path(name: str) -> str:
    """Get the output log file path for a session."""
    return os.path.join(_SESSION_DIR, f"{name}.log")


def _tmux_session_exists(name: str) -> bool:
    """Check if a tmux session exists."""
    result = subprocess.run(
        ["tmux", "has-session", "-t", name],
        capture_output=True, timeout=5,
    )
    return result.returncode == 0


def _sanitize_session_name(name: str) -> str:
    """Validate and sanitize a session name."""
    sanitized = re.sub(r"[^a-zA-Z0-9_-]", "", name)
    if not sanitized:
        sanitized = "session"
    return sanitized[:64]


def session_exists(name: str) -> bool:
    """Check if a session exists and tmux session is alive."""
    _require_tmux()
    with _registry_lock():
        registry = _load_registry()
        if name not in registry:
            # Also check tmux directly
            if _tmux_session_exists(name):
                return True
            return False
        if _tmux_session_exists(name):
            return True
        # Clean up stale entry
        del registry[name]
        _save_registry(registry)
        return False


def require_session(name: str) -> None:
    """Raise SessionNotFoundError if session does not exist."""
    if not session_exists(name):
        raise SessionNotFoundError(name)


def create_session(
    name: str,
    *,
    window_name: str | None = None,
    start_directory: str | None = None,
    detach: bool = False,
) -> None:
    """Create a new tmux session."""
    _require_tmux()

    if not re.match(r"^[a-zA-Z0-9_-]+$", name):
        raise SessionCommandError(
            cmd=["create", name],
            stderr=f"Invalid session name '{name}': only alphanumeric, hyphens, and underscores allowed",
        )

    if session_exists(name):
        raise SessionExistsError(name)

    _ensure_dir()
    cwd = start_directory or os.getcwd()

    # Build tmux new-session command
    cmd = ["tmux", "new-session", "-d", "-s", name]
    if window_name:
        cmd.extend(["-n", window_name])
    cmd.extend(["-c", cwd])

    result = subprocess.run(cmd, capture_output=True, text=True, timeout=10)
    if result.returncode != 0:
        raise SessionCommandError(
            cmd=cmd,
            stderr=result.stderr.strip() or f"Failed to create tmux session '{name}'",
        )

    # Set scrollback buffer size
    subprocess.run(
        ["tmux", "set-option", "-t", name, "history-limit", "50000"],
        capture_output=True, timeout=5,
    )

    # Get PID of the tmux server process for this session
    pid_result = subprocess.run(
        ["tmux", "display-message", "-t", name, "-p", "#{pane_pid}"],
        capture_output=True, text=True, timeout=5,
    )
    pid = int(pid_result.stdout.strip()) if pid_result.returncode == 0 else None

    # Register in state file
    with _registry_lock():
        registry = _load_registry()
        registry[name] = {
            "pid": pid,
            "created": time.strftime("%Y-%m-%d %H:%M:%S"),
            "cwd": cwd,
        }
        _save_registry(registry)


def list_sessions() -> list[SessionInfo]:
    """List all active sessions."""
    _require_tmux()

    # Get tmux sessions
    result = subprocess.run(
        ["tmux", "list-sessions", "-F", "#{session_name}:#{session_windows}:#{session_created}:#{session_attached}"],
        capture_output=True, text=True, timeout=5,
    )

    sessions = []
    if result.returncode == 0 and result.stdout.strip():
        for line in result.stdout.strip().split("\n"):
            parts = line.split(":", 3)
            if len(parts) >= 4:
                sessions.append(SessionInfo(
                    name=parts[0],
                    windows=int(parts[1]) if parts[1].isdigit() else 1,
                    created=parts[2],
                    attached=parts[3] == "1",
                ))

    # Clean up registry
    with _registry_lock():
        registry = _load_registry()
        tmux_names = {s.name for s in sessions}
        stale = [n for n in registry if n not in tmux_names]
        if stale:
            for n in stale:
                registry.pop(n, None)
            _save_registry(registry)

    return sessions


def kill_session(name: str) -> None:
    """Kill a session."""
    _require_tmux()
    require_session(name)

    result = subprocess.run(
        ["tmux", "kill-session", "-t", name],
        capture_output=True, text=True, timeout=10,
    )

    # Clean up registry
    with _registry_lock():
        registry = _load_registry()
        registry.pop(name, None)
        _save_registry(registry)

    # Clean up log file
    log_file = _log_path(name)
    if os.path.exists(log_file):
        try:
            os.unlink(log_file)
        except OSError:
            pass


def resolve_target(session: str, pane: str | None = None) -> str:
    """Build a tmux target string."""
    if pane:
        return f"{session}:{pane}"
    return session


def send_keys(
    session: str,
    keys: str,
    *,
    pane: str | None = None,
    enter: bool = True,
    literal: bool = False,
) -> None:
    """Send keystrokes to a session."""
    _require_tmux()
    require_session(session)

    target = resolve_target(session, pane)
    cmd = ["tmux", "send-keys", "-t", target]
    if literal:
        cmd.append("-l")
    cmd.append(keys)

    if enter:
        # Send keys then Enter
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=5)
        if result.returncode != 0:
            raise SessionCommandError(cmd=cmd, stderr=result.stderr.strip())
        # Send Enter separately
        subprocess.run(
            ["tmux", "send-keys", "-t", target, "Enter"],
            capture_output=True, timeout=5,
        )
    else:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=5)
        if result.returncode != 0:
            raise SessionCommandError(cmd=cmd, stderr=result.stderr.strip())


def capture_pane(
    session: str,
    *,
    pane: str | None = None,
    start_line: int | None = None,
    end_line: int | None = None,
) -> str:
    """Capture session output using tmux capture-pane."""
    _require_tmux()
    require_session(session)

    target = resolve_target(session, pane)
    cmd = ["tmux", "capture-pane", "-t", target, "-p"]

    if start_line is not None:
        cmd.extend(["-S", str(start_line)])
    else:
        cmd.extend(["-S", "-"])  # Capture from start of scrollback

    if end_line is not None:
        cmd.extend(["-E", str(end_line)])

    result = subprocess.run(cmd, capture_output=True, text=True, timeout=10)
    if result.returncode != 0:
        return ""

    return result.stdout.rstrip("\n")


def open_terminal_window(command: str) -> None:
    """Open a new terminal window and run a command."""
    # Try iTerm2 first
    iterm_script = f'''
    tell application "iTerm"
        create window with default profile command "{command}"
    end tell
    '''
    try:
        result = subprocess.run(
            ["osascript", "-e", iterm_script],
            capture_output=True, timeout=5,
        )
        if result.returncode == 0:
            return
    except Exception:
        pass

    # Fallback to Terminal.app
    terminal_script = f'''
    tell application "Terminal"
        do script "{command}"
        activate
    end tell
    '''
    subprocess.run(
        ["osascript", "-e", terminal_script],
        capture_output=True, timeout=5,
    )


def get_session_pids(session_name: str) -> list[int]:
    """Get all process PIDs associated with a session."""
    _require_tmux()
    pids = []

    # Get the pane PID
    result = subprocess.run(
        ["tmux", "list-panes", "-t", session_name, "-F", "#{pane_pid}"],
        capture_output=True, text=True, timeout=5,
    )
    if result.returncode == 0 and result.stdout.strip():
        for line in result.stdout.strip().split("\n"):
            if line.strip().isdigit():
                pid = int(line.strip())
                pids.append(pid)
                # Also get children
                try:
                    import psutil
                    proc = psutil.Process(pid)
                    for child in proc.children(recursive=True):
                        pids.append(child.pid)
                except Exception:
                    pass

    return pids


def session_pid_map() -> dict[int, str]:
    """Map PIDs to session names."""
    _require_tmux()
    pid_map: dict[int, str] = {}

    result = subprocess.run(
        ["tmux", "list-panes", "-a", "-F", "#{session_name}:#{pane_pid}"],
        capture_output=True, text=True, timeout=5,
    )

    if result.returncode == 0 and result.stdout.strip():
        for line in result.stdout.strip().split("\n"):
            parts = line.split(":", 1)
            if len(parts) == 2 and parts[1].strip().isdigit():
                session_name = parts[0]
                pid = int(parts[1].strip())
                pid_map[pid] = session_name
                try:
                    import psutil
                    proc = psutil.Process(pid)
                    for child in proc.children(recursive=True):
                        pid_map[child.pid] = session_name
                except Exception:
                    pass

    return pid_map
