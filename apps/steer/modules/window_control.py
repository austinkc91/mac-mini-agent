"""Window management using Quartz and AppleScript on macOS."""

import subprocess
from dataclasses import dataclass

from modules.app_control import find_app_windows, activate
from modules.errors import WindowNotFound, WindowActionFailed


@dataclass
class WinInfo:
    app: str
    title: str
    x: int
    y: int
    width: int
    height: int
    is_minimized: bool
    is_fullscreen: bool

    def to_dict(self) -> dict:
        return {
            "app": self.app,
            "title": self.title,
            "x": self.x,
            "y": self.y,
            "width": self.width,
            "height": self.height,
            "isMinimized": self.is_minimized,
            "isFullscreen": self.is_fullscreen,
        }


def list_windows(app_name: str) -> list[WinInfo]:
    """List all windows for an app."""
    windows = find_app_windows(app_name)
    if not windows:
        return []
    results = []
    for w in windows:
        results.append(WinInfo(
            app=app_name,
            title=w.get("title", ""),
            x=w["x"], y=w["y"],
            width=w["width"], height=w["height"],
            is_minimized=False,  # Hard to detect via Quartz
            is_fullscreen=False,
        ))
    return results


def _run_applescript(script: str) -> bool:
    """Run an AppleScript and return success."""
    try:
        result = subprocess.run(
            ["osascript", "-e", script],
            capture_output=True, text=True, timeout=10,
        )
        return result.returncode == 0
    except Exception:
        return False


def move(app_name: str, x: float, y: float) -> None:
    """Move the first window of an app."""
    script = f'''
    tell application "System Events"
        tell process "{app_name}"
            set position of window 1 to {{{int(x)}, {int(y)}}}
        end tell
    end tell
    '''
    if not _run_applescript(script):
        raise WindowActionFailed("move", app_name)


def resize(app_name: str, width: float, height: float) -> None:
    """Resize the first window of an app."""
    script = f'''
    tell application "System Events"
        tell process "{app_name}"
            set size of window 1 to {{{int(width)}, {int(height)}}}
        end tell
    end tell
    '''
    if not _run_applescript(script):
        raise WindowActionFailed("resize", app_name)


def minimize(app_name: str, flag: bool = True) -> None:
    """Minimize or restore a window."""
    if flag:
        script = f'''
        tell application "System Events"
            tell process "{app_name}"
                set miniaturized of window 1 to true
            end tell
        end tell
        '''
    else:
        # Activate to un-minimize
        script = f'tell application "{app_name}" to activate'

    if not _run_applescript(script):
        raise WindowActionFailed("minimize" if flag else "restore", app_name)


def fullscreen(app_name: str) -> None:
    """Toggle fullscreen for a window using the green button."""
    script = f'''
    tell application "System Events"
        tell process "{app_name}"
            set value of attribute "AXFullScreen" of window 1 to not (value of attribute "AXFullScreen" of window 1)
        end tell
    end tell
    '''
    if not _run_applescript(script):
        raise WindowActionFailed("fullscreen", app_name)


def close(app_name: str) -> None:
    """Close a window."""
    script = f'''
    tell application "System Events"
        tell process "{app_name}"
            click button 1 of window 1
        end tell
    end tell
    '''
    # Try closing via Cmd+W first (more reliable)
    close_script = f'''
    tell application "{app_name}" to activate
    tell application "System Events" to keystroke "w" using command down
    '''
    if not _run_applescript(close_script):
        raise WindowActionFailed("close", app_name)
