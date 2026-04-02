"""Clipboard control using pyperclip on macOS."""

import os
import tempfile

from modules.errors import ClipboardEmpty


def read_text() -> str | None:
    """Read text from the clipboard."""
    try:
        import pyperclip
        text = pyperclip.paste()
        return text if text else None
    except Exception:
        return None


def write_text(text: str) -> None:
    """Write text to the clipboard."""
    import pyperclip
    pyperclip.copy(text)


def read_image(save_to: str | None = None) -> str:
    """Read an image from the clipboard and save to file. Returns path."""
    try:
        from PIL import ImageGrab
        img = ImageGrab.grabclipboard()
        if img is None:
            raise ClipboardEmpty("image")
    except ImportError:
        raise ClipboardEmpty("image (Pillow not installed)")

    if save_to is None:
        steer_dir = os.path.join(tempfile.gettempdir(), "steer")
        os.makedirs(steer_dir, exist_ok=True)
        import uuid
        save_to = os.path.join(steer_dir, f"clipboard-{uuid.uuid4().hex[:8]}.png")

    img.save(save_to)
    return save_to


def write_image(from_path: str) -> None:
    """Write an image file to the clipboard using osascript."""
    if not os.path.exists(from_path):
        raise ClipboardEmpty(f"image at {from_path}")

    import subprocess
    abs_path = os.path.abspath(from_path)

    # Use osascript to set clipboard image on macOS
    script = f'''
    set the clipboard to (read (POSIX file "{abs_path}") as «class PNGf»)
    '''
    try:
        subprocess.run(
            ["osascript", "-e", script],
            capture_output=True, timeout=10,
        )
    except Exception:
        # Fallback: try using pbcopy with image data
        try:
            with open(abs_path, "rb") as f:
                subprocess.run(
                    ["osascript", "-e",
                     f'set the clipboard to (read (POSIX file "{abs_path}") as TIFF picture)'],
                    capture_output=True, timeout=10,
                )
        except Exception:
            raise ClipboardEmpty(f"Failed to write image to clipboard: {abs_path}")
