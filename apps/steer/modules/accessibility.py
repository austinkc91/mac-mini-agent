"""Accessibility tree walking using macOS Accessibility API via pyobjc.

Falls back gracefully if pyobjc is not available.
"""

import logging
import subprocess

logger = logging.getLogger(__name__)


def is_available() -> bool:
    """Check if Accessibility API is available."""
    try:
        import Quartz
        from ApplicationServices import (
            AXUIElementCreateSystemWide,
            AXUIElementCopyAttributeValue,
        )
        return True
    except ImportError:
        return False


def walk(app_name: str, max_depth: int = 10) -> list[dict]:
    """Walk the accessibility tree for an app.

    Returns list of UIElement-compatible dicts.
    """
    if not is_available():
        return []

    try:
        from ApplicationServices import (
            AXUIElementCreateApplication,
            AXUIElementCopyAttributeValue,
            AXValueGetValue,
        )
        import Quartz

        # Find the app's PID
        pid = _find_app_pid(app_name)
        if pid is None:
            return []

        app_ref = AXUIElementCreateApplication(pid)
        if app_ref is None:
            return []

        elements = []
        _walk_element(app_ref, 0, max_depth, elements)

        visible = [e for e in elements if e["width"] > 1 and e["height"] > 1 and _is_interactive(e["role"])]
        return _assign_ids(visible)

    except Exception as e:
        logger.debug(f"Accessibility walk failed for '{app_name}': {e}")
        return []


def focused_element(app_name: str | None = None) -> dict | None:
    """Get the currently focused UI element."""
    if not is_available():
        return None

    try:
        from ApplicationServices import (
            AXUIElementCreateSystemWide,
            AXUIElementCopyAttributeValue,
        )

        system_wide = AXUIElementCreateSystemWide()
        err, focused = AXUIElementCopyAttributeValue(system_wide, "AXFocusedUIElement")
        if err != 0 or focused is None:
            return None

        role = _get_attr(focused, "AXRole") or "unknown"
        label = _get_attr(focused, "AXTitle") or _get_attr(focused, "AXDescription") or ""
        value = _get_attr(focused, "AXValue")
        pos = _get_position(focused)
        size = _get_size(focused)

        x = int(pos[0]) if pos else 0
        y = int(pos[1]) if pos else 0
        w = int(size[0]) if size else 0
        h = int(size[1]) if size else 0

        return {
            "id": "F0",
            "role": _ax_role_to_generic(role),
            "label": str(label),
            "value": str(value) if value else None,
            "x": x, "y": y,
            "width": w, "height": h,
            "isEnabled": True,
            "depth": 0,
        }

    except Exception as e:
        logger.debug(f"Failed to get focused element: {e}")
        return None


def _find_app_pid(app_name: str) -> int | None:
    """Find a running app's PID by name."""
    try:
        from AppKit import NSWorkspace
        workspace = NSWorkspace.sharedWorkspace()
        for app in workspace.runningApplications():
            name = app.localizedName() or ""
            if app_name.lower() in name.lower():
                return app.processIdentifier()
    except ImportError:
        pass

    # Fallback: use pgrep
    try:
        result = subprocess.run(
            ["pgrep", "-i", "-x", app_name],
            capture_output=True, text=True, timeout=5,
        )
        if result.returncode == 0 and result.stdout.strip():
            return int(result.stdout.strip().split("\n")[0])
    except Exception:
        pass

    return None


def _get_attr(element, attr_name: str):
    """Get an accessibility attribute value."""
    try:
        from ApplicationServices import AXUIElementCopyAttributeValue
        err, value = AXUIElementCopyAttributeValue(element, attr_name)
        if err == 0:
            return value
    except Exception:
        pass
    return None


def _get_position(element) -> tuple | None:
    """Get element position as (x, y)."""
    try:
        from ApplicationServices import AXUIElementCopyAttributeValue, AXValueGetValue
        import Quartz
        err, pos_value = AXUIElementCopyAttributeValue(element, "AXPosition")
        if err == 0 and pos_value is not None:
            err2, point = AXValueGetValue(pos_value, Quartz.kAXValueCGPointType)
            if err2:
                return (point.x, point.y)
    except Exception:
        pass
    return None


def _get_size(element) -> tuple | None:
    """Get element size as (width, height)."""
    try:
        from ApplicationServices import AXUIElementCopyAttributeValue, AXValueGetValue
        import Quartz
        err, size_value = AXUIElementCopyAttributeValue(element, "AXSize")
        if err == 0 and size_value is not None:
            err2, size = AXValueGetValue(size_value, Quartz.kAXValueCGSizeType)
            if err2:
                return (size.width, size.height)
    except Exception:
        pass
    return None


def _walk_element(el, depth: int, max_depth: int, out: list[dict]) -> None:
    """Recursively walk an accessibility element tree."""
    if depth >= max_depth:
        return

    try:
        role = _get_attr(el, "AXRole") or "unknown"
        name = _get_attr(el, "AXTitle") or _get_attr(el, "AXDescription") or ""
        value = _get_attr(el, "AXValue")

        pos = _get_position(el)
        size = _get_size(el)
        x = int(pos[0]) if pos else 0
        y = int(pos[1]) if pos else 0
        w = int(size[0]) if size else 0
        h = int(size[1]) if size else 0

        out.append({
            "role": _ax_role_to_generic(role),
            "label": str(name) if name else "",
            "value": str(value) if value else None,
            "x": x, "y": y,
            "width": w, "height": h,
            "isEnabled": True,
            "depth": depth,
        })

        children = _get_attr(el, "AXChildren")
        if children:
            for child in children:
                _walk_element(child, depth + 1, max_depth, out)

    except Exception as e:
        logger.debug(f"Error walking element at depth {depth}: {e}")


def _ax_role_to_generic(ax_role: str) -> str:
    """Map macOS AX role to generic role name."""
    mapping = {
        "AXButton": "push button",
        "AXCheckBox": "check box",
        "AXComboBox": "combo box",
        "AXTextField": "text",
        "AXTextArea": "text",
        "AXLink": "link",
        "AXImage": "image",
        "AXRow": "list item",
        "AXMenuBar": "menu bar",
        "AXMenuItem": "menu item",
        "AXRadioButton": "radio button",
        "AXSlider": "slider",
        "AXIncrementor": "spin button",
        "AXTabGroup": "tab",
        "AXStaticText": "label",
        "AXToolbar": "tool bar",
        "AXOutlineRow": "tree item",
        "AXCell": "cell",
        "AXWindow": "window",
        "AXGroup": "group",
        "AXScrollArea": "pane",
        "AXPopUpButton": "combo box",
        "AXSecureTextField": "password text",
    }
    return mapping.get(ax_role, ax_role.replace("AX", "").lower())


def _is_interactive(role: str) -> bool:
    """Check if a role is considered interactive."""
    interactive_roles = {
        "push button", "toggle button", "radio button", "check box",
        "text", "password text", "entry", "combo box",
        "menu item", "menu", "menu bar",
        "link", "slider", "spin button",
        "tab", "tool bar", "label", "image",
        "list item", "tree item", "cell",
    }
    return role.lower() in interactive_roles


def detect_dialogs() -> list[dict]:
    """Detect modal/popup dialog windows on the desktop.

    Returns list of dialog dicts with title, buttons, and coordinates.
    """
    if not is_available():
        return []

    try:
        from ApplicationServices import (
            AXUIElementCreateApplication,
            AXUIElementCopyAttributeValue,
        )
        from AppKit import NSWorkspace

        dialogs = []
        workspace = NSWorkspace.sharedWorkspace()

        for app in workspace.runningApplications():
            if app.activationPolicy() != 0:
                continue

            pid = app.processIdentifier()
            app_ref = AXUIElementCreateApplication(pid)
            if app_ref is None:
                continue

            windows = _get_attr(app_ref, "AXWindows")
            if not windows:
                continue

            for win in windows:
                subrole = _get_attr(win, "AXSubrole") or ""
                role = _get_attr(win, "AXRole") or ""
                title = _get_attr(win, "AXTitle") or ""

                is_dialog = subrole in ("AXDialog", "AXSystemDialog", "AXStandardWindow")
                if not is_dialog and "dialog" in subrole.lower():
                    is_dialog = True

                # Check for sheets (modal overlays on macOS)
                if not is_dialog:
                    modal = _get_attr(win, "AXModal")
                    if modal:
                        is_dialog = True

                if is_dialog:
                    pos = _get_position(win)
                    size = _get_size(win)
                    x = int(pos[0]) if pos else 0
                    y = int(pos[1]) if pos else 0
                    w = int(size[0]) if size else 0
                    h = int(size[1]) if size else 0

                    buttons = []
                    children = _get_attr(win, "AXChildren")
                    if children:
                        for child in children:
                            child_role = _get_attr(child, "AXRole") or ""
                            if child_role == "AXButton":
                                btn_name = _get_attr(child, "AXTitle") or ""
                                btn_pos = _get_position(child)
                                btn_size = _get_size(child)
                                buttons.append({
                                    "label": str(btn_name),
                                    "x": int(btn_pos[0]) if btn_pos else 0,
                                    "y": int(btn_pos[1]) if btn_pos else 0,
                                    "width": int(btn_size[0]) if btn_size else 0,
                                    "height": int(btn_size[1]) if btn_size else 0,
                                })

                    dialogs.append({
                        "title": str(title),
                        "x": x, "y": y,
                        "width": w, "height": h,
                        "buttons": buttons,
                    })

        return dialogs

    except Exception as e:
        logger.debug(f"Dialog detection failed: {e}")
        return []


def _assign_ids(elements: list[dict]) -> list[dict]:
    """Assign role-based IDs to elements."""
    prefix_map = {
        "push button": "B", "toggle button": "B",
        "text": "T", "entry": "T", "password text": "T", "combo box": "T",
        "label": "S",
        "image": "I",
        "check box": "C",
        "radio button": "R",
        "slider": "SL", "spin button": "SL",
        "link": "L",
        "menu item": "M", "menu bar": "M", "menu": "M",
        "tab": "TB",
        "list item": "E", "tree item": "E", "cell": "E",
    }

    counts: dict[str, int] = {}
    result = []
    for el in elements:
        prefix = prefix_map.get(el["role"].lower(), "E")
        counts[prefix] = counts.get(prefix, 0) + 1
        el["id"] = f"{prefix}{counts[prefix]}"
        result.append(el)
    return result
