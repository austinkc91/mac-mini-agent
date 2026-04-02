"""Element store for persisting UI element snapshots."""

import json
import os
import tempfile
import threading
from pathlib import Path


_cache: dict[str, list[dict]] = {}
_MAX_CACHE_SIZE = 50
_save_count = 0
_lock = threading.Lock()
STORE_DIR = os.path.join(tempfile.gettempdir(), "steer")


def _cleanup_old_snapshots(keep: int = 20):
    """Remove old snapshot files from disk, keeping only the most recent.

    Note: Caller must NOT hold _lock when calling this, as it only does
    filesystem I/O (no cache mutation).
    """
    if not os.path.exists(STORE_DIR):
        return
    imgs = sorted(
        [f for f in os.listdir(STORE_DIR) if f.endswith((".png", ".jpg"))],
        key=lambda f: os.path.getmtime(os.path.join(STORE_DIR, f)),
        reverse=True,
    )
    for old_img in imgs[keep:]:
        snap_id = old_img.rsplit(".", 1)[0]
        for ext in (".png", ".jpg", ".json"):
            path = os.path.join(STORE_DIR, snap_id + ext)
            try:
                os.unlink(path)
            except OSError:
                pass


def save(snap_id: str, elements: list[dict]) -> None:
    """Save elements to cache and disk. Thread-safe."""
    global _save_count
    should_cleanup = False

    with _lock:
        _cache[snap_id] = elements
        if len(_cache) > _MAX_CACHE_SIZE:
            excess = len(_cache) - _MAX_CACHE_SIZE
            for key in list(_cache.keys())[:excess]:
                del _cache[key]
        _save_count += 1
        should_cleanup = (_save_count % 10 == 0)

    # Disk I/O outside the lock to minimize hold time
    os.makedirs(STORE_DIR, exist_ok=True)
    path = os.path.join(STORE_DIR, f"{snap_id}.json")
    with open(path, "w") as f:
        json.dump(elements, f, separators=(",", ":"))

    if should_cleanup:
        _cleanup_old_snapshots()


def load(snap_id: str) -> list[dict] | None:
    """Load elements from cache or disk. Thread-safe."""
    with _lock:
        if snap_id in _cache:
            return _cache[snap_id]

    path = os.path.join(STORE_DIR, f"{snap_id}.json")
    if not os.path.exists(path):
        return None
    with open(path) as f:
        els = json.load(f)

    with _lock:
        _cache[snap_id] = els
    return els


def latest() -> tuple[str, list[dict]] | None:
    """Get the most recent snapshot. Thread-safe."""
    with _lock:
        if _cache:
            snap_id = max(_cache.keys())
            return (snap_id, _cache[snap_id])

    if not os.path.exists(STORE_DIR):
        return None
    json_files = [
        f for f in os.listdir(STORE_DIR) if f.endswith(".json")
    ]
    if not json_files:
        return None
    json_files.sort(
        key=lambda f: os.path.getmtime(os.path.join(STORE_DIR, f)),
        reverse=True,
    )
    snap_id = json_files[0].removesuffix(".json")
    with open(os.path.join(STORE_DIR, json_files[0])) as f:
        els = json.load(f)

    with _lock:
        _cache[snap_id] = els
    return (snap_id, els)


def resolve(query: str, snap: str | None = None) -> dict:
    """Resolve an element by ID or label."""
    from modules.errors import ElementNotFound, NoSnapshot

    if snap:
        els = load(snap)
        if els is None:
            raise NoSnapshot()
    else:
        result = latest()
        if result is None:
            raise NoSnapshot()
        els = result[1]

    lq = query.lower()

    # Single pass: check ID exact, label exact, and label substring
    label_exact = None
    label_partial = None
    for el in els:
        if el.get("id", "").lower() == lq:
            return el  # ID exact match — highest priority
        lbl = el.get("label", "").lower()
        if label_exact is None and lbl == lq:
            label_exact = el
        elif label_partial is None and lq in lbl:
            label_partial = el

    if label_exact is not None:
        return label_exact
    if label_partial is not None:
        return label_partial

    raise ElementNotFound(query)


def center_of(el: dict) -> tuple[int, int]:
    """Get center coordinates of an element."""
    return (el["x"] + el["width"] // 2, el["y"] + el["height"] // 2)
