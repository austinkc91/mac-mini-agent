"""Mouse control using pyautogui on macOS."""

import time

import pyautogui

pyautogui.FAILSAFE = False
pyautogui.PAUSE = 0.01


def click(
    x: float,
    y: float,
    button: int = 1,
    count: int = 1,
    modifiers: list[str] | None = None,
) -> None:
    """Click at coordinates.

    button: 1=left, 2=middle, 3=right
    """
    button_map = {1: "left", 2: "middle", 3: "right"}
    btn = button_map.get(button, "left")
    px, py = int(x), int(y)

    # Hold modifier keys
    held = []
    if modifiers:
        for mod in modifiers:
            key = _normalize_modifier(mod)
            pyautogui.keyDown(key)
            held.append(key)
        time.sleep(0.01)

    try:
        pyautogui.click(x=px, y=py, clicks=count, button=btn)
    finally:
        for key in held:
            pyautogui.keyUp(key)


def drag(
    from_x: float,
    from_y: float,
    to_x: float,
    to_y: float,
    steps: int = 20,
    modifiers: list[str] | None = None,
) -> None:
    """Drag from one point to another."""
    fx, fy = int(from_x), int(from_y)
    tx, ty = int(to_x), int(to_y)

    held = []
    if modifiers:
        for mod in modifiers:
            key = _normalize_modifier(mod)
            pyautogui.keyDown(key)
            held.append(key)
        time.sleep(0.01)

    try:
        pyautogui.moveTo(fx, fy)
        time.sleep(0.05)
        pyautogui.mouseDown(button="left")
        time.sleep(0.05)

        for i in range(1, steps + 1):
            t = i / steps
            cx = fx + (tx - fx) * t
            cy = fy + (ty - fy) * t
            pyautogui.moveTo(int(cx), int(cy))
            time.sleep(0.005)

        time.sleep(0.05)
        pyautogui.mouseUp(button="left")
    finally:
        for key in held:
            pyautogui.keyUp(key)


def move_to(x: float, y: float) -> None:
    """Move mouse cursor to coordinates."""
    pyautogui.moveTo(int(x), int(y))


def scroll(direction: str, lines: int = 3, x: int | None = None, y: int | None = None) -> None:
    """Scroll in a direction by N lines."""
    direction_map = {
        "up": lines,
        "down": -lines,
        "left": -lines,
        "right": lines,
    }
    amount = direction_map.get(direction.lower())
    if amount is None:
        raise ValueError("Direction must be: up, down, left, right")

    if x is not None and y is not None:
        pyautogui.moveTo(int(x), int(y))
    else:
        size = pyautogui.size()
        pyautogui.moveTo(size[0] // 2, size[1] // 2)
    time.sleep(0.05)

    if direction.lower() in ("left", "right"):
        pyautogui.hscroll(amount)
    else:
        pyautogui.scroll(amount)


def _normalize_modifier(mod: str) -> str:
    """Normalize modifier name to pyautogui key name for macOS."""
    mod_map = {
        "ctrl": "ctrl",
        "control": "ctrl",
        "alt": "option",
        "option": "option",
        "opt": "option",
        "shift": "shift",
        "cmd": "command",
        "command": "command",
        "super": "command",
        "meta": "command",
        "win": "command",
    }
    return mod_map.get(mod.lower(), mod.lower())
