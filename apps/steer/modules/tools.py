"""Tool availability checks for macOS dependencies."""

import shutil

from modules.errors import ToolNotFound


def require(tool: str) -> str:
    """Return path to a required tool binary, or raise ToolNotFound."""
    path = shutil.which(tool)
    if path is None:
        hints = {
            "tesseract": "brew install tesseract",
        }
        raise ToolNotFound(tool, hints.get(tool, f"Install {tool} and add to PATH"))
    return path


def ensure_display() -> str:
    """On macOS, check that we have display access. Returns 'macos'."""
    return "macos"


def check_display() -> str:
    """Return display identifier."""
    return ensure_display()
