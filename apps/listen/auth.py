"""Authentication module for the Agent Dashboard."""

import hashlib
import base64
import secrets
from pathlib import Path

import yaml

USERS_FILE = Path(__file__).parent / "users.yaml"

# In-memory session store: token -> username
_sessions: dict[str, str] = {}

SESSION_COOKIE = "agent_session"


def _load_users() -> dict:
    """Load users from YAML file."""
    if not USERS_FILE.exists():
        return {}
    with open(USERS_FILE) as f:
        data = yaml.safe_load(f) or {}
    return data.get("users", {})


def _hash_password(password: str, salt: str) -> str:
    """Hash a password with PBKDF2-SHA256."""
    h = hashlib.pbkdf2_hmac("sha256", password.encode(), salt.encode(), 100000)
    return base64.b64encode(h).decode()


def verify_password(username: str, password: str) -> bool:
    """Check if username/password combo is valid."""
    users = _load_users()
    user = users.get(username)
    if not user:
        return False
    stored_hash = user.get("password_hash", "")
    if ":" not in stored_hash:
        return False
    salt, expected = stored_hash.split(":", 1)
    actual = _hash_password(password, salt)
    return secrets.compare_digest(actual, expected)


def create_session(username: str) -> str:
    """Create a new session and return the token."""
    token = secrets.token_urlsafe(32)
    _sessions[token] = username
    return token


def get_session_user(token: str) -> str | None:
    """Get the username for a session token, or None if invalid."""
    return _sessions.get(token)


def destroy_session(token: str):
    """Remove a session."""
    _sessions.pop(token, None)


def lookup_user(name: str) -> dict | None:
    """Look up a user by username or display_name (case-insensitive). Returns user dict or None."""
    users = _load_users()
    name_lower = name.lower()
    # Try exact username match
    if name_lower in users:
        return {"username": name_lower, **users[name_lower]}
    # Try display_name match
    for uname, udata in users.items():
        if udata.get("display_name", "").lower() == name_lower:
            return {"username": uname, **udata}
    return None
