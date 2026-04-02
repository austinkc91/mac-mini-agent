"""Error hierarchy for drive CLI."""
import click


class DriveError(click.ClickException):
    """Base error for all drive operations."""

    code: str = "error"

    def __init__(self, message: str):
        super().__init__(message)

    def to_dict(self) -> dict:
        return {"ok": False, "error": self.code, "message": self.message}


class SessionNotFoundError(DriveError):
    code = "session_not_found"

    def __init__(self, name: str):
        super().__init__(f"Session not found: {name}")
        self.session = name

    def to_dict(self) -> dict:
        d = super().to_dict()
        d["session"] = self.session
        return d


class SessionExistsError(DriveError):
    code = "session_exists"

    def __init__(self, name: str):
        super().__init__(f"Session already exists: {name}")
        self.session = name


class CommandTimeoutError(DriveError):
    code = "timeout"

    def __init__(self, session: str, cmd: str, timeout: float):
        super().__init__(
            f"Command timed out after {timeout}s in session '{session}': {cmd[:80]}"
        )
        self.session = session
        self.cmd = cmd
        self.timeout = timeout

    def to_dict(self) -> dict:
        d = super().to_dict()
        d["session"] = self.session
        d["timeout"] = self.timeout
        return d


class SessionCommandError(DriveError):
    code = "session_error"

    def __init__(self, cmd: list[str], stderr: str):
        super().__init__(f"session command {' '.join(cmd)}: {stderr}")


class PatternNotFoundError(DriveError):
    code = "pattern_not_found"

    def __init__(self, pattern: str, session: str, timeout: float):
        super().__init__(
            f"Pattern '{pattern}' not found in session '{session}' within {timeout}s"
        )


class ProcessNotFoundError(DriveError):
    code = "process_not_found"

    def __init__(self, pid: int | None = None, name: str | None = None):
        if pid is not None:
            msg = f"Process not found: PID {pid}"
        elif name is not None:
            msg = f"No processes matching: {name}"
        else:
            msg = "No matching processes found"
        super().__init__(msg)


class KillPermissionError(DriveError):
    code = "kill_permission_denied"

    def __init__(self, pid: int):
        super().__init__(f"Permission denied killing PID {pid}")
        self.pid = pid
