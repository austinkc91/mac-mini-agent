"""Steer error types."""


class SteerError(Exception):
    """Base error for steer operations."""
    pass


class CaptureFailure(SteerError):
    def __init__(self, msg: str):
        super().__init__(f"Capture failed: {msg}")


class AppNotFound(SteerError):
    def __init__(self, name: str):
        super().__init__(f"App not found: {name}")


class ElementNotFound(SteerError):
    def __init__(self, query: str):
        super().__init__(f"Element not found: {query}")


class NoSnapshot(SteerError):
    def __init__(self):
        super().__init__("No snapshot. Run 'steer see' first.")


class ScreenNotFound(SteerError):
    def __init__(self, index: int, available: int):
        super().__init__(
            f"Screen {index} not found. {available} screen(s) available "
            f"(use 0-{available - 1}). Run 'steer screens' to list."
        )


class WindowNotFound(SteerError):
    def __init__(self, name: str):
        super().__init__(f"No window found for app: {name}")


class WindowActionFailed(SteerError):
    def __init__(self, action: str, name: str):
        super().__init__(f"Window {action} failed for: {name}")


class ClipboardEmpty(SteerError):
    def __init__(self, content_type: str):
        super().__init__(f"Clipboard has no {content_type} content")


class WaitTimeout(SteerError):
    def __init__(self, condition: str, seconds: float):
        super().__init__(f"Timeout after {int(seconds)}s waiting for {condition}")


class ToolNotFound(SteerError):
    def __init__(self, tool: str, install_hint: str):
        super().__init__(
            f"Required tool '{tool}' not found. Install with: {install_hint}"
        )
