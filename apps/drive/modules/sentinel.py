"""Reliable command completion detection via sentinel protocol.

Protocol:
  1. Generate a unique token
  2. Wrap command with echo markers
  3. Send the wrapped command to session
  4. Poll output until __DONE_ sentinel appears
  5. Extract output between __START_ and __DONE_ lines

Using paired START/DONE markers makes output extraction immune to
line wrapping on narrow windows.
"""
import re
import time
import uuid

from modules import session_manager
from modules.errors import CommandTimeoutError

_START_PREFIX = "__START_"
_DONE_PREFIX = "__DONE_"


def generate_token() -> str:
    """Generate an 8-char hex token for sentinel matching."""
    return uuid.uuid4().hex[:8]


def start_marker(token: str) -> str:
    return f"{_START_PREFIX}{token}"


def done_marker(token: str) -> str:
    return f"{_DONE_PREFIX}{token}"


def wrap_command(cmd: str, token: str) -> str:
    """Wrap a shell command with start and done sentinels.

    For bash/zsh: echo markers and capture $?.
    """
    assert token.isalnum(), f"Invalid sentinel token: {token}"
    return (
        f'echo "{start_marker(token)}" ; '
        f'{cmd} ; '
        f'echo "{done_marker(token)}:$?"'
    )


def _done_pattern(token: str) -> re.Pattern:
    """Compile regex to match the done sentinel line."""
    marker = re.escape(done_marker(token))
    return re.compile(rf"^{marker}:(\d*)\s*$", re.MULTILINE)


def _start_pattern(token: str) -> re.Pattern:
    """Compile regex to match the start sentinel line."""
    marker = re.escape(start_marker(token))
    return re.compile(rf"^{marker}\s*$", re.MULTILINE)


def detect_completion(captured: str, token: str) -> tuple[bool, int | None, str]:
    """Scan captured output for the sentinel pair.

    Returns:
        (found, exit_code, output_between_markers)
    """
    done_match = _done_pattern(token).search(captured)
    if done_match is None:
        return (False, None, "")

    exit_code_str = done_match.group(1)
    exit_code = int(exit_code_str) if exit_code_str else 0

    start_match = _start_pattern(token).search(captured)
    if start_match:
        output = captured[start_match.end() : done_match.start()].strip()
    else:
        output = captured[: done_match.start()].strip()

    return (True, exit_code, output)


def run_and_wait(
    session: str,
    cmd: str,
    *,
    pane: str | None = None,
    timeout: float = 30.0,
    poll_interval: float = 0.05,
) -> tuple[int, str]:
    """Send a command and wait for sentinel-based completion.

    Uses exponential backoff polling (50ms -> 500ms).

    Args:
        timeout: Max seconds to wait. 0 means no limit.

    Returns:
        (exit_code, output) tuple.

    Raises:
        CommandTimeoutError: If sentinel not detected within timeout.
    """
    token = generate_token()
    wrapped = wrap_command(cmd, token)
    session_manager.send_keys(session, wrapped, pane=pane, enter=False, literal=True)

    deadline = None if timeout == 0 else time.monotonic() + timeout
    interval = poll_interval
    max_interval = 0.5

    while deadline is None or time.monotonic() < deadline:
        time.sleep(interval)

        captured = session_manager.capture_pane(session, pane=pane, start_line=-500)
        found, exit_code, output = detect_completion(captured, token)
        if found:
            return (exit_code, output)

        interval = min(interval * 1.5, max_interval)

    raise CommandTimeoutError(session=session, cmd=cmd, timeout=timeout)
