"""Application control on macOS using Quartz and AppKit via pyobjc."""

import subprocess

from modules.errors import AppNotFound

# Cache PID-to-name lookups
_pid_name_cache: dict[int, str] = {}


def list_apps() -> list[dict]:
    """List running GUI applications with windows."""
    try:
        from AppKit import NSWorkspace
        workspace = NSWorkspace.sharedWorkspace()
        running = workspace.runningApplications()

        active_app = workspace.frontmostApplication()
        active_pid = active_app.processIdentifier() if active_app else -1

        apps = []
        for app in running:
            # Only list apps with a UI (activationPolicy 0 = regular app)
            if app.activationPolicy() == 0:
                name = app.localizedName() or app.bundleIdentifier() or "unknown"
                apps.append({
                    "name": name,
                    "pid": app.processIdentifier(),
                    "bundleId": app.bundleIdentifier(),
                    "isActive": app.processIdentifier() == active_pid,
                })
        return apps
    except ImportError:
        # Fallback: use AppleScript
        return _list_apps_applescript()


def _list_apps_applescript() -> list[dict]:
    """Fallback: list apps via AppleScript."""
    script = '''
    tell application "System Events"
        set appList to {}
        repeat with proc in (every process whose background only is false)
            set end of appList to {name of proc, unix id of proc, frontmost of proc}
        end repeat
        return appList
    end tell
    '''
    try:
        result = subprocess.run(
            ["osascript", "-e", script],
            capture_output=True, text=True, timeout=10,
        )
        # Parse the output (AppleScript returns nested lists as text)
        apps = []
        if result.returncode == 0 and result.stdout.strip():
            # Simple parsing of AppleScript list output
            import re
            items = re.findall(r'(\w[\w\s]*?),\s*(\d+),\s*(true|false)', result.stdout)
            for name, pid, frontmost in items:
                apps.append({
                    "name": name.strip(),
                    "pid": int(pid),
                    "bundleId": None,
                    "isActive": frontmost == "true",
                })
        return apps
    except Exception:
        return []


def find_app(name: str) -> dict | None:
    """Find a running app by name (case-insensitive)."""
    apps = list_apps()
    name_lower = name.lower()
    for app in apps:
        if app["name"].lower() == name_lower:
            return app
    for app in apps:
        if name_lower in app["name"].lower():
            return app
    return None


def find_app_windows(name: str) -> list[dict]:
    """Find all windows belonging to an app using Quartz."""
    try:
        import Quartz
    except ImportError:
        return _find_app_windows_applescript(name)

    name_lower = name.lower()
    results = []

    window_list = Quartz.CGWindowListCopyWindowInfo(
        Quartz.kCGWindowListOptionOnScreenOnly | Quartz.kCGWindowListExcludeDesktopElements,
        Quartz.kCGNullWindowID,
    )

    for win in window_list:
        owner = win.get(Quartz.kCGWindowOwnerName, "")
        if name_lower not in owner.lower():
            continue

        bounds = win.get(Quartz.kCGWindowBounds, {})
        x = int(bounds.get("X", 0))
        y = int(bounds.get("Y", 0))
        w = int(bounds.get("Width", 0))
        h = int(bounds.get("Height", 0))
        title = win.get(Quartz.kCGWindowName, "") or ""
        wid = win.get(Quartz.kCGWindowNumber, 0)
        pid = win.get(Quartz.kCGWindowOwnerPID, 0)

        if w > 1 and h > 1:
            results.append({
                "id": wid,
                "x": x, "y": y,
                "width": w, "height": h,
                "title": title,
                "pid": pid,
            })

    return results


def _find_app_windows_applescript(name: str) -> list[dict]:
    """Fallback: find windows via AppleScript."""
    script = f'''
    tell application "System Events"
        set winList to {{}}
        try
            tell process "{name}"
                repeat with w in windows
                    set pos to position of w
                    set sz to size of w
                    set t to name of w
                    set end of winList to {{t, item 1 of pos, item 2 of pos, item 1 of sz, item 2 of sz}}
                end repeat
            end tell
        end try
        return winList
    end tell
    '''
    try:
        result = subprocess.run(
            ["osascript", "-e", script],
            capture_output=True, text=True, timeout=10,
        )
        # For now return empty - AppleScript output parsing is complex
        return []
    except Exception:
        return []


def activate(name: str) -> None:
    """Activate (bring to front) an app by name."""
    try:
        from AppKit import NSWorkspace, NSRunningApplication
        workspace = NSWorkspace.sharedWorkspace()
        for app in workspace.runningApplications():
            app_name = app.localizedName() or ""
            if name.lower() in app_name.lower():
                app.activateWithOptions_(3)  # NSApplicationActivateAllWindows | NSApplicationActivateIgnoringOtherApps
                return
    except ImportError:
        pass

    # Fallback: AppleScript
    try:
        subprocess.run(
            ["osascript", "-e", f'tell application "{name}" to activate'],
            capture_output=True, timeout=10,
        )
        return
    except Exception:
        pass

    raise AppNotFound(name)


def launch(name: str) -> None:
    """Launch an application by name."""
    # Try opening by app name
    try:
        subprocess.Popen(
            ["open", "-a", name],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        return
    except Exception:
        pass

    # Try direct execution
    try:
        subprocess.Popen(
            [name],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
    except Exception:
        raise AppNotFound(name)


def frontmost() -> dict | None:
    """Get the frontmost (active) application."""
    try:
        from AppKit import NSWorkspace
        workspace = NSWorkspace.sharedWorkspace()
        app = workspace.frontmostApplication()
        if app:
            return {
                "name": app.localizedName() or "unknown",
                "pid": app.processIdentifier(),
                "bundleId": app.bundleIdentifier(),
                "isActive": True,
            }
    except ImportError:
        pass

    # Fallback
    apps = list_apps()
    for app in apps:
        if app.get("isActive"):
            return app
    return None
