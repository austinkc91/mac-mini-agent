"""Keyboard control using pyautogui on macOS."""

import pyautogui

pyautogui.FAILSAFE = False
pyautogui.PAUSE = 0.01

# Map modifier names to pyautogui key names (macOS)
MODIFIER_MAP = {
    "cmd": "command",
    "command": "command",
    "ctrl": "ctrl",
    "control": "ctrl",
    "alt": "option",
    "option": "option",
    "opt": "option",
    "shift": "shift",
    "fn": "fn",
    "super": "command",
    "meta": "command",
    "win": "command",
}

# Map key names to pyautogui key names
KEY_MAP = {
    "return": "enter",
    "enter": "enter",
    "tab": "tab",
    "space": "space",
    "delete": "backspace",
    "backspace": "backspace",
    "escape": "escape",
    "esc": "escape",
    "left": "left",
    "right": "right",
    "down": "down",
    "up": "up",
    "f1": "f1", "f2": "f2", "f3": "f3", "f4": "f4",
    "f5": "f5", "f6": "f6", "f7": "f7", "f8": "f8",
    "f9": "f9", "f10": "f10", "f11": "f11", "f12": "f12",
    "home": "home",
    "end": "end",
    "pageup": "pageup",
    "pagedown": "pagedown",
    "forwarddelete": "delete",
}


def type_text(text: str) -> None:
    """Type a string of text using clipboard paste (fast)."""
    _type_unicode(text)


def _type_unicode(text: str) -> None:
    """Type unicode text using pyperclip + paste."""
    import pyperclip
    pyperclip.copy(text)
    pyautogui.hotkey("command", "v")  # Cmd+V on macOS


def hotkey(combo: str) -> None:
    """Execute a hotkey combo like 'cmd+s', 'alt+tab', 'return'."""
    parts = combo.lower().split("+")
    keys = []
    for part in parts:
        part = part.strip()
        if part in MODIFIER_MAP:
            keys.append(MODIFIER_MAP[part])
        elif part in KEY_MAP:
            keys.append(KEY_MAP[part])
        elif len(part) == 1:
            keys.append(part)
        else:
            keys.append(part)

    pyautogui.hotkey(*keys)


def parse_modifiers(combo: str) -> list[str]:
    """Parse modifier string into list of pyautogui modifier names."""
    mods = []
    for part in combo.lower().split("+"):
        part = part.strip()
        if part in MODIFIER_MAP:
            mods.append(MODIFIER_MAP[part])
    return mods
