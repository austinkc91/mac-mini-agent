"""Screen capture using mss on macOS."""

import os
import tempfile
from dataclasses import dataclass

from modules.errors import CaptureFailure, ScreenNotFound

# Performance: max width for screenshots sent to AI
_MAX_WIDTH = 1920
_JPEG_QUALITY = 85


def get_dpi_scale_factor() -> float:
    """Detect the DPI scale factor for the primary monitor.

    On macOS Retina displays, this is typically 2.0.
    """
    try:
        import Quartz
        main_display = Quartz.CGMainDisplayID()
        # Retina: pixel width / point width
        pixel_width = Quartz.CGDisplayPixelsWide(main_display)
        mode = Quartz.CGDisplayCopyDisplayMode(main_display)
        if mode:
            point_width = Quartz.CGDisplayModeGetPixelWidth(mode)
            if pixel_width > 0 and point_width > 0:
                # On Retina, CGDisplayPixelsWide returns point width
                # CGDisplayModeGetPixelWidth returns actual pixel width
                return point_width / pixel_width
    except (ImportError, Exception):
        pass

    return 1.0


def get_dpi_for_monitor(display_id: int) -> float:
    """Get the DPI scale factor for a specific display."""
    try:
        import Quartz
        mode = Quartz.CGDisplayCopyDisplayMode(display_id)
        if mode:
            pixel_width = Quartz.CGDisplayModeGetPixelWidth(mode)
            point_width = Quartz.CGDisplayModeGetWidth(mode)
            if point_width > 0:
                return pixel_width / point_width
    except (ImportError, Exception):
        pass
    return get_dpi_scale_factor()


def _save_fast(img, output_path: str) -> None:
    """Save image as JPEG for speed, downscaling if needed."""
    w, h = img.size
    if w > _MAX_WIDTH:
        ratio = _MAX_WIDTH / w
        new_h = int(h * ratio)
        img = img.resize((_MAX_WIDTH, new_h), resample=1)  # BILINEAR=1

    if output_path.endswith(".png"):
        output_path_jpg = output_path[:-4] + ".jpg"
    else:
        output_path_jpg = output_path
    img.save(output_path_jpg, format="JPEG", quality=_JPEG_QUALITY, optimize=False)
    if output_path != output_path_jpg:
        try:
            os.replace(output_path_jpg, output_path)
        except OSError:
            pass


@dataclass
class ScreenInfo:
    index: int
    name: str
    width: int
    height: int
    origin_x: int
    origin_y: int
    is_main: bool
    scale_factor: float

    def to_dict(self) -> dict:
        return {
            "index": self.index,
            "name": self.name,
            "width": self.width,
            "height": self.height,
            "originX": self.origin_x,
            "originY": self.origin_y,
            "isMain": self.is_main,
            "scaleFactor": self.scale_factor,
        }


@dataclass
class WindowBounds:
    window_x: int
    window_y: int
    window_width: int
    window_height: int
    window_title: str | None
    window_id: int

    def to_dict(self) -> dict:
        return {
            "windowX": self.window_x,
            "windowY": self.window_y,
            "windowWidth": self.window_width,
            "windowHeight": self.window_height,
            "windowTitle": self.window_title,
            "windowID": self.window_id,
        }


def list_screens() -> list[ScreenInfo]:
    """List connected displays using Quartz."""
    try:
        import Quartz

        max_displays = 16
        (err, display_ids, count) = Quartz.CGGetOnlineDisplayList(max_displays, None, None)
        if err != 0 or not display_ids:
            return _list_screens_mss()

        main_id = Quartz.CGMainDisplayID()
        screens = []
        for i, did in enumerate(display_ids[:count]):
            bounds = Quartz.CGDisplayBounds(did)
            scale = get_dpi_for_monitor(did)
            screens.append(ScreenInfo(
                index=i,
                name=f"Display{i}",
                width=int(bounds.size.width),
                height=int(bounds.size.height),
                origin_x=int(bounds.origin.x),
                origin_y=int(bounds.origin.y),
                is_main=(did == main_id),
                scale_factor=scale,
            ))
        return screens
    except ImportError:
        return _list_screens_mss()


def _list_screens_mss() -> list[ScreenInfo]:
    """Fallback: list screens using mss."""
    try:
        import mss
        with mss.mss() as sct:
            screens = []
            for i, mon in enumerate(sct.monitors[1:], start=0):
                screens.append(ScreenInfo(
                    index=i,
                    name=f"Display{i}",
                    width=mon["width"],
                    height=mon["height"],
                    origin_x=mon["left"],
                    origin_y=mon["top"],
                    is_main=(i == 0),
                    scale_factor=1.0,
                ))
            return screens
    except ImportError:
        return []


def screen_info(index: int) -> ScreenInfo | None:
    """Get info for a specific screen by index."""
    screens = list_screens()
    if 0 <= index < len(screens):
        return screens[index]
    return None


def capture_display(output_path: str | None = None) -> str:
    """Capture the entire display. Returns path to image."""
    if output_path is None:
        output_path = tempfile.mktemp(suffix=".png", dir=_steer_dir())
    try:
        import mss
        with mss.mss() as sct:
            sct_img = sct.grab(sct.monitors[0])
            from PIL import Image
            img = Image.frombytes("RGB", sct_img.size, sct_img.bgra, "raw", "BGRX")
            _save_fast(img, output_path)
    except ImportError:
        # Fallback: use screencapture command
        import subprocess
        subprocess.run(["screencapture", "-x", output_path], timeout=10)

    if not os.path.exists(output_path):
        raise CaptureFailure("Screenshot did not produce output file")
    return output_path


def capture_screen(index: int, output_path: str | None = None) -> str:
    """Capture a specific screen by index. Returns path to image."""
    screens = list_screens()
    if index < 0 or index >= len(screens):
        raise ScreenNotFound(index, len(screens))
    screen = screens[index]
    if output_path is None:
        output_path = tempfile.mktemp(suffix=".png", dir=_steer_dir())

    monitor = {
        "left": screen.origin_x,
        "top": screen.origin_y,
        "width": screen.width,
        "height": screen.height,
    }

    try:
        import mss
        with mss.mss() as sct:
            sct_img = sct.grab(monitor)
            from PIL import Image
            img = Image.frombytes("RGB", sct_img.size, sct_img.bgra, "raw", "BGRX")
            _save_fast(img, output_path)
    except ImportError:
        import subprocess
        # screencapture can capture specific display with -D flag (1-indexed)
        subprocess.run(["screencapture", "-x", "-D", str(index + 1), output_path], timeout=10)

    return output_path


def capture_window(window_id: int, output_path: str | None = None) -> str:
    """Capture a specific window by Quartz window ID. Returns path to image."""
    if output_path is None:
        output_path = tempfile.mktemp(suffix=".png", dir=_steer_dir())

    try:
        import Quartz
        from PIL import Image

        # Use CGWindowListCreateImage to capture a specific window
        image_ref = Quartz.CGWindowListCreateImage(
            Quartz.CGRectNull,
            Quartz.kCGWindowListOptionIncludingWindow,
            window_id,
            Quartz.kCGWindowImageDefault,
        )

        if image_ref:
            width = Quartz.CGImageGetWidth(image_ref)
            height = Quartz.CGImageGetHeight(image_ref)
            if width > 0 and height > 0:
                # Convert CGImage to PIL Image via raw data
                bytes_per_row = Quartz.CGImageGetBytesPerRow(image_ref)
                data_provider = Quartz.CGImageGetDataProvider(image_ref)
                data = Quartz.CGDataProviderCopyData(data_provider)
                img = Image.frombuffer("RGBA", (width, height), data, "raw", "BGRA", bytes_per_row, 1)
                img = img.convert("RGB")
                _save_fast(img, output_path)
                return output_path
    except (ImportError, Exception):
        pass

    # Fallback: use screencapture with window ID
    try:
        import subprocess
        subprocess.run(["screencapture", "-x", "-l", str(window_id), output_path], timeout=10)
        if os.path.exists(output_path):
            return output_path
    except Exception:
        pass

    # Final fallback: capture full display
    return capture_display(output_path)


def capture_app(app_name: str, output_path: str | None = None) -> str:
    """Capture windows belonging to an app. Returns path to image."""
    from modules.app_control import find_app_windows
    windows = find_app_windows(app_name)
    if windows:
        return capture_window(windows[0]["id"], output_path)
    return capture_display(output_path)


def window_bounds(app_name: str) -> list[WindowBounds]:
    """Get window bounds for an app."""
    from modules.app_control import find_app_windows
    windows = find_app_windows(app_name)
    bounds = []
    for w in windows:
        if w["width"] > 1 and w["height"] > 1:
            bounds.append(WindowBounds(
                window_x=w["x"], window_y=w["y"],
                window_width=w["width"], window_height=w["height"],
                window_title=w.get("title"),
                window_id=w["id"],
            ))
    return bounds


def _steer_dir() -> str:
    """Ensure and return the steer temp directory."""
    d = os.path.join(tempfile.gettempdir(), "steer")
    os.makedirs(d, exist_ok=True)
    return d


def cleanup_snapshots(max_age_hours: int = 4, max_files: int = 50) -> int:
    """Remove old screenshot snapshots from temp/steer."""
    import time

    d = os.path.join(tempfile.gettempdir(), "steer")
    if not os.path.isdir(d):
        return 0

    files = []
    for name in os.listdir(d):
        path = os.path.join(d, name)
        if os.path.isfile(path) and (name.endswith(".png") or name.endswith(".jpg")):
            try:
                files.append((path, os.path.getmtime(path)))
            except OSError:
                pass

    if not files:
        return 0

    files.sort(key=lambda f: f[1])
    now = time.time()
    cutoff = now - max_age_hours * 3600
    removed = 0

    for path, mtime in files:
        if mtime < cutoff:
            try:
                os.unlink(path)
                removed += 1
            except OSError:
                pass

    remaining = [(p, m) for p, m in files if os.path.exists(p)]
    remaining.sort(key=lambda f: f[1])
    while len(remaining) > max_files:
        path, _ = remaining.pop(0)
        try:
            os.unlink(path)
            removed += 1
        except OSError:
            pass

    return removed


def _crop_image(src: str, dst: str, x: int, y: int, w: int, h: int) -> None:
    """Crop an image using PIL."""
    from PIL import Image
    img = Image.open(src)
    cropped = img.crop((x, y, x + w, y + h))
    cropped.save(dst)
