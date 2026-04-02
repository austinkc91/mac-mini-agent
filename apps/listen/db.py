"""SQLite database module for job storage.

Provides both async (aiosqlite) helpers for the FastAPI server
and sync (sqlite3) helpers for the worker process.

Uses WAL mode for safe concurrent access from multiple processes.
"""

import json
import os
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import aiosqlite

DB_PATH = Path(__file__).parent / "jobs.db"
JOBS_DIR = Path(__file__).parent / "jobs"  # For log files only


# ---------------------------------------------------------------------------
# Schema
# ---------------------------------------------------------------------------

_SCHEMA = """
CREATE TABLE IF NOT EXISTS jobs (
    id TEXT PRIMARY KEY,
    status TEXT NOT NULL DEFAULT 'running',
    prompt TEXT NOT NULL,
    submitted_by TEXT,
    submitted_by_email TEXT,
    created_at TEXT NOT NULL,
    completed_at TEXT,
    pid INTEGER DEFAULT 0,
    exit_code INTEGER,
    duration_seconds INTEGER,
    summary TEXT DEFAULT '',
    session TEXT,
    chain TEXT DEFAULT '[]',
    chain_from TEXT,
    attachments TEXT DEFAULT '[]',
    archived INTEGER DEFAULT 0,
    model TEXT
);

CREATE TABLE IF NOT EXISTS job_updates (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    job_id TEXT NOT NULL REFERENCES jobs(id),
    text TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS chain_steps (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    origin_job_id TEXT NOT NULL,
    step_index INTEGER NOT NULL,
    prompt TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'pending',
    created_at TEXT NOT NULL,
    completed_at TEXT
);

CREATE INDEX IF NOT EXISTS idx_jobs_status ON jobs(status);
CREATE INDEX IF NOT EXISTS idx_jobs_archived ON jobs(archived);
CREATE INDEX IF NOT EXISTS idx_jobs_archived_created ON jobs(archived, created_at);
CREATE INDEX IF NOT EXISTS idx_jobs_archived_status ON jobs(archived, status);
CREATE INDEX IF NOT EXISTS idx_job_updates_job_id ON job_updates(job_id);
CREATE INDEX IF NOT EXISTS idx_chain_steps_origin ON chain_steps(origin_job_id, status);
"""


# ---------------------------------------------------------------------------
# Async helpers (for FastAPI server)
# ---------------------------------------------------------------------------

_db: Optional[aiosqlite.Connection] = None


async def init_db() -> aiosqlite.Connection:
    """Initialize the database, create tables, return connection."""
    global _db
    _db = await aiosqlite.connect(str(DB_PATH))
    _db.row_factory = aiosqlite.Row
    await _db.execute("PRAGMA journal_mode=WAL")
    await _db.execute("PRAGMA synchronous=NORMAL")
    await _db.execute("PRAGMA busy_timeout=5000")
    await _db.executescript(_SCHEMA)
    # Migrations: add columns that may not exist in older databases
    try:
        await _db.execute("ALTER TABLE jobs ADD COLUMN model TEXT")
    except Exception:
        pass  # Column already exists
    await _db.commit()
    return _db


async def close_db():
    """Close the database connection."""
    global _db
    if _db:
        await _db.close()
        _db = None


async def get_db() -> aiosqlite.Connection:
    """Get the active database connection."""
    if _db is None:
        raise RuntimeError("Database not initialized — call init_db() first")
    return _db


def _row_to_dict(row) -> dict:
    """Convert an aiosqlite.Row to a plain dict with parsed JSON fields."""
    d = dict(row)
    for key in ("chain", "attachments"):
        if key in d and isinstance(d[key], str):
            try:
                d[key] = json.loads(d[key])
            except (json.JSONDecodeError, TypeError):
                d[key] = []
    return d


async def job_exists(job_id: str) -> bool:
    """Check if a job exists (lightweight, no data fetch)."""
    db = await get_db()
    async with db.execute("SELECT 1 FROM jobs WHERE id = ?", (job_id,)) as cur:
        return await cur.fetchone() is not None


async def create_job(
    job_id: str,
    prompt: str,
    submitted_by: Optional[str] = None,
    submitted_by_email: Optional[str] = None,
    chain: Optional[list] = None,
    chain_from: Optional[str] = None,
    model: Optional[str] = None,
) -> dict:
    """Insert a new job and return it as a dict."""
    db = await get_db()
    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    await db.execute(
        """INSERT INTO jobs (id, status, prompt, submitted_by, submitted_by_email,
           created_at, chain, chain_from, model)
           VALUES (?, 'running', ?, ?, ?, ?, ?, ?, ?)""",
        (
            job_id,
            prompt,
            submitted_by,
            submitted_by_email,
            now,
            json.dumps(chain or []),
            chain_from,
            model,
        ),
    )
    await db.commit()
    return await get_job(job_id)


async def get_job(job_id: str) -> Optional[dict]:
    """Fetch a single job by ID."""
    db = await get_db()
    async with db.execute("SELECT * FROM jobs WHERE id = ?", (job_id,)) as cur:
        row = await cur.fetchone()
    if not row:
        return None
    job = _row_to_dict(row)
    # Attach updates
    async with db.execute(
        "SELECT text, created_at FROM job_updates WHERE job_id = ? ORDER BY id",
        (job_id,),
    ) as cur:
        job["updates"] = [dict(r) async for r in cur]
    return job


async def list_jobs(archived: bool = False) -> list[dict]:
    """List all jobs (non-archived by default). Uses a single JOIN query."""
    db = await get_db()
    async with db.execute(
        """SELECT j.*, GROUP_CONCAT(u.text, '\x1f') as _updates_concat
           FROM jobs j
           LEFT JOIN job_updates u ON u.job_id = j.id
           WHERE j.archived = ?
           GROUP BY j.id
           ORDER BY j.created_at DESC""",
        (1 if archived else 0,),
    ) as cur:
        rows = await cur.fetchall()
    jobs = []
    for row in rows:
        job = _row_to_dict(row)
        # Parse concatenated updates from JOIN
        updates_str = job.pop("_updates_concat", None)
        job["updates"] = updates_str.split("\x1f") if updates_str else []
        jobs.append(job)
    return jobs


async def update_job(job_id: str, **fields) -> Optional[dict]:
    """Update arbitrary fields on a job. JSON-serializes chain/attachments."""
    db = await get_db()
    for key in ("chain", "attachments"):
        if key in fields and not isinstance(fields[key], str):
            fields[key] = json.dumps(fields[key])
    if not fields:
        return await get_job(job_id)
    cols = ", ".join(f"{k} = ?" for k in fields)
    vals = list(fields.values()) + [job_id]
    await db.execute(f"UPDATE jobs SET {cols} WHERE id = ?", vals)
    await db.commit()
    return await get_job(job_id)


async def add_update(job_id: str, text: str):
    """Append a progress update to a job."""
    db = await get_db()
    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    await db.execute(
        "INSERT INTO job_updates (job_id, text, created_at) VALUES (?, ?, ?)",
        (job_id, text, now),
    )
    await db.commit()


async def set_summary(job_id: str, text: str):
    """Set the summary field on a job."""
    await update_job(job_id, summary=text)


async def add_attachment(job_id: str, path: str):
    """Append a file path to a job's attachments list."""
    db = await get_db()
    async with db.execute(
        "SELECT attachments FROM jobs WHERE id = ?", (job_id,)
    ) as cur:
        row = await cur.fetchone()
    if not row:
        return
    current = json.loads(row["attachments"] or "[]")
    current.append(path)
    await db.execute(
        "UPDATE jobs SET attachments = ? WHERE id = ?",
        (json.dumps(current), job_id),
    )
    await db.commit()


async def archive_old_jobs(max_age_days: int = 7):
    """Mark completed/failed/stopped jobs older than max_age_days as archived."""
    db = await get_db()
    cutoff = datetime.now(timezone.utc)
    from datetime import timedelta

    cutoff_str = (cutoff - timedelta(days=max_age_days)).strftime(
        "%Y-%m-%dT%H:%M:%SZ"
    )
    result = await db.execute(
        """UPDATE jobs SET archived = 1
           WHERE archived = 0
             AND status IN ('completed', 'failed', 'stopped')
             AND COALESCE(completed_at, created_at) < ?""",
        (cutoff_str,),
    )
    await db.commit()
    return result.rowcount


async def archive_all_jobs():
    """Archive all non-running jobs."""
    db = await get_db()
    result = await db.execute(
        "UPDATE jobs SET archived = 1 WHERE archived = 0 AND status != 'running'"
    )
    await db.commit()
    return result.rowcount


def _is_process_alive(pid: int) -> bool:
    """Check if a process is alive (cross-platform)."""
    if sys.platform == "win32":
        import ctypes
        kernel32 = ctypes.windll.kernel32
        PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
        handle = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
        if handle:
            kernel32.CloseHandle(handle)
            return True
        return False
    else:
        try:
            os.kill(pid, 0)
            return True
        except ProcessLookupError:
            return False
        except PermissionError:
            return True  # Process exists but we can't signal it


async def recover_orphaned_jobs():
    """Recover jobs stuck in 'running' whose worker process is dead.

    This is a safety net for crashes — the normal completion path is
    the agent calling POST /job/{id}/complete, which marks the job done
    immediately. This function only catches cases where the worker died
    without the agent signaling completion.
    """
    db = await get_db()
    async with db.execute(
        "SELECT id, pid, summary FROM jobs WHERE status = 'running'"
    ) as cur:
        rows = await cur.fetchall()
    recovered = 0
    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    for row in rows:
        pid = row["pid"]
        if pid and pid > 0 and _is_process_alive(pid):
            # Worker is alive — job is still running, leave it alone
            continue

        # Worker is dead. If agent posted a summary, the work was done
        # but the /complete call never happened (crash). Mark completed.
        # If no summary, the worker crashed before finishing — mark failed.
        summary = row["summary"] or ""
        if summary:
            await db.execute(
                """UPDATE jobs SET status = 'completed', exit_code = 0,
                   completed_at = ?
                   WHERE id = ?""",
                (now, row["id"]),
            )
        else:
            await db.execute(
                """UPDATE jobs SET status = 'failed', exit_code = -1,
                   completed_at = ?,
                   summary = 'Job interrupted unexpectedly (worker process died).'
                   WHERE id = ?""",
                (now, row["id"]),
            )
        recovered += 1
    if recovered:
        await db.commit()
    return recovered


async def save_chain_steps(origin_job_id: str, steps: list[str]):
    """Persist chain steps to the database so they survive server restarts."""
    db = await get_db()
    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    for i, prompt in enumerate(steps):
        await db.execute(
            """INSERT INTO chain_steps (origin_job_id, step_index, prompt, status, created_at)
               VALUES (?, ?, ?, 'pending', ?)""",
            (origin_job_id, i, prompt, now),
        )
    await db.commit()


async def get_remaining_chain_steps(job_id: str) -> list[str]:
    """Get pending chain steps for a job, ordered by step index."""
    db = await get_db()
    async with db.execute(
        """SELECT prompt FROM chain_steps
           WHERE origin_job_id = ? AND status = 'pending'
           ORDER BY step_index""",
        (job_id,),
    ) as cur:
        rows = await cur.fetchall()
    return [row["prompt"] for row in rows]


async def mark_chain_step_done(origin_job_id: str, step_index: int):
    """Mark the first pending chain step as completed."""
    db = await get_db()
    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    # Mark the lowest-index pending step as done
    await db.execute(
        """UPDATE chain_steps SET status = 'completed', completed_at = ?
           WHERE id = (
               SELECT id FROM chain_steps
               WHERE origin_job_id = ? AND status = 'pending'
               ORDER BY step_index LIMIT 1
           )""",
        (now, origin_job_id),
    )
    await db.commit()


async def get_pending_chain_jobs() -> list[str]:
    """Find completed jobs that still have pending chain steps (interrupted chains).

    Returns a list of origin_job_ids that need chain continuation.
    """
    db = await get_db()
    async with db.execute(
        """SELECT DISTINCT cs.origin_job_id
           FROM chain_steps cs
           JOIN jobs j ON j.id = cs.origin_job_id
           WHERE cs.status = 'pending' AND j.status = 'completed'""",
    ) as cur:
        rows = await cur.fetchall()
    return [row["origin_job_id"] for row in rows]


async def get_undelivered_jobs() -> list[dict]:
    """Get completed/failed/stopped jobs that haven't been archived.
    Used by the Telegram bot to find results to deliver. Single JOIN query."""
    db = await get_db()
    async with db.execute(
        """SELECT j.*, GROUP_CONCAT(u.text, '\x1f') as _updates_concat
           FROM jobs j
           LEFT JOIN job_updates u ON u.job_id = j.id
           WHERE j.status IN ('completed', 'failed', 'stopped')
             AND j.archived = 0
           GROUP BY j.id
           ORDER BY j.completed_at""",
    ) as cur:
        rows = await cur.fetchall()
    jobs = []
    for row in rows:
        job = _row_to_dict(row)
        updates_str = job.pop("_updates_concat", None)
        job["updates"] = updates_str.split("\x1f") if updates_str else []
        jobs.append(job)
    return jobs


# ---------------------------------------------------------------------------
# Sync helpers (for worker.py — separate process)
# ---------------------------------------------------------------------------


def _sync_connect() -> sqlite3.Connection:
    """Create a sync SQLite connection with WAL mode."""
    conn = sqlite3.connect(str(DB_PATH), timeout=10)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    conn.execute("PRAGMA busy_timeout=5000")
    return conn


def sync_get_job(job_id: str) -> Optional[dict]:
    """Fetch a single job (sync, for worker process)."""
    conn = _sync_connect()
    try:
        row = conn.execute("SELECT * FROM jobs WHERE id = ?", (job_id,)).fetchone()
        if not row:
            return None
        d = dict(row)
        for key in ("chain", "attachments"):
            if key in d and isinstance(d[key], str):
                try:
                    d[key] = json.loads(d[key])
                except (json.JSONDecodeError, TypeError):
                    d[key] = []
        return d
    finally:
        conn.close()


def sync_update_job(job_id: str, **fields):
    """Update job fields (sync, for worker process)."""
    conn = _sync_connect()
    try:
        for key in ("chain", "attachments"):
            if key in fields and not isinstance(fields[key], str):
                fields[key] = json.dumps(fields[key])
        if not fields:
            return
        cols = ", ".join(f"{k} = ?" for k in fields)
        vals = list(fields.values()) + [job_id]
        conn.execute(f"UPDATE jobs SET {cols} WHERE id = ?", vals)
        conn.commit()
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Migration: YAML → SQLite (one-time)
# ---------------------------------------------------------------------------


async def migrate_yaml_to_sqlite():
    """Import existing YAML job files into SQLite. Skips already-imported jobs."""
    import yaml

    yaml_dir = JOBS_DIR
    if not yaml_dir.is_dir():
        return 0

    db = await get_db()
    migrated = 0

    for f in yaml_dir.glob("*.yaml"):
        job_id = f.stem
        if job_id == "chat_history":
            continue
        # Skip if already in DB
        async with db.execute(
            "SELECT id FROM jobs WHERE id = ?", (job_id,)
        ) as cur:
            if await cur.fetchone():
                continue
        try:
            with open(f) as fh:
                data = yaml.safe_load(fh)
            if not data or not isinstance(data, dict):
                continue

            await db.execute(
                """INSERT OR IGNORE INTO jobs
                   (id, status, prompt, submitted_by, submitted_by_email,
                    created_at, completed_at, pid, exit_code, duration_seconds,
                    summary, session, chain, chain_from, attachments, archived)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    data.get("id", job_id),
                    data.get("status", "unknown"),
                    data.get("prompt", ""),
                    data.get("submitted_by"),
                    data.get("submitted_by_email"),
                    data.get("created_at", ""),
                    data.get("completed_at"),
                    data.get("pid", 0),
                    data.get("exit_code"),
                    data.get("duration_seconds"),
                    data.get("summary", ""),
                    data.get("session"),
                    json.dumps(data.get("chain", [])),
                    data.get("chain_from"),
                    json.dumps(data.get("attachments", [])),
                    0,
                ),
            )

            # Migrate updates
            for update_text in data.get("updates", []):
                if isinstance(update_text, str):
                    await db.execute(
                        "INSERT INTO job_updates (job_id, text, created_at) VALUES (?, ?, ?)",
                        (job_id, update_text, data.get("created_at", "")),
                    )

            migrated += 1
        except Exception as e:
            print(f"Migration warning: failed to import {f.name}: {e}")

    if migrated:
        await db.commit()
        print(f"Migrated {migrated} YAML job(s) to SQLite")
    return migrated
