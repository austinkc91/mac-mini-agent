"""Listen server — FastAPI job manager with SQLite storage.

Accepts job prompts, spawns Claude Code workers in terminal sessions,
tracks progress, and serves results via HTTP API.
"""

import asyncio
import json
import logging
import os
import shutil
import signal
import socket
import subprocess
import sys
import tempfile
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

import yaml
from fastapi import FastAPI, HTTPException, UploadFile, File, Request, Response, WebSocket, WebSocketDisconnect
from fastapi.responses import (
    HTMLResponse,
    PlainTextResponse,
    FileResponse,
    RedirectResponse,
    JSONResponse,
)
from pydantic import BaseModel, Field
from typing import Optional

import auth
import db
import cron_manager

logger = logging.getLogger("listen")

UPLOAD_DIR = Path(__file__).parent / "uploads"
UPLOAD_DIR.mkdir(exist_ok=True)
ATTACHMENTS_DIR = Path(__file__).parent / "attachments"
ATTACHMENTS_DIR.mkdir(exist_ok=True)
MAX_UPLOAD_SIZE = 50 * 1024 * 1024  # 50MB

JOBS_DIR = db.JOBS_DIR  # For log files
JOBS_DIR.mkdir(exist_ok=True)

# Chat history file shared with Telegram bot for cross-channel context
CHAT_HISTORY_FILE = Path(__file__).parent / "jobs" / "chat_history.jsonl"
CHAT_HISTORY_MAX_LINES = 50


def _log_chat(role: str, text: str):
    """Append a message to the shared chat history log (used by both listen and telegram)."""
    try:
        entry = {
            "ts": datetime.now(timezone.utc).isoformat(),
            "role": role,
            "text": text[:2000],
        }
        new_line = json.dumps(entry)
        lines = []
        if CHAT_HISTORY_FILE.exists():
            lines = CHAT_HISTORY_FILE.read_text().strip().splitlines()
        lines.append(new_line)
        if len(lines) > CHAT_HISTORY_MAX_LINES:
            lines = lines[-CHAT_HISTORY_MAX_LINES:]
        tmp = CHAT_HISTORY_FILE.with_suffix(".tmp")
        tmp.write_text("\n".join(lines) + "\n")
        tmp.replace(CHAT_HISTORY_FILE)
    except Exception as e:
        logger.error(f"Failed to log chat: {e}")

MAX_WORKERS = 4
MAX_PROMPT_LENGTH = 100_000  # 100KB max prompt size
_SERVER_START_TIME = datetime.now(timezone.utc)
_worker_semaphore: asyncio.Semaphore = asyncio.Semaphore(MAX_WORKERS)
_active_workers: dict[str, asyncio.subprocess.Process] = {}
_job_queue: asyncio.Queue = asyncio.Queue()
_queue_processor_task: asyncio.Task | None = None
_slot_lock: asyncio.Lock = asyncio.Lock()  # Prevents race condition in slot allocation

# ---------------------------------------------------------------------------
# Service notification (no-op on macOS, systemd not available)
# ---------------------------------------------------------------------------


def _sd_notify(msg: str):
    """No-op on macOS. On Linux this would notify systemd."""
    pass


# ---------------------------------------------------------------------------
# Periodic maintenance
# ---------------------------------------------------------------------------


async def _periodic_maintenance():
    """Run maintenance tasks periodically."""
    while True:
        await asyncio.sleep(60)
        try:
            # Notify systemd watchdog
            _sd_notify("WATCHDOG=1")

            # Recover orphaned jobs every minute
            recovered = await db.recover_orphaned_jobs()
            if recovered:
                print(f"Recovered {recovered} orphaned job(s)")
        except Exception as e:
            print(f"Maintenance error (recovery): {e}")

        try:
            # Archive old jobs every hour (check modulo)
            import time
            if int(time.time()) % 3600 < 60:
                archived = await db.archive_old_jobs()
                if archived:
                    print(f"Auto-archived {archived} old job(s)")
        except Exception as e:
            print(f"Maintenance error (archive): {e}")

        try:
            # Clean up old screenshots
            await asyncio.to_thread(_cleanup_steer_snapshots)
        except Exception as e:
            print(f"Maintenance error (snapshots): {e}")

        try:
            # Clean up old log files and uploads (check hourly)
            import time
            if int(time.time()) % 3600 < 60:
                await asyncio.to_thread(_cleanup_old_log_files)
                await asyncio.to_thread(_cleanup_old_uploads)
        except Exception as e:
            print(f"Maintenance error (log/upload cleanup): {e}")


def _cleanup_steer_snapshots():
    """Clean up old screenshot snapshots from /tmp/steer."""
    steer_dir = Path(tempfile.gettempdir()) / "steer"
    if not steer_dir.is_dir():
        return
    import time
    cutoff = time.time() - 4 * 3600
    removed = 0
    pngs = sorted(steer_dir.glob("*.png"), key=lambda p: p.stat().st_mtime)
    for p in pngs:
        try:
            if p.stat().st_mtime < cutoff:
                p.unlink()
                removed += 1
        except OSError:
            pass
    remaining = sorted(steer_dir.glob("*.png"), key=lambda p: p.stat().st_mtime)
    while len(remaining) > 50:
        try:
            remaining.pop(0).unlink()
            removed += 1
        except OSError:
            pass
    if removed:
        print(f"Cleaned up {removed} stale screenshot(s)")


def _cleanup_old_log_files(max_age_days: int = 7) -> int:
    """Remove job log files older than max_age_days. Returns count removed."""
    import time
    cutoff = time.time() - max_age_days * 86400
    removed = 0
    for p in JOBS_DIR.glob("*.log"):
        try:
            if p.stat().st_mtime < cutoff:
                p.unlink()
                removed += 1
        except OSError:
            pass
    if removed:
        print(f"Cleaned up {removed} old log file(s)")
    return removed


def _cleanup_old_uploads(max_age_days: int = 7) -> int:
    """Remove uploaded files and old attachment dirs older than max_age_days."""
    import time
    cutoff = time.time() - max_age_days * 86400
    removed = 0
    # Clean chat uploads
    for p in UPLOAD_DIR.iterdir():
        try:
            if p.is_file() and p.stat().st_mtime < cutoff:
                p.unlink()
                removed += 1
        except OSError:
            pass
    # Clean old job attachment directories
    for p in ATTACHMENTS_DIR.iterdir():
        try:
            if p.is_dir() and p.stat().st_mtime < cutoff:
                shutil.rmtree(p, ignore_errors=True)
                removed += 1
        except OSError:
            pass
    if removed:
        print(f"Cleaned up {removed} old upload/attachment(s)")
    return removed


# ---------------------------------------------------------------------------
# Worker management
# ---------------------------------------------------------------------------


TELEGRAM_WEBHOOK_URL = os.environ.get("TELEGRAM_WEBHOOK_URL", "http://127.0.0.1:7601")


_httpx_client: "httpx.AsyncClient | None" = None


async def _get_httpx_client():
    global _httpx_client
    if _httpx_client is None or _httpx_client.is_closed:
        import httpx
        _httpx_client = httpx.AsyncClient(timeout=10)
    return _httpx_client


async def _notify_telegram(job_id: str):
    """Notify the Telegram bot that a job has completed (fire-and-forget)."""
    try:
        client = await _get_httpx_client()
        await client.post(
            f"{TELEGRAM_WEBHOOK_URL}/notify/{job_id}",
            timeout=3,
        )
    except Exception:
        pass  # Non-critical — Telegram bot will still poll


async def _spawn_worker(job_id: str, prompt: str):
    """Spawn a worker subprocess and track it."""
    worker_path = Path(__file__).parent / "worker.py"
    log_file = JOBS_DIR / f"{job_id}.log"
    log_fh = open(log_file, "w")

    # Write prompt to temp file instead of passing as CLI arg
    # (avoids CLI arg length limits; prompts can be very long)
    import tempfile
    prompt_file = Path(tempfile.gettempdir()) / f"worker-prompt-{job_id}.txt"
    prompt_file.write_text(prompt, encoding="utf-8")

    proc = await asyncio.create_subprocess_exec(
        sys.executable,
        str(worker_path),
        job_id,
        str(prompt_file),
        cwd=str(Path(__file__).parent),
        stdout=log_fh,
        stderr=log_fh,
        start_new_session=True,
    )

    # Update PID in DB
    await db.update_job(job_id, pid=proc.pid)
    _active_workers[job_id] = proc

    try:
        await proc.wait()
    finally:
        _active_workers.pop(job_id, None)
        _worker_semaphore.release()
        log_fh.close()

        # If /complete was already called, the job is already finalized,
        # telegram was already notified, and chains were already triggered.
        # Only notify/chain if the worker exited without /complete (fallback).
        job = await db.get_job(job_id)
        if job and job.get("status") in ("running", "queued"):
            # Worker exited but /complete was never called — agent crashed
            # or CLI exited before the agent could signal. Worker.py should
            # have marked it, but as a safety net, notify telegram anyway.
            asyncio.create_task(_notify_telegram(job_id))
            asyncio.create_task(_submit_next_in_chain(job_id))
        elif job and job.get("status") == "completed":
            # /complete already fired telegram + chain — nothing to do
            pass
        else:
            # Failed/stopped — still notify telegram so user sees the error
            asyncio.create_task(_notify_telegram(job_id))


async def _submit_next_in_chain(job_id: str):
    """Check if a completed job has remaining chain steps and submit the next one.

    Chain state is persisted to the database via the chain_steps table so that
    if the server restarts mid-chain, _resume_pending_chains() can pick up
    where it left off.
    """
    try:
        job = await db.get_job(job_id)
        if not job or job.get("status") != "completed":
            return

        # Use persisted chain steps (survives restarts) with fallback to job field
        remaining_steps = await db.get_remaining_chain_steps(job_id)
        if not remaining_steps:
            chain = job.get("chain", [])
            if not chain:
                return
            remaining_steps = chain

        next_prompt = remaining_steps[0]
        remaining = remaining_steps[1:]

        prev_summary = job.get("summary", "")
        if prev_summary:
            contextualized_prompt = (
                f"This is a chained job. The previous job (ID: {job_id}) completed with this result:\n"
                f"---\n{prev_summary}\n---\n\n"
                f"Now do the following:\n{next_prompt}"
            )
        else:
            contextualized_prompt = next_prompt

        client = await _get_httpx_client()
        resp = await client.post(
            "http://localhost:7600/job",
            json={
                "prompt": contextualized_prompt,
                "chain": remaining,
                "chain_from": job_id,
            },
            timeout=10,
        )
        if resp.status_code == 200:
            next_id = resp.json().get("job_id", "?")
            # Mark this step as completed in the persisted chain
            await db.mark_chain_step_done(job_id, 0)
            print(f"Chain: job {job_id} -> job {next_id} ({len(remaining)} remaining)")
        else:
            print(f"Chain: failed to submit next job after {job_id}: {resp.status_code}")
    except Exception as e:
        print(f"Chain: error submitting next job after {job_id}: {e}")


async def _resume_pending_chains():
    """Resume chains that were interrupted by a server restart.

    Finds completed jobs that still have unprocessed chain steps in the
    chain_steps table and re-triggers the next step.
    """
    try:
        pending = await db.get_pending_chain_jobs()
        for job_id in pending:
            print(f"Chain resume: re-triggering chain continuation for job {job_id}")
            asyncio.create_task(_submit_next_in_chain(job_id))
        if pending:
            print(f"Chain resume: resumed {len(pending)} interrupted chain(s)")
    except Exception as e:
        print(f"Chain resume error: {e}")


# ---------------------------------------------------------------------------
# App lifecycle
# ---------------------------------------------------------------------------


@asynccontextmanager
async def lifespan(app):
    # Bootstrap psutil CPU measurement so api_status gets non-blocking values
    import psutil
    psutil.cpu_percent(interval=None)

    # Initialize database
    await db.init_db()
    await db.migrate_yaml_to_sqlite()
    recovered = await db.recover_orphaned_jobs()
    if recovered:
        print(f"Startup: recovered {recovered} orphaned job(s)")

    # Clean up stale completion signal files from previous runs
    try:
        for f in Path(tempfile.gettempdir()).glob("job-complete-*"):
            f.unlink(missing_ok=True)
    except Exception:
        pass

    # Start cron scheduler
    cron_manager.start()

    # Start job queue processor
    global _queue_processor_task
    _queue_processor_task = asyncio.create_task(_process_job_queue())

    # Resume any chains interrupted by a previous restart
    await _resume_pending_chains()

    # Start periodic maintenance
    maintenance_task = asyncio.create_task(_periodic_maintenance())

    # Tell systemd we're ready
    _sd_notify("READY=1")
    _sd_notify("WATCHDOG=1")

    yield

    # Shutdown
    print("Shutting down...")
    if _queue_processor_task:
        _queue_processor_task.cancel()
    maintenance_task.cancel()
    cron_manager.stop()

    # Mark any still-running jobs as stopped so they don't hang forever
    for job_id in list(_active_workers.keys()):
        try:
            now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
            await db.update_job(job_id, status="stopped", completed_at=now,
                                summary="Job stopped: server shutting down.")
        except Exception:
            pass

    # Close shared httpx client
    global _httpx_client
    if _httpx_client and not _httpx_client.is_closed:
        await _httpx_client.aclose()
        _httpx_client = None

    await db.close_db()
    print("Shutdown complete.")


app = FastAPI(lifespan=lifespan)


# ---------------------------------------------------------------------------
# Authentication middleware
# ---------------------------------------------------------------------------

_PUBLIC_PATHS = {"/", "/auth/login", "/login", "/health"}
# These paths are listed for documentation; middleware uses _PUBLIC_PATHS + cookie/localhost checks


def _is_localhost(request: Request) -> bool:
    client = request.client
    if not client:
        return False
    return client.host in ("127.0.0.1", "::1", "localhost")


class AuthMiddleware:
    """ASGI middleware for authentication.

    Uses raw ASGI instead of @app.middleware("http") / BaseHTTPMiddleware
    because BaseHTTPMiddleware breaks WebSocket connections in Starlette.
    """

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        # WebSocket connections handle their own auth — pass through
        if scope["type"] == "websocket":
            await self.app(scope, receive, send)
            return

        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        request = Request(scope, receive)
        path = request.url.path

        # Public paths — pass through
        if path in _PUBLIC_PATHS or path.startswith("/auth/") or path.startswith("/uploads/"):
            await self.app(scope, receive, send)
            return

        # Localhost requests skip auth (covers local access and Tailscale proxy)
        if _is_localhost(request):
            await self.app(scope, receive, send)
            return

        token = request.cookies.get(auth.SESSION_COOKIE)
        if token and auth.get_session_user(token):
            await self.app(scope, receive, send)
            return

        # Not authenticated — return error response
        content_type = request.headers.get("content-type", "")
        accept = request.headers.get("accept", "")
        wants_json = (
            path.startswith("/api/")
            or "application/json" in content_type
            or "application/json" in accept
        )
        if wants_json:
            response = JSONResponse(status_code=401, content={"detail": "Not authenticated"})
        else:
            response = RedirectResponse(url=f"/login?next={path}", status_code=302)
        await response(scope, receive, send)


app.add_middleware(AuthMiddleware)


# ---------------------------------------------------------------------------
# Auth endpoints
# ---------------------------------------------------------------------------


class LoginRequest(BaseModel):
    username: str
    password: str


@app.get("/")
async def root():
    return RedirectResponse(url="/cc", status_code=302)


@app.get("/login", response_class=HTMLResponse)
async def login_page():
    login_file = Path(__file__).parent / "login.html"
    return await asyncio.to_thread(login_file.read_text)


@app.post("/auth/login")
async def login(req: LoginRequest, response: Response):
    if not auth.verify_password(req.username, req.password):
        raise HTTPException(status_code=401, detail="Invalid username or password")
    token = auth.create_session(req.username)
    response = JSONResponse(content={"ok": True})
    response.set_cookie(
        key=auth.SESSION_COOKIE,
        value=token,
        httponly=True,
        samesite="lax",
        max_age=60 * 60 * 24 * 30,
    )
    return response


@app.post("/auth/logout")
async def logout(request: Request):
    token = request.cookies.get(auth.SESSION_COOKIE)
    if token:
        auth.destroy_session(token)
    response = RedirectResponse(url="/login", status_code=302)
    response.delete_cookie(auth.SESSION_COOKIE)
    return response


# ---------------------------------------------------------------------------
# Health endpoint
# ---------------------------------------------------------------------------


@app.get("/health")
async def health():
    return {
        "status": "ok",
        "workers_available": MAX_WORKERS - len(_active_workers),
        "workers_max": MAX_WORKERS,
        "active_jobs": list(_active_workers.keys()),
        "queued_jobs": _job_queue.qsize(),
    }


# ---------------------------------------------------------------------------
# Job endpoints
# ---------------------------------------------------------------------------


class JobRequest(BaseModel):
    prompt: str = Field(..., min_length=1, max_length=MAX_PROMPT_LENGTH)
    chain: list[str] = Field(default_factory=list)
    chain_from: Optional[str] = None
    files: list[str] = Field(default_factory=list)
    submitted_by: Optional[str] = None
    model: Optional[str] = None


async def _process_job_queue():
    """Background task that pulls jobs from the queue when worker slots open.

    This task MUST stay alive for the entire server lifetime — if it crashes,
    no queued jobs will ever start. All exceptions are caught and logged.
    """
    while True:
        job_id, prompt = await _job_queue.get()
        try:
            await _worker_semaphore.acquire()
            await db.update_job(job_id, status="running")
            asyncio.create_task(_spawn_worker(job_id, prompt))
        except Exception as e:
            print(f"CRITICAL: Queue processor error for job {job_id}: {e}")
            # Release the semaphore if we acquired it but failed to spawn
            try:
                _worker_semaphore.release()
            except ValueError:
                pass  # Wasn't acquired
            # Mark the job as failed so it doesn't stay queued forever
            try:
                now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
                await db.update_job(
                    job_id,
                    status="failed",
                    completed_at=now,
                    summary=f"Failed to start job: {e}",
                )
            except Exception:
                pass
        finally:
            _job_queue.task_done()


@app.post("/job")
async def create_job(req: JobRequest):
    job_id = uuid4().hex[:8]

    # Build prompt with file attachment context
    prompt = req.prompt
    if req.files:
        file_paths = []
        for fname in req.files:
            fpath = UPLOAD_DIR / fname
            if fpath.exists():
                file_paths.append(str(fpath))
        if file_paths:
            prompt += "\n\nAttached files:\n" + "\n".join(f"- {p}" for p in file_paths)

    # Look up user info
    submitted_by = req.submitted_by
    user_email = None
    if submitted_by:
        user_info = auth.lookup_user(submitted_by)
        if user_info:
            user_email = user_info.get("email")
            submitted_by = user_info.get("display_name", submitted_by)

    # Use lock to prevent check-then-act race condition on slot allocation.
    # Without this, two concurrent requests could both see a free slot and
    # both try to acquire it, bypassing the queue.
    async with _slot_lock:
        has_slot = _worker_semaphore._value > 0
        initial_status = "running" if has_slot else "queued"

        await db.create_job(
            job_id=job_id,
            prompt=prompt,
            submitted_by=submitted_by,
            submitted_by_email=user_email,
            chain=req.chain if req.chain else None,
            chain_from=req.chain_from,
            model=req.model,
        )

        # Persist chain steps to DB so they survive server restarts
        if req.chain:
            await db.save_chain_steps(job_id, req.chain)

        if has_slot:
            # Slot available — start immediately (acquire inside lock)
            await _worker_semaphore.acquire()
            asyncio.create_task(_spawn_worker(job_id, prompt))
        else:
            # All slots full — queue the job (it will start when a slot opens)
            await db.update_job(job_id, status="queued")
            await _job_queue.put((job_id, prompt))

    # Log user message to shared chat history (extract current request if context-wrapped)
    user_msg = req.prompt
    if "Current request: " in user_msg:
        user_msg = user_msg.split("Current request: ", 1)[1]
    _log_chat("user", user_msg)

    queue_size = _job_queue.qsize()
    return {
        "job_id": job_id,
        "status": initial_status,
        "workers_available": _worker_semaphore._value,
        "queue_size": queue_size,
    }


@app.get("/job/{job_id}")
async def get_job(job_id: str):
    """Get job as YAML (backward compat with telegram bot)."""
    job = await db.get_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    # Convert updates from list of dicts to list of strings for compat
    if job.get("updates"):
        job["updates"] = [
            u["text"] if isinstance(u, dict) else u for u in job["updates"]
        ]
    # Remove internal fields
    job.pop("archived", None)
    return PlainTextResponse(
        yaml.dump(job, default_flow_style=False, sort_keys=False)
    )


@app.get("/api/job/{job_id}")
async def get_job_json(job_id: str):
    """Get job as JSON."""
    job = await db.get_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    if job.get("updates"):
        job["updates"] = [
            u["text"] if isinstance(u, dict) else u for u in job["updates"]
        ]
    job.pop("archived", None)
    return job


@app.get("/api/chat-history")
async def chat_history(limit: int = 50):
    """Return recent jobs as JSON for command center chat history."""
    jobs = await db.list_jobs(archived=False)
    result = []
    for j in jobs[:limit]:
        raw_attachments = j.get("attachments", [])
        if isinstance(raw_attachments, str):
            try:
                raw_attachments = json.loads(raw_attachments)
            except Exception:
                raw_attachments = []
        job_id = j.get("id")
        attachment_info = []
        for idx, apath in enumerate(raw_attachments):
            p = Path(apath)
            try:
                exists = p.exists()
            except (PermissionError, OSError):
                exists = False
            attachment_info.append({
                "index": idx,
                "filename": p.name,
                "path": str(p),
                "exists": exists,
                "is_image": p.suffix.lower()
                in {".jpg", ".jpeg", ".png", ".gif", ".webp", ".bmp"},
                "url": f"/api/job/{job_id}/attachment/{idx}",
            })
        result.append({
            "id": job_id,
            "status": j.get("status"),
            "prompt": j.get("prompt"),
            "summary": j.get("summary", ""),
            "created_at": j.get("created_at"),
            "completed_at": j.get("completed_at"),
            "attachments": attachment_info,
        })
    # Return oldest first so chat renders top-to-bottom chronologically
    result.reverse()
    return result


@app.get("/jobs")
async def list_jobs_endpoint(archived: bool = False):
    """List all jobs as YAML."""
    jobs = await db.list_jobs(archived=archived)
    summary = []
    for j in jobs:
        summary.append({
            "id": j.get("id"),
            "status": j.get("status"),
            "prompt": j.get("prompt"),
            "created_at": j.get("created_at"),
        })
    return PlainTextResponse(
        yaml.dump({"jobs": summary}, default_flow_style=False, sort_keys=False)
    )


@app.post("/jobs/clear")
async def clear_jobs():
    count = await db.archive_all_jobs()
    return {"archived": count}


@app.delete("/job/{job_id}")
async def stop_job(job_id: str):
    job = await db.get_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")

    pid = job.get("pid")
    if pid:
        try:
            import psutil
            proc = psutil.Process(pid)
            for child in proc.children(recursive=True):
                child.terminate()
            proc.terminate()
        except Exception:
            try:
                os.kill(pid, signal.SIGTERM)
            except (ProcessLookupError, PermissionError, OSError):
                pass

    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    await db.update_job(job_id, status="stopped", completed_at=now)

    _active_workers.pop(job_id, None)

    return {"job_id": job_id, "status": "stopped"}


# ---------------------------------------------------------------------------
# Agent-facing endpoints (called by Claude agent via curl)
# ---------------------------------------------------------------------------


class UpdateRequest(BaseModel):
    text: str


class SummaryRequest(BaseModel):
    text: str


class AttachRequest(BaseModel):
    path: str


@app.post("/job/{job_id}/update")
async def job_update(job_id: str, req: UpdateRequest):
    """Append a progress update to a job."""
    if not await db.job_exists(job_id):
        raise HTTPException(status_code=404, detail="Job not found")
    await db.add_update(job_id, req.text)
    return {"ok": True}


@app.post("/job/{job_id}/summary")
async def job_summary(job_id: str, req: SummaryRequest):
    """Set the summary (response to user) for a job."""
    if not await db.job_exists(job_id):
        raise HTTPException(status_code=404, detail="Job not found")
    await db.set_summary(job_id, req.text)
    # Log bot response to shared chat history
    _log_chat("bot", req.text)
    return {"ok": True}


@app.post("/job/{job_id}/attach")
async def job_attach(job_id: str, req: AttachRequest):
    """Add a file attachment to a job.

    Copies the file to persistent storage so it survives temp directory cleanup.
    """
    if not await db.job_exists(job_id):
        raise HTTPException(status_code=404, detail="Job not found")

    src = Path(req.path)
    if not src.exists():
        raise HTTPException(status_code=404, detail="Source file not found")

    # Copy to persistent attachments directory with job-scoped name
    job_attach_dir = ATTACHMENTS_DIR / job_id
    job_attach_dir.mkdir(exist_ok=True)
    dest = job_attach_dir / src.name
    # Handle duplicate filenames
    if dest.exists():
        stem, suffix = dest.stem, dest.suffix
        counter = 1
        while dest.exists():
            dest = job_attach_dir / f"{stem}_{counter}{suffix}"
            counter += 1
    await asyncio.to_thread(shutil.copy2, str(src), str(dest))
    await db.add_attachment(job_id, str(dest))
    return {"ok": True}


@app.post("/job/{job_id}/complete")
async def job_complete(job_id: str):
    """Mark a job as completed. Called by the agent as its final action.

    This is the authoritative completion signal. It:
    1. Transitions the job to 'completed' in the DB
    2. Writes a signal file so the worker process knows to exit
    3. Notifies the Telegram bot for instant delivery

    The worker watches for the signal file and kills the claude CLI
    process if it hasn't already exited.
    """
    job = await db.get_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    if job.get("status") not in ("running", "queued"):
        return {"ok": True, "status": job.get("status"), "note": "already finished"}

    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    created = job.get("created_at", now)
    try:
        created_dt = datetime.strptime(created, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
        duration = int((datetime.now(timezone.utc) - created_dt).total_seconds())
    except Exception:
        duration = 0

    await db.update_job(
        job_id,
        status="completed",
        exit_code=0,
        duration_seconds=duration,
        completed_at=now,
    )

    # Write signal file for the worker process
    signal_file = Path(tempfile.gettempdir()) / f"job-complete-{job_id}"
    signal_file.write_text("done")

    # Notify Telegram bot instantly
    asyncio.create_task(_notify_telegram(job_id))
    # Trigger chain continuation
    asyncio.create_task(_submit_next_in_chain(job_id))

    return {"ok": True, "status": "completed", "duration_seconds": duration}


# ---------------------------------------------------------------------------
# Cron endpoints (unchanged)
# ---------------------------------------------------------------------------


class CronRequest(BaseModel):
    name: str
    schedule: str
    prompt: str
    timezone: str = "US/Central"
    enabled: bool = True


class CronUpdate(BaseModel):
    name: Optional[str] = None
    schedule: Optional[str] = None
    prompt: Optional[str] = None
    timezone: Optional[str] = None
    enabled: Optional[bool] = None


@app.post("/cron")
async def create_cron(req: CronRequest):
    try:
        cron = cron_manager.add_cron(
            name=req.name,
            schedule=req.schedule,
            prompt=req.prompt,
            timezone=req.timezone,
            enabled=req.enabled,
        )
        return cron
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))


@app.get("/crons")
async def list_crons():
    return {"crons": cron_manager.list_crons()}


@app.get("/cron/{cron_id}")
async def get_cron(cron_id: str):
    cron = cron_manager.get_cron(cron_id)
    if not cron:
        raise HTTPException(status_code=404, detail="Cron not found")
    return cron


@app.put("/cron/{cron_id}")
async def update_cron(cron_id: str, req: CronUpdate):
    updates = {k: v for k, v in req.model_dump().items() if v is not None}
    if not updates:
        raise HTTPException(status_code=400, detail="No fields to update")
    cron = cron_manager.update_cron(cron_id, **updates)
    if not cron:
        raise HTTPException(status_code=404, detail="Cron not found")
    return cron


@app.delete("/cron/{cron_id}")
async def delete_cron(cron_id: str):
    if not cron_manager.delete_cron(cron_id):
        raise HTTPException(status_code=404, detail="Cron not found")
    return {"deleted": cron_id}


@app.post("/cron/{cron_id}/trigger")
async def trigger_cron(cron_id: str):
    found, job_id = cron_manager.trigger_cron(cron_id)
    if not found:
        raise HTTPException(status_code=404, detail="Cron not found")
    return {"triggered": cron_id, "job_id": job_id}


# ---------------------------------------------------------------------------
# File upload
# ---------------------------------------------------------------------------


@app.post("/upload")
async def upload_file(file: UploadFile = File(...)):
    content = await file.read()
    if len(content) > MAX_UPLOAD_SIZE:
        raise HTTPException(status_code=413, detail="File too large (max 50MB)")
    ext = Path(file.filename or "file").suffix or ""
    unique_name = f"{uuid4().hex[:12]}{ext}"
    dest = UPLOAD_DIR / unique_name
    await asyncio.to_thread(dest.write_bytes, content)
    return {
        "filename": unique_name,
        "original_name": file.filename,
        "size": len(content),
        "path": str(dest),
    }


@app.get("/uploads/{filename}")
async def serve_upload(filename: str):
    filepath = UPLOAD_DIR / filename
    if not filepath.exists():
        raise HTTPException(status_code=404, detail="File not found")
    return FileResponse(filepath)


# ---------------------------------------------------------------------------
# Dashboard API
# ---------------------------------------------------------------------------


@app.get("/api/job/{job_id}")
async def api_get_job(job_id: str):
    job = await db.get_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    attachments = job.get("attachments", [])
    attachment_info = []
    for i, path in enumerate(attachments):
        p = Path(path)
        try:
            exists = p.exists()
        except (PermissionError, OSError):
            exists = False
        attachment_info.append({
            "index": i,
            "filename": p.name,
            "path": str(p),
            "exists": exists,
            "is_image": p.suffix.lower()
            in {".jpg", ".jpeg", ".png", ".gif", ".webp", ".bmp"},
            "url": f"/api/job/{job_id}/attachment/{i}",
        })
    updates = job.get("updates", [])
    if updates and isinstance(updates[0], dict):
        updates = [u.get("text", "") for u in updates]
    return {
        "id": job.get("id"),
        "status": job.get("status"),
        "summary": job.get("summary", ""),
        "updates": updates,
        "attachments": attachment_info,
        "created_at": job.get("created_at"),
        "completed_at": job.get("completed_at"),
    }


@app.get("/api/job/{job_id}/attachment/{index}")
async def serve_job_attachment(job_id: str, index: int):
    job = await db.get_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    attachments = job.get("attachments", [])
    if index < 0 or index >= len(attachments):
        raise HTTPException(status_code=404, detail="Attachment not found")
    filepath = Path(attachments[index])
    if not filepath.exists():
        raise HTTPException(status_code=404, detail="File not found on disk")
    return FileResponse(filepath, filename=filepath.name)


@app.get("/api/me")
async def api_me(request: Request):
    token = request.cookies.get(auth.SESSION_COOKIE)
    username = auth.get_session_user(token) if token else None
    return {"username": username}


@app.get("/dashboard", response_class=HTMLResponse)
async def dashboard():
    dashboard_file = Path(__file__).parent / "dashboard.html"
    if not dashboard_file.exists():
        raise HTTPException(status_code=404, detail="Dashboard not found")
    return await asyncio.to_thread(dashboard_file.read_text)


@app.get("/cc", response_class=HTMLResponse)
async def command_center():
    cc_file = Path(__file__).parent / "command-center.html"
    if not cc_file.exists():
        raise HTTPException(status_code=404, detail="Command Center not found")
    return await asyncio.to_thread(cc_file.read_text)


@app.get("/api/status")
async def api_status():
    jobs = await db.list_jobs(archived=False)

    active_jobs = []
    for data in jobs:
        raw_prompt = data.get("prompt") or ""
        display_prompt = raw_prompt
        if "Current request:" in raw_prompt:
            display_prompt = raw_prompt.split("Current request:", 1)[1].strip()

        job_id = data.get("id")
        attachments = data.get("attachments", [])
        attachment_info = []
        for idx, apath in enumerate(attachments):
            p = Path(apath)
            try:
                exists = p.exists()
            except (PermissionError, OSError):
                exists = False
            attachment_info.append({
                "index": idx,
                "filename": p.name,
                "exists": exists,
                "is_image": p.suffix.lower()
                in {".jpg", ".jpeg", ".png", ".gif", ".webp", ".bmp"},
                "url": f"/api/job/{job_id}/attachment/{idx}",
            })

        updates = data.get("updates", [])
        if updates and isinstance(updates[0], dict):
            updates = [u.get("text", "") for u in updates]

        summary = data.get("summary") or ""
        active_jobs.append({
            "id": job_id,
            "status": data.get("status"),
            "prompt": (display_prompt[:200] + "...")
            if len(display_prompt) > 200
            else display_prompt,
            "created_at": data.get("created_at"),
            "completed_at": data.get("completed_at"),
            "duration_seconds": data.get("duration_seconds"),
            "summary": (summary[:300] + "...")
            if len(summary) > 300
            else summary,
            "updates": updates,
            "chain_from": data.get("chain_from"),
            "chain": data.get("chain", []),
            "attachments": attachment_info,
        })

    crons = cron_manager.list_crons()

    import psutil

    # Non-blocking: interval=None returns cached value from last call
    cpu = psutil.cpu_percent(interval=None)
    mem = psutil.virtual_memory()
    disk = psutil.disk_usage("/")

    uptime_seconds = int((datetime.now(timezone.utc) - _SERVER_START_TIME).total_seconds())

    return {
        "jobs": active_jobs,
        "crons": crons,
        "system": {
            "cpu_percent": cpu,
            "memory_used_gb": round(mem.used / (1024**3), 1),
            "memory_total_gb": round(mem.total / (1024**3), 1),
            "memory_percent": mem.percent,
            "disk_used_gb": round(disk.used / (1024**3), 1),
            "disk_total_gb": round(disk.total / (1024**3), 1),
            "disk_percent": round(disk.percent, 1),
            "max_workers": MAX_WORKERS,
            "uptime_seconds": uptime_seconds,
            "active_workers": len(_active_workers),
            "queue_size": _job_queue.qsize(),
        },
    }


# ---------------------------------------------------------------------------
# Reset endpoints
# ---------------------------------------------------------------------------


@app.post("/cleanup")
async def cleanup_old_files():
    """Manually trigger cleanup of old log files, uploads, and archived jobs."""
    log_count = await asyncio.to_thread(_cleanup_old_log_files)
    upload_count = await asyncio.to_thread(_cleanup_old_uploads)
    archived = await db.archive_old_jobs()
    return {
        "logs_cleaned": log_count or 0,
        "uploads_cleaned": upload_count or 0,
        "jobs_archived": archived or 0,
    }


@app.post("/reset/soft")
async def soft_reset():
    results = {}

    # 1. Stop all running jobs
    jobs = await db.list_jobs()
    stopped = 0
    for j in jobs:
        if j.get("status") == "running":
            try:
                await stop_job(j["id"])
                stopped += 1
            except Exception:
                pass
    results["jobs_stopped"] = stopped

    # 2. Kill stale claude processes
    try:
        import psutil
        killed = 0
        running_job_ids = {j["id"] for j in jobs if j.get("status") == "running"}
        for proc in psutil.process_iter(["pid", "name", "cmdline"]):
            try:
                name = proc.info["name"] or ""
                if "claude" in name.lower():
                    cmdline = " ".join(proc.info.get("cmdline") or [])
                    is_active = any(jid in cmdline for jid in running_job_ids)
                    if not is_active:
                        proc.terminate()
                        killed += 1
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                continue
        results["processes_killed"] = killed
    except Exception as e:
        results["processes_killed"] = f"error: {e}"

    # 3. Clean up tmux sessions created by drive
    try:
        import subprocess as sp
        r = sp.run(["tmux", "kill-server"], capture_output=True, timeout=5)
        results["sessions_killed"] = "tmux server killed" if r.returncode == 0 else 0
    except Exception:
        results["sessions_killed"] = 0

    # 4. Service restart
    results["service_restart"] = "restart with: launchctl kickstart -k gui/$(id -u)/com.agent.listen"

    return results


@app.post("/reset/hard")
async def hard_reset():
    async def _reboot():
        await asyncio.sleep(1)
        subprocess.Popen(
            ["sudo", "shutdown", "-r", "+1"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )

    asyncio.create_task(_reboot())
    return {"status": "rebooting in 5 seconds"}


# ---------------------------------------------------------------------------
# Admin Shell
# ---------------------------------------------------------------------------

@app.post("/api/admin-shell")
async def launch_admin_shell():
    """Launch a Terminal window in the project directory with Claude Code ready."""
    project_dir = Path(__file__).parent.parent.parent  # mac-mini-agent root
    try:
        script = f'''
        tell application "Terminal"
            do script "cd '{project_dir}' && echo 'Admin Shell - mac-mini-agent' && echo 'Run: claude  to start Claude Code'"
            activate
        end tell
        '''
        subprocess.Popen(["osascript", "-e", script])
        return {"status": "ok", "message": "Admin shell launched"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# ---------------------------------------------------------------------------
# Desktop Screenshot API
# ---------------------------------------------------------------------------

_SCREENSHOT_CACHE: dict = {"path": None, "time": 0}
_SCREENSHOT_LOCK = asyncio.Lock()

@app.get("/api/screenshot")
async def api_screenshot():
    """Take a screenshot of the desktop and return it as a JPEG image."""
    import time
    now = time.time()

    # Cache for 5 seconds to avoid hammering the screen capture
    async with _SCREENSHOT_LOCK:
        if _SCREENSHOT_CACHE["path"] and now - _SCREENSHOT_CACHE["time"] < 5:
            cached = Path(_SCREENSHOT_CACHE["path"])
            if cached.exists():
                return FileResponse(str(cached), media_type="image/jpeg")

        try:
            import mss
            from PIL import Image

            tmp = Path(tempfile.gettempdir()) / "cc-screenshot.jpg"
            with mss.mss() as sct:
                monitor = sct.monitors[1]  # Primary monitor
                img = sct.grab(monitor)
                pil_img = Image.frombytes("RGB", img.size, img.bgra, "raw", "BGRX")
                # Resize for thumbnail (max 960px wide)
                w, h = pil_img.size
                if w > 960:
                    ratio = 960 / w
                    pil_img = pil_img.resize((960, int(h * ratio)), Image.LANCZOS)
                pil_img.save(str(tmp), "JPEG", quality=70)

            _SCREENSHOT_CACHE["path"] = str(tmp)
            _SCREENSHOT_CACHE["time"] = now
            return FileResponse(str(tmp), media_type="image/jpeg")
        except Exception as e:
            raise HTTPException(status_code=500, detail=f"Screenshot failed: {e}")


# ---------------------------------------------------------------------------
# Remote Control API
# ---------------------------------------------------------------------------


class RemoteClickRequest(BaseModel):
    x: float
    y: float
    button: str = "left"
    double: bool = False


class RemoteTypeRequest(BaseModel):
    text: str


class RemoteHotkeyRequest(BaseModel):
    keys: list[str]


class RemoteScrollRequest(BaseModel):
    amount: int  # positive = up, negative = down
    x: Optional[float] = None
    y: Optional[float] = None


class RemoteMouseMoveRequest(BaseModel):
    x: float
    y: float


class RemoteMouseDownRequest(BaseModel):
    x: float
    y: float
    button: str = "left"


class RemoteMouseUpRequest(BaseModel):
    x: float
    y: float
    button: str = "left"


@app.get("/api/remote/screen-info")
async def remote_screen_info():
    """Return the actual screen dimensions for coordinate mapping."""
    try:
        import mss
        with mss.mss() as sct:
            monitor = sct.monitors[1]
            return {
                "width": monitor["width"],
                "height": monitor["height"],
                "left": monitor["left"],
                "top": monitor["top"],
            }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/remote/click")
async def remote_click(req: RemoteClickRequest):
    """Click at the given screen coordinates."""
    try:
        import pyautogui
        pyautogui.FAILSAFE = False
        x, y = int(req.x), int(req.y)
        if req.double:
            pyautogui.doubleClick(x, y, button=req.button)
        else:
            pyautogui.click(x, y, button=req.button)
        return {"status": "ok", "x": x, "y": y, "button": req.button, "double": req.double}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/remote/type")
async def remote_type(req: RemoteTypeRequest):
    """Type text at the current cursor position."""
    try:
        import pyautogui
        pyautogui.FAILSAFE = False
        if req.text.isascii():
            pyautogui.typewrite(req.text, interval=0.02)
        else:
            # For non-ASCII, use pyperclip + Ctrl+V
            import pyperclip
            pyperclip.copy(req.text)
            pyautogui.hotkey("ctrl", "v")
        return {"status": "ok", "text": req.text}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/remote/hotkey")
async def remote_hotkey(req: RemoteHotkeyRequest):
    """Send a hotkey combination."""
    try:
        import pyautogui
        pyautogui.FAILSAFE = False
        pyautogui.hotkey(*req.keys)
        return {"status": "ok", "keys": req.keys}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/remote/scroll")
async def remote_scroll(req: RemoteScrollRequest):
    """Scroll at the given position (or current mouse position)."""
    try:
        import pyautogui
        pyautogui.FAILSAFE = False
        kwargs = {"clicks": req.amount}
        if req.x is not None and req.y is not None:
            kwargs["x"] = int(req.x)
            kwargs["y"] = int(req.y)
        pyautogui.scroll(**kwargs)
        return {"status": "ok", "amount": req.amount}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/remote/mousemove")
async def remote_mousemove(req: RemoteMouseMoveRequest):
    """Move the mouse cursor to the given screen coordinates."""
    try:
        import pyautogui
        pyautogui.FAILSAFE = False
        pyautogui.moveTo(int(req.x), int(req.y), _pause=False)
        return {"status": "ok", "x": int(req.x), "y": int(req.y)}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/remote/mousedown")
async def remote_mousedown(req: RemoteMouseDownRequest):
    """Press mouse button down at the given coordinates."""
    try:
        import pyautogui
        pyautogui.FAILSAFE = False
        pyautogui.moveTo(int(req.x), int(req.y), _pause=False)
        pyautogui.mouseDown(button=req.button, _pause=False)
        return {"status": "ok", "x": int(req.x), "y": int(req.y), "button": req.button}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/remote/mouseup")
async def remote_mouseup(req: RemoteMouseUpRequest):
    """Release mouse button at the given coordinates."""
    try:
        import pyautogui
        pyautogui.FAILSAFE = False
        pyautogui.moveTo(int(req.x), int(req.y), _pause=False)
        pyautogui.mouseUp(button=req.button, _pause=False)
        return {"status": "ok", "x": int(req.x), "y": int(req.y), "button": req.button}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.websocket("/ws/screen")
async def ws_screen_stream(websocket: WebSocket):
    """WebSocket endpoint that streams JPEG screenshots at adaptive frame rates."""
    # Auth check: allow localhost, otherwise require session cookie
    client = websocket.client
    is_local = client and client.host in ("127.0.0.1", "::1", "localhost")
    logger.info(f"[ws/screen] connection from {client.host if client else 'unknown'}, is_local={is_local}")
    if not is_local:
        token = websocket.cookies.get(auth.SESSION_COOKIE)
        if not token or not auth.get_session_user(token):
            logger.warning(f"[ws/screen] unauthorized remote connection from {client.host if client else 'unknown'}")
            await websocket.close(code=4001, reason="Unauthorized")
            return
    await websocket.accept()
    logger.info("[ws/screen] accepted, starting stream")
    try:
        import mss
        from PIL import Image
        import io
        import time

        quality = 60
        max_dim = 1920

        with mss.mss() as sct:
            monitor = sct.monitors[1]
            # Send screen dimensions first
            await websocket.send_json({
                "type": "info",
                "width": monitor["width"],
                "height": monitor["height"],
            })

            last_frame_time = 0
            min_interval = 0.08  # ~12fps minimum interval

            while True:
                try:
                    # Check for control messages (non-blocking)
                    try:
                        msg = await asyncio.wait_for(
                            websocket.receive_json(), timeout=0.01
                        )
                        if msg.get("type") == "quality":
                            quality = max(20, min(95, msg.get("value", 60)))
                        elif msg.get("type") == "maxdim":
                            max_dim = max(640, min(3840, msg.get("value", 1920)))
                        elif msg.get("type") == "ping":
                            await websocket.send_json({"type": "pong", "ts": msg.get("ts", 0)})
                            continue
                    except asyncio.TimeoutError:
                        pass

                    # Throttle frame rate
                    now = time.time()
                    elapsed = now - last_frame_time
                    if elapsed < min_interval:
                        await asyncio.sleep(min_interval - elapsed)

                    # Capture screenshot
                    img = sct.grab(monitor)
                    pil_img = Image.frombytes("RGB", img.size, img.bgra, "raw", "BGRX")
                    w, h = pil_img.size
                    if w > max_dim:
                        ratio = max_dim / w
                        pil_img = pil_img.resize((max_dim, int(h * ratio)), Image.LANCZOS)

                    buf = io.BytesIO()
                    pil_img.save(buf, "JPEG", quality=quality)
                    frame_bytes = buf.getvalue()

                    await websocket.send_bytes(frame_bytes)
                    last_frame_time = time.time()

                except WebSocketDisconnect:
                    logger.info("[ws/screen] client disconnected")
                    break
                except Exception as e:
                    logger.error(f"[ws/screen] frame error: {type(e).__name__}: {e}")
                    break
    except WebSocketDisconnect:
        logger.info("[ws/screen] disconnected during setup")
    except Exception as e:
        logger.error(f"[ws/screen] fatal error: {type(e).__name__}: {e}")


@app.get("/api/screenshot/live")
async def api_screenshot_live():
    """Fast screenshot for remote control mode - no caching, higher quality."""
    try:
        import mss
        from PIL import Image

        tmp = Path(tempfile.gettempdir()) / "cc-screenshot-live.jpg"
        with mss.mss() as sct:
            monitor = sct.monitors[1]
            img = sct.grab(monitor)
            pil_img = Image.frombytes("RGB", img.size, img.bgra, "raw", "BGRX")
            w, h = pil_img.size
            if w > 1920:
                ratio = 1920 / w
                pil_img = pil_img.resize((1920, int(h * ratio)), Image.LANCZOS)
            pil_img.save(str(tmp), "JPEG", quality=85)

        return FileResponse(str(tmp), media_type="image/jpeg",
                            headers={"Cache-Control": "no-store"})
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Screenshot failed: {e}")


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import uvicorn

    config = uvicorn.Config(app, host="0.0.0.0", port=7600)
    config.socket_options = [(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)]
    server = uvicorn.Server(config)
    server.run()
