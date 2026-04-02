"""Telegram bot for remote agent control.

Allows users to:
- Send text prompts to the Listen job server
- Check job status
- Send images/files that get saved and referenced in prompts
- Take screenshots of the agent's desktop
- Run steer/drive commands directly
"""

import asyncio
import json
import logging
import os
import re
import subprocess
import tempfile
from datetime import datetime, timezone
from pathlib import Path

import httpx
import yaml

logger = logging.getLogger(__name__)

# --- Dangerous shell command blocklist ---
# Patterns that should be blocked outright (case-insensitive)
_BLOCKED_SHELL_PATTERNS: list[re.Pattern] = [
    re.compile(r"\brm\s+(-\w*\s+)*-r\w*\s+/\s*$", re.IGNORECASE),       # rm -rf /
    re.compile(r"\brm\s+(-\w*\s+)*-r\w*\s+/(usr|etc|var|bin|sbin|System|Library)\b", re.IGNORECASE),  # rm -rf /usr etc
    re.compile(r":\(\)\s*\{\s*:\|\s*:\s*&\s*\}", re.IGNORECASE),          # fork bomb
    re.compile(r"\bmkfs\b", re.IGNORECASE),                                # mkfs
    re.compile(r"\bdd\s+.*\bof=/dev/", re.IGNORECASE),                    # dd of=/dev/...
    re.compile(r"\bshutdown\s+-h", re.IGNORECASE),                         # shutdown -h
    re.compile(r"\bdiskutil\s+erase", re.IGNORECASE),                      # diskutil erase
    re.compile(r"\bsudo\s+rm\s+(-\w*\s+)*-r\w*\s+/\s*$", re.IGNORECASE), # sudo rm -rf /
]

# Commands that require user confirmation before executing
_CONFIRM_SHELL_PATTERNS: list[re.Pattern] = [
    re.compile(r"\brm\s+", re.IGNORECASE),
    re.compile(r"\bkill\b", re.IGNORECASE),
    re.compile(r"\bkillall\b", re.IGNORECASE),
    re.compile(r"\bpkill\b", re.IGNORECASE),
    re.compile(r"\blaunchctl\s+(remove|unload|stop)\b", re.IGNORECASE),
    re.compile(r"\bsudo\b", re.IGNORECASE),
]

# Pending shell confirmations: chat_id -> (command, asyncio.Event, confirmed bool)
_pending_confirms: dict[int, tuple[str, asyncio.Event, list]] = {}


def _is_blocked_command(cmd: str) -> bool:
    """Return True if the command matches a blocked pattern."""
    for pattern in _BLOCKED_SHELL_PATTERNS:
        if pattern.search(cmd):
            return True
    return False


def _needs_confirmation(cmd: str) -> bool:
    """Return True if the command looks destructive and should require confirmation."""
    for pattern in _CONFIRM_SHELL_PATTERNS:
        if pattern.search(cmd):
            return True
    return False

# --- Chat history for conversational context ---
CHAT_HISTORY_FILE = Path(__file__).parent.parent / "listen" / "jobs" / "chat_history.jsonl"
CHAT_HISTORY_CONTEXT_LINES = 20  # How many recent messages to inject as context
CHAT_HISTORY_MAX_LINES = 50  # Max lines kept on disk (rotated on write)


def _log_chat(role: str, text: str):
    """Append a message to the chat history log, rotating if over max."""
    try:
        entry = {
            "ts": datetime.now(timezone.utc).isoformat(),
            "role": role,  # "user" or "bot"
            "text": text[:2000],  # Cap stored length
        }
        new_line = json.dumps(entry)

        # Read existing, append, and rotate if needed
        lines = []
        if CHAT_HISTORY_FILE.exists():
            lines = CHAT_HISTORY_FILE.read_text().strip().splitlines()
        lines.append(new_line)

        # Keep only the last N lines
        if len(lines) > CHAT_HISTORY_MAX_LINES:
            lines = lines[-CHAT_HISTORY_MAX_LINES:]

        # Atomic write via temp file
        tmp = CHAT_HISTORY_FILE.with_suffix(".tmp")
        tmp.write_text("\n".join(lines) + "\n")
        tmp.replace(CHAT_HISTORY_FILE)
    except Exception as e:
        logger.error(f"Failed to log chat: {e}")


def _get_recent_chat(n: int = CHAT_HISTORY_CONTEXT_LINES) -> str:
    """Return the last N chat messages formatted as context."""
    if not CHAT_HISTORY_FILE.exists():
        return ""
    try:
        lines = CHAT_HISTORY_FILE.read_text().strip().splitlines()
        recent = lines[-n:] if len(lines) > n else lines
        formatted = []
        for line in recent:
            entry = json.loads(line)
            who = "User" if entry["role"] == "user" else "Bot"
            formatted.append(f"[{who}]: {entry['text']}")
        return "\n".join(formatted)
    except Exception as e:
        logger.error(f"Failed to read chat history: {e}")
        return ""


def _build_prompt_with_context(prompt: str) -> str:
    """Wrap the user's prompt with recent chat history for conversational context."""
    history = _get_recent_chat()
    if not history:
        return prompt
    return (
        f"Recent Telegram chat history (for context — the user may reference earlier messages):\n"
        f"---\n{history}\n---\n\n"
        f"Current request: {prompt}"
    )

MAX_TG_MSG = 4000  # Conservative limit (Telegram allows 4096)


def _split_message(text: str, limit: int = MAX_TG_MSG) -> list[str]:
    """Split a long message into chunks that fit Telegram's character limit.
    Splits on double-newlines first, then single newlines, then hard-cuts."""
    if len(text) <= limit:
        return [text]
    chunks = []
    while text:
        if len(text) <= limit:
            chunks.append(text)
            break
        # Try to split at a double newline
        cut = text.rfind("\n\n", 0, limit)
        if cut == -1:
            cut = text.rfind("\n", 0, limit)
        if cut == -1:
            cut = limit
        chunks.append(text[:cut].rstrip())
        text = text[cut:].lstrip("\n")
    return chunks

LISTEN_URL = os.environ.get("LISTEN_URL", "http://localhost:7600")
REPO_ROOT = Path(__file__).parent.parent.parent

# Shared httpx client — reused across all requests instead of creating per-call
_http_client: httpx.AsyncClient | None = None


def _get_http_client() -> httpx.AsyncClient:
    """Get or create a shared async HTTP client."""
    global _http_client
    if _http_client is None or _http_client.is_closed:
        _http_client = httpx.AsyncClient(timeout=30)
    return _http_client
JOBS_DIR = REPO_ROOT / "apps" / "listen" / "jobs"
DELIVERED_FILE = JOBS_DIR / ".delivered"
UPLOADS_DIR = Path(tempfile.gettempdir()) / "telegram-uploads"
UPLOADS_DIR.mkdir(exist_ok=True)

# Atomic delivery lock — ensures only one coroutine can claim a job for delivery
_delivery_lock = asyncio.Lock()

# In-memory set to prevent concurrent delivery of the same job
_delivering: set[str] = set()

# Event-driven job completion: asyncio Events keyed by job_id
_job_events: dict[str, asyncio.Event] = {}

# Bot reference for webhook-triggered delivery
_webhook_bot = None
_webhook_chat_id = None

# In-memory cache of delivered job IDs (loaded once from disk at startup)
_delivered_cache: set[str] | None = None


def _load_delivered() -> set[str]:
    """Load set of delivered job IDs (cached in memory after first read)."""
    global _delivered_cache
    if _delivered_cache is None:
        if DELIVERED_FILE.exists():
            _delivered_cache = set(DELIVERED_FILE.read_text().strip().splitlines())
        else:
            _delivered_cache = set()
    return _delivered_cache


def _is_delivered(job_id: str) -> bool:
    """Check if a job has already been delivered (in-memory check only)."""
    return job_id in _delivering or job_id in _load_delivered()


async def _claim_for_delivery(job_id: str) -> bool:
    """Atomically claim a job for delivery. Returns True if this caller won the claim.

    This prevents the race between _poll_and_reply and periodic_delivery_check
    where both see a completed job and both try to deliver it.
    """
    async with _delivery_lock:
        if _is_delivered(job_id):
            return False
        _delivering.add(job_id)
        return True


def _mark_delivered(job_id: str):
    """Persist a job as delivered (updates in-memory cache + appends to disk)."""
    _delivering.add(job_id)
    _load_delivered().add(job_id)
    with open(DELIVERED_FILE, "a") as f:
        f.write(job_id + "\n")


def _save_chat_id(chat_id: int):
    """Persist the chat ID so recovery works after restart."""
    chat_id_file = JOBS_DIR / ".chat_id"
    chat_id_file.write_text(str(chat_id))

# Authorized user IDs (set via TELEGRAM_ALLOWED_USERS env var, comma-separated)
# If not set, ALL authenticated Telegram users are allowed (open access).
ALLOWED_USERS: set[int] | None = None
_allowed = os.environ.get("TELEGRAM_ALLOWED_USERS", "").strip()
if _allowed:
    ALLOWED_USERS = {int(uid.strip()) for uid in _allowed.split(",") if uid.strip()}
    logger.info(f"TELEGRAM_ALLOWED_USERS: {ALLOWED_USERS}")
else:
    logger.warning(
        "TELEGRAM_ALLOWED_USERS is not set. ALL users will be allowed. "
        "Set TELEGRAM_ALLOWED_USERS to restrict access."
    )


def is_authorized(user_id: int) -> bool:
    """Check if a user is authorized. If no allowlist configured, allow all."""
    if ALLOWED_USERS is None:
        return True
    if user_id not in ALLOWED_USERS:
        logger.warning(f"Rejected unauthorized user {user_id}")
        return False
    return True


async def _poll_and_reply(chat_id, job_id, context):
    """Poll the listen server until the job completes, then send the result.

    Uses event-driven wakeup when available: the webhook server sets an
    asyncio.Event when the listen server notifies us of job completion,
    so we wake up instantly instead of waiting for the next poll cycle.
    """
    # Register an event so the webhook can wake us instantly
    event = asyncio.Event()
    _job_events[job_id] = event
    try:
        client = _get_http_client()
        if True:
            poll_count = 0
            while poll_count < 7200:  # ~4 hours max
                poll_count += 1
                # Adaptive polling: 1s for first 5 min (300 polls), then 2s
                interval = 1 if poll_count <= 300 else 2
                # Wait for either the interval OR instant webhook notification
                try:
                    await asyncio.wait_for(event.wait(), timeout=interval)
                except asyncio.TimeoutError:
                    pass  # Normal polling cycle
                try:
                    resp = await client.get(f"{LISTEN_URL}/api/job/{job_id}", timeout=10)
                    if resp.status_code != 200:
                        event.clear()  # Reset for next cycle
                        continue
                    data = resp.json()
                    if data.get("status") in ("completed", "failed", "stopped"):
                        if not await _claim_for_delivery(job_id):
                            return  # Already claimed by periodic_delivery_check
                        await _send_job_result(context.bot, chat_id, data, job_id)
                        return
                except Exception:
                    pass
                event.clear()  # Reset for next cycle
            await context.bot.send_message(chat_id=chat_id, text=f"Sorry, that took too long. Use /status {job_id} to check.")
            _mark_delivered(job_id)
    except Exception as e:
        logger.error(f"Poll error for job {job_id}: {e}", exc_info=True)
    finally:
        _job_events.pop(job_id, None)


async def _send_job_result(bot, chat_id, data, job_id):
    """Send a job's result (summary + attachments) to Telegram.

    Guards against duplicate delivery: marks the job as delivered BEFORE
    sending any messages, so concurrent callers will see it immediately.
    Callers add job_id to _delivering before calling this, so we only
    check the persistent delivered file here (not _delivering).
    """
    # Final guard: if already written to the persistent delivered file, skip
    if job_id in _load_delivered():
        logger.warning(f"Skipping duplicate delivery for job {job_id}")
        return
    _mark_delivered(job_id)  # Mark BEFORE sending to prevent any race

    msg = data.get("summary", "") or f"Job {job_id} {data.get('status', 'done')}."
    _log_chat("bot", msg)
    # Split long messages into chunks to avoid Telegram's 4096 char limit
    for chunk in _split_message(msg):
        await bot.send_message(chat_id=chat_id, text=chunk)

    for attachment in data.get("attachments", []):
        try:
            # Handle both dict (from /api/job) and string (legacy) formats
            if isinstance(attachment, dict):
                file_path = attachment.get("path", "")
            else:
                file_path = str(attachment)
            if not file_path or not os.path.exists(file_path):
                continue
            ext = os.path.splitext(file_path)[1].lower()
            file_size = os.path.getsize(file_path)
            with open(file_path, "rb") as f:
                if ext in (".jpg", ".jpeg", ".png", ".gif", ".bmp", ".webp"):
                    await bot.send_photo(chat_id=chat_id, photo=f, caption=os.path.basename(file_path))
                elif ext in (".ogg", ".opus", ".oga"):
                    await bot.send_voice(chat_id=chat_id, voice=f)
                elif ext in (".mp3", ".m4a", ".flac", ".wav") and file_size <= 50 * 1024 * 1024:
                    await bot.send_audio(chat_id=chat_id, audio=f, filename=os.path.basename(file_path))
                elif ext in (".mp4", ".mov", ".mkv") and file_size <= 50 * 1024 * 1024:
                    await bot.send_video(chat_id=chat_id, video=f, filename=os.path.basename(file_path), supports_streaming=True)
                elif file_size > 50 * 1024 * 1024:
                    size_mb = file_size / (1024 * 1024)
                    await bot.send_message(chat_id=chat_id, text=f"File {os.path.basename(file_path)} is too large for Telegram ({size_mb:.0f} MB). Saved locally at: {file_path}")
                else:
                    await bot.send_document(chat_id=chat_id, document=f, filename=os.path.basename(file_path))
        except Exception as e:
            logger.error(f"Failed to send attachment {attachment}: {e}")

    logger.info(f"Sent result for job {job_id} to chat {chat_id}")


async def recover_undelivered(bot, chat_id):
    """Scan for completed jobs that were never delivered (e.g., after a restart or cron-triggered)."""
    delivered = _load_delivered()
    recovered = 0
    try:
        client = _get_http_client()
        if True:
            resp = await client.get(f"{LISTEN_URL}/jobs", timeout=10)
            if resp.status_code != 200:
                return
            data = yaml.safe_load(resp.text)
            for job in (data.get("jobs") or []):
                job_id = job.get("id")
                if not job_id or job_id in delivered or job_id in _delivering:
                    continue
                if job.get("status") in ("completed", "failed", "stopped"):
                    if not await _claim_for_delivery(job_id):
                        continue  # Already being delivered
                    try:
                        detail_resp = await client.get(f"{LISTEN_URL}/api/job/{job_id}", timeout=10)
                        if detail_resp.status_code == 200:
                            await _send_job_result(bot, chat_id, detail_resp.json(), job_id)
                            recovered += 1
                    except Exception as e:
                        _delivering.discard(job_id)  # Release claim so it can be retried
                        logger.error(f"Recovery failed for {job_id}: {e}")
    except Exception as e:
        logger.error(f"Recovery scan failed: {e}")
    if recovered:
        logger.info(f"Recovered {recovered} undelivered job(s)")


async def periodic_delivery_check(bot):
    """Periodically check for undelivered jobs (catches cron-triggered jobs)."""
    chat_id_file = JOBS_DIR / ".chat_id"
    while True:
        await asyncio.sleep(10)  # Check every 10 seconds
        try:
            if not chat_id_file.exists():
                continue
            chat_id = int(chat_id_file.read_text().strip())
            await recover_undelivered(bot, chat_id)
        except Exception as e:
            logger.error(f"Periodic delivery check error: {e}")


async def handle_start(update, context):
    """Handle /start command."""
    if not is_authorized(update.effective_user.id):
        await update.message.reply_text("Unauthorized. Your user ID: " + str(update.effective_user.id))
        return
    await update.message.reply_text(
        "Windows Agent Bot\n\n"
        "Commands:\n"
        "/job <prompt> - Submit a job to the agent\n"
        "/jobs - List all jobs\n"
        "/status [id] - Check job status (latest if no ID)\n"
        "/stop <id> - Stop a running job\n"
        "/screenshot - Take a screenshot\n"
        "/steer <cmd> - Run a steer command\n"
        "/drive <cmd> - Run a drive command\n"
        "/shell <cmd> - Run a shell command\n"
        "/cron - Manage scheduled cron jobs (add/list/edit/del/toggle/trigger)\n"
        "/reset - Soft reset (stop jobs, kill processes)\n"
        "/reset hard - Full reboot of the machine\n"
        "/restart - Restart agent services (listen + telegram)\n"
        "\nYou can also send images and files — they'll be saved and "
        "you can reference them in subsequent job prompts."
    )


async def handle_job(update, context):
    """Submit a job to the Listen server."""
    if not is_authorized(update.effective_user.id):
        return
    prompt = " ".join(context.args) if context.args else None
    if not prompt:
        await update.message.reply_text("Usage: /job <prompt>")
        return

    _log_chat("user", f"/job {prompt}")
    user_name = update.effective_user.first_name or update.effective_user.username or "unknown"
    try:
        client = _get_http_client()
        if True:
            resp = await client.post(
                f"{LISTEN_URL}/job",
                json={"prompt": prompt, "submitted_by": user_name},
                timeout=30,
            )
            if resp.status_code == 200:
                data = resp.json()
                job_id = data.get("job_id", data.get("id", "unknown"))
                await update.message.reply_text(f"Job submitted: {job_id}\nI'll reply when it's done.")
                asyncio.create_task(_poll_and_reply(update.effective_chat.id, job_id, context))
            elif resp.status_code == 429:
                await update.message.reply_text("All job slots are full right now. Try again in a minute.")
            else:
                await update.message.reply_text(f"Something went wrong (HTTP {resp.status_code}), try again.")
    except httpx.TimeoutException:
        await update.message.reply_text("The server is overloaded right now. Try again in a minute.")
    except Exception as e:
        logger.error(f"Failed to submit job: {type(e).__name__}: {e}")
        await update.message.reply_text(f"Sorry, I couldn't process that ({type(e).__name__}). Try again.")


async def handle_jobs(update, context):
    """List all jobs."""
    if not is_authorized(update.effective_user.id):
        return
    try:
        client = _get_http_client()
        if True:
            resp = await client.get(f"{LISTEN_URL}/jobs", timeout=10)
            if resp.status_code == 200:
                data = yaml.safe_load(resp.text)
                jobs = data.get("jobs", []) if data else []
                if not jobs:
                    await update.message.reply_text("No jobs.")
                    return
                lines = []
                for job in jobs[:10]:  # Show latest 10
                    status = job.get("status", "?")
                    jid = job.get("id", "?")
                    prompt = (job.get("prompt", "")[:50] + "...") if len(job.get("prompt", "")) > 50 else job.get("prompt", "")
                    lines.append(f"[{status}] {jid}: {prompt}")
                await update.message.reply_text("\n".join(lines))
            else:
                await update.message.reply_text(f"Error: {resp.status_code}")
    except Exception as e:
        await update.message.reply_text(f"Failed to list jobs: {e}")


async def handle_status(update, context):
    """Check status of a specific job, or show latest job if no ID given."""
    if not is_authorized(update.effective_user.id):
        return

    job_id = context.args[0] if context.args else None

    try:
        client = _get_http_client()
        if True:
            # If no job_id, find the latest running job (or most recent job)
            if not job_id:
                resp = await client.get(f"{LISTEN_URL}/jobs", timeout=10)
                if resp.status_code != 200:
                    await update.message.reply_text("Could not fetch jobs.")
                    return
                data = yaml.safe_load(resp.text)
                jobs = data.get("jobs", []) if data else []
                if not jobs:
                    await update.message.reply_text("No jobs found.")
                    return
                # Prefer running jobs, otherwise most recent
                running = [j for j in jobs if j.get("status") == "running"]
                target = running[-1] if running else jobs[-1]
                job_id = target.get("id")

            resp = await client.get(f"{LISTEN_URL}/job/{job_id}", timeout=10)
            if resp.status_code == 200:
                data = yaml.safe_load(resp.text)
                lines = [
                    f"Job: {data.get('id', '?')}",
                    f"Status: {data.get('status', '?')}",
                    f"Prompt: {(data.get('prompt', '')[:80] + '...') if len(data.get('prompt', '')) > 80 else data.get('prompt', '')}",
                ]
                if data.get("summary"):
                    lines.append(f"Summary: {data['summary']}")
                if data.get("duration_seconds"):
                    lines.append(f"Duration: {data['duration_seconds']}s")
                if data.get("updates"):
                    lines.append("Updates:")
                    for u in data["updates"][-5:]:  # Last 5 updates
                        lines.append(f"  - {u}")
                await update.message.reply_text("\n".join(lines))
            else:
                await update.message.reply_text(f"Job not found: {resp.status_code}")
    except Exception as e:
        await update.message.reply_text(f"Failed to get status: {e}")


async def handle_stop(update, context):
    """Stop a running job."""
    if not is_authorized(update.effective_user.id):
        return
    if not context.args:
        await update.message.reply_text("Usage: /stop <job_id>")
        return
    job_id = context.args[0]
    try:
        client = _get_http_client()
        if True:
            resp = await client.delete(f"{LISTEN_URL}/job/{job_id}", timeout=10)
            await update.message.reply_text(f"Stop result: {resp.status_code} — {resp.text}")
    except Exception as e:
        await update.message.reply_text(f"Failed to stop job: {e}")


async def handle_screenshot(update, context):
    """Take a screenshot and send it back."""
    if not is_authorized(update.effective_user.id):
        return
    try:
        steer_path = REPO_ROOT / "apps" / "steer"
        proc = await asyncio.create_subprocess_exec(
            "uv", "run", "python", "main.py", "see", "--json",
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
            cwd=str(steer_path),
        )
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=15)
        if proc.returncode == 0:
            import json
            data = json.loads(stdout.decode())
            ss_path = data.get("screenshot", "")
            if ss_path and os.path.exists(ss_path):
                with open(ss_path, "rb") as f:
                    await update.message.reply_photo(photo=f, caption="Desktop screenshot")
                return

        await update.message.reply_text("Failed to capture screenshot.")
    except Exception as e:
        await update.message.reply_text(f"Screenshot error: {e}")


async def handle_steer(update, context):
    """Run a steer command."""
    if not is_authorized(update.effective_user.id):
        return
    if not context.args:
        await update.message.reply_text("Usage: /steer <command> [args...]")
        return
    cmd_args = list(context.args)
    steer_path = REPO_ROOT / "apps" / "steer"
    try:
        proc = await asyncio.create_subprocess_exec(
            "uv", "run", "python", "main.py", *cmd_args, "--json",
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
            cwd=str(steer_path),
        )
        stdout_bytes, stderr_bytes = await asyncio.wait_for(proc.communicate(), timeout=30)
        stdout_str = stdout_bytes.decode() if stdout_bytes else ""
        stderr_str = stderr_bytes.decode() if stderr_bytes else ""
        output = stdout_str or stderr_str or "(no output)"
        if len(output) > 4000:
            output = output[:4000] + "\n...(truncated)"
        await update.message.reply_text(output)

        # If the command was 'see' or 'ocr', try to send the screenshot too
        if cmd_args and cmd_args[0] in ("see", "ocr") and proc.returncode == 0:
            try:
                import json
                data = json.loads(stdout_str)
                ss_path = data.get("screenshot", "")
                if ss_path and os.path.exists(ss_path):
                    with open(ss_path, "rb") as f:
                        await update.message.reply_photo(photo=f)
            except Exception:
                pass

    except asyncio.TimeoutError:
        await update.message.reply_text("Command timed out (30s)")
    except Exception as e:
        await update.message.reply_text(f"Error: {e}")


async def handle_drive(update, context):
    """Run a drive command."""
    if not is_authorized(update.effective_user.id):
        return
    if not context.args:
        await update.message.reply_text("Usage: /drive <command> [args...]")
        return
    cmd_args = list(context.args)
    drive_path = REPO_ROOT / "apps" / "drive"
    try:
        proc = await asyncio.create_subprocess_exec(
            "uv", "run", "python", "main.py", *cmd_args, "--json",
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
            cwd=str(drive_path),
        )
        stdout_bytes, stderr_bytes = await asyncio.wait_for(proc.communicate(), timeout=30)
        output = (stdout_bytes.decode() if stdout_bytes else "") or (stderr_bytes.decode() if stderr_bytes else "") or "(no output)"
        if len(output) > 4000:
            output = output[:4000] + "\n...(truncated)"
        await update.message.reply_text(output)
    except asyncio.TimeoutError:
        await update.message.reply_text("Command timed out (30s)")
    except Exception as e:
        await update.message.reply_text(f"Error: {e}")


async def handle_shell(update, context):
    """Run an arbitrary shell command with safety checks."""
    if not is_authorized(update.effective_user.id):
        return
    if not context.args:
        await update.message.reply_text("Usage: /shell <command>")
        return
    cmd = " ".join(context.args)

    # Block obviously dangerous commands
    if _is_blocked_command(cmd):
        await update.message.reply_text(
            "BLOCKED: This command is too dangerous to run remotely.\n"
            f"Command: {cmd}"
        )
        logger.warning(f"Blocked dangerous shell command from user {update.effective_user.id}: {cmd}")
        return

    # Require confirmation for destructive commands
    if _needs_confirmation(cmd):
        chat_id = update.effective_chat.id
        confirmed = [False]
        event = asyncio.Event()
        _pending_confirms[chat_id] = (cmd, event, confirmed)
        await update.message.reply_text(
            f"This command looks destructive:\n\n{cmd}\n\n"
            "Reply /confirm to execute or /cancel to abort."
        )
        try:
            await asyncio.wait_for(event.wait(), timeout=60)
        except asyncio.TimeoutError:
            _pending_confirms.pop(chat_id, None)
            await update.message.reply_text("Confirmation timed out. Command cancelled.")
            return
        finally:
            _pending_confirms.pop(chat_id, None)

        if not confirmed[0]:
            await update.message.reply_text("Command cancelled.")
            return

    try:
        proc = await asyncio.create_subprocess_shell(
            cmd,
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
            cwd=str(REPO_ROOT),
        )
        stdout_bytes, stderr_bytes = await asyncio.wait_for(proc.communicate(), timeout=30)
        output = (stdout_bytes.decode() if stdout_bytes else "") or (stderr_bytes.decode() if stderr_bytes else "") or "(no output)"
        if len(output) > 4000:
            output = output[:4000] + "\n...(truncated)"
        await update.message.reply_text(output)
    except asyncio.TimeoutError:
        await update.message.reply_text("Command timed out (30s)")
    except Exception as e:
        await update.message.reply_text(f"Error: {e}")


async def handle_confirm(update, context):
    """Confirm a pending destructive command."""
    if not is_authorized(update.effective_user.id):
        return
    chat_id = update.effective_chat.id
    pending = _pending_confirms.get(chat_id)
    if not pending:
        await update.message.reply_text("No pending command to confirm.")
        return
    cmd, event, confirmed = pending
    confirmed[0] = True
    event.set()


async def handle_cancel(update, context):
    """Cancel a pending destructive command."""
    if not is_authorized(update.effective_user.id):
        return
    chat_id = update.effective_chat.id
    pending = _pending_confirms.get(chat_id)
    if not pending:
        await update.message.reply_text("No pending command to cancel.")
        return
    cmd, event, confirmed = pending
    confirmed[0] = False
    event.set()


async def handle_photo(update, context):
    """Handle received photos — save and auto-submit as job if caption present."""
    if not is_authorized(update.effective_user.id):
        return
    photo = update.message.photo[-1]  # Highest resolution
    file = await context.bot.get_file(photo.file_id)
    filename = f"photo_{photo.file_unique_id}.jpg"
    save_path = UPLOADS_DIR / filename
    await file.download_to_drive(str(save_path))

    caption = update.message.caption
    if caption:
        # Auto-submit as job with photo reference
        _save_chat_id(update.effective_chat.id)
        _log_chat("user", f"[photo] {caption}")
        prompt = f"{caption}\n\nImage attached at: {save_path}"
        prompt_with_context = _build_prompt_with_context(prompt)
        user_name = update.effective_user.first_name or update.effective_user.username or "unknown"
        try:
            client = _get_http_client()
            if True:
                resp = await client.post(
                    f"{LISTEN_URL}/job",
                    json={"prompt": prompt_with_context, "submitted_by": user_name},
                    timeout=30,
                )
                if resp.status_code == 200:
                    data = resp.json()
                    job_id = data.get("job_id", data.get("id", "unknown"))
                    asyncio.create_task(_poll_and_reply(update.effective_chat.id, job_id, context))
                elif resp.status_code == 429:
                    await update.message.reply_text("All job slots are full right now. Try again in a minute.")
                else:
                    await update.message.reply_text(f"Something went wrong (HTTP {resp.status_code}), try again.")
        except httpx.TimeoutException:
            await update.message.reply_text("The server is overloaded right now. Try again in a minute.")
        except Exception as e:
            logger.error(f"Failed to submit photo job: {type(e).__name__}: {e}")
            await update.message.reply_text(f"Something went wrong ({type(e).__name__}). Try again.")
    else:
        await update.message.reply_text(
            f"Photo saved: {save_path}\n"
            f"Reference it in a job prompt with: /job Use the image at {save_path} to ..."
        )


async def handle_voice(update, context):
    """Handle received voice messages — save and auto-submit as job if caption present."""
    if not is_authorized(update.effective_user.id):
        return
    voice = update.message.voice
    file = await context.bot.get_file(voice.file_id)
    filename = f"voice_{voice.file_unique_id}.ogg"
    save_path = UPLOADS_DIR / filename
    await file.download_to_drive(str(save_path))

    caption = update.message.caption or ""
    _save_chat_id(update.effective_chat.id)
    _log_chat("user", f"[voice: {filename}] {caption}")

    if caption:
        prompt = f"{caption}\n\nVoice message attached at: {save_path}"
        prompt_with_context = _build_prompt_with_context(prompt)
        user_name = update.effective_user.first_name or update.effective_user.username or "unknown"
        try:
            client = _get_http_client()
            if True:
                resp = await client.post(
                    f"{LISTEN_URL}/job",
                    json={"prompt": prompt_with_context, "submitted_by": user_name},
                    timeout=30,
                )
                if resp.status_code == 200:
                    data = resp.json()
                    job_id = data.get("job_id", data.get("id", "unknown"))
                    asyncio.create_task(_poll_and_reply(update.effective_chat.id, job_id, context))
                elif resp.status_code == 429:
                    await update.message.reply_text("All job slots are full right now. Try again in a minute.")
                else:
                    await update.message.reply_text(f"Something went wrong (HTTP {resp.status_code}), try again.")
        except httpx.TimeoutException:
            await update.message.reply_text("The server is overloaded right now. Try again in a minute.")
        except Exception as e:
            logger.error(f"Failed to submit voice job: {type(e).__name__}: {e}")
            await update.message.reply_text(f"Something went wrong ({type(e).__name__}). Try again.")
    else:
        await update.message.reply_text(
            f"Voice saved: {save_path}\n"
            f"Reference it in a job prompt with: /job Use the voice at {save_path} to ..."
        )


async def handle_audio(update, context):
    """Handle received audio files — save and auto-submit as job if caption present."""
    if not is_authorized(update.effective_user.id):
        return
    audio = update.message.audio
    file = await context.bot.get_file(audio.file_id)
    raw_name = audio.file_name or f"audio_{audio.file_unique_id}.mp3"
    filename = os.path.basename(raw_name)
    if not filename:
        filename = f"audio_{audio.file_unique_id}.mp3"
    save_path = UPLOADS_DIR / filename
    if not save_path.resolve().is_relative_to(UPLOADS_DIR.resolve()):
        await update.message.reply_text("Invalid filename.")
        return
    await file.download_to_drive(str(save_path))

    caption = update.message.caption or ""
    _save_chat_id(update.effective_chat.id)
    _log_chat("user", f"[audio: {filename}] {caption}")

    if caption:
        prompt = f"{caption}\n\nAudio file attached at: {save_path}"
        prompt_with_context = _build_prompt_with_context(prompt)
        user_name = update.effective_user.first_name or update.effective_user.username or "unknown"
        try:
            client = _get_http_client()
            if True:
                resp = await client.post(
                    f"{LISTEN_URL}/job",
                    json={"prompt": prompt_with_context, "submitted_by": user_name},
                    timeout=30,
                )
                if resp.status_code == 200:
                    data = resp.json()
                    job_id = data.get("job_id", data.get("id", "unknown"))
                    asyncio.create_task(_poll_and_reply(update.effective_chat.id, job_id, context))
                elif resp.status_code == 429:
                    await update.message.reply_text("All job slots are full right now. Try again in a minute.")
                else:
                    await update.message.reply_text(f"Something went wrong (HTTP {resp.status_code}), try again.")
        except httpx.TimeoutException:
            await update.message.reply_text("The server is overloaded right now. Try again in a minute.")
        except Exception as e:
            logger.error(f"Failed to submit audio job: {type(e).__name__}: {e}")
            await update.message.reply_text(f"Something went wrong ({type(e).__name__}). Try again.")
    else:
        await update.message.reply_text(
            f"Audio saved: {save_path}\n"
            f"Reference it in a job prompt with: /job Use the audio at {save_path} to ..."
        )


async def handle_document(update, context):
    """Handle received documents/files — save and confirm."""
    if not is_authorized(update.effective_user.id):
        return
    doc = update.message.document
    file = await context.bot.get_file(doc.file_id)
    # Sanitize filename to prevent path traversal (e.g. ../../../etc/passwd)
    raw_name = doc.file_name or f"file_{doc.file_unique_id}"
    filename = os.path.basename(raw_name)  # Strip any directory components
    if not filename:
        filename = f"file_{doc.file_unique_id}"
    save_path = UPLOADS_DIR / filename
    # Verify the resolved path is still inside UPLOADS_DIR
    if not save_path.resolve().is_relative_to(UPLOADS_DIR.resolve()):
        await update.message.reply_text("Invalid filename.")
        return
    await file.download_to_drive(str(save_path))

    # Validate the downloaded file isn't corrupted (all null bytes)
    file_bytes = save_path.read_bytes()
    if len(file_bytes) > 0 and all(b == 0 for b in file_bytes[:1024]):
        logger.warning(f"Downloaded file {filename} appears corrupted (null bytes)")
        await update.message.reply_text(
            f"The file {filename} downloaded as corrupted data (all null bytes). "
            f"This usually happens when the file hasn't fully synced from cloud storage "
            f"(iCloud, Google Drive, etc.) on your device. Try opening the file on your "
            f"phone first to make sure it's fully downloaded, then re-send it."
        )
        return

    caption = update.message.caption
    if caption:
        # Auto-submit as job with file reference
        _save_chat_id(update.effective_chat.id)
        _log_chat("user", f"[file: {filename}] {caption}")
        prompt = f"{caption}\n\nFile attached at: {save_path}"
        user_name = update.effective_user.first_name or update.effective_user.username or "unknown"
        try:
            client = _get_http_client()
            if True:
                resp = await client.post(
                    f"{LISTEN_URL}/job",
                    json={"prompt": prompt, "submitted_by": user_name},
                    timeout=30,
                )
                if resp.status_code == 200:
                    data = resp.json()
                    job_id = data.get("job_id", data.get("id", "unknown"))
                    asyncio.create_task(_poll_and_reply(update.effective_chat.id, job_id, context))
                elif resp.status_code == 429:
                    await update.message.reply_text("All job slots are full right now. Try again in a minute.")
                else:
                    await update.message.reply_text(f"Something went wrong (HTTP {resp.status_code}), try again.")
        except httpx.TimeoutException:
            await update.message.reply_text("The server is overloaded right now. Try again in a minute.")
        except Exception as e:
            logger.error(f"Failed to submit file job: {type(e).__name__}: {e}")
            await update.message.reply_text(f"Something went wrong ({type(e).__name__}). Try again.")
    else:
        await update.message.reply_text(
            f"File saved: {save_path}\n"
            f"Reference it in a job prompt with: /job Use the file at {save_path} to ..."
        )


async def handle_cron(update, context):
    """Manage cron jobs. Usage:
    /cron list — show all crons
    /cron add <schedule> | <name> | <prompt> — create a cron
    /cron del <id> — delete a cron
    /cron toggle <id> — enable/disable a cron
    /cron edit <id> schedule <new_schedule> — edit schedule
    /cron edit <id> name <new_name> — edit name
    /cron edit <id> prompt <new_prompt> — edit prompt
    /cron trigger <id> — fire a cron right now
    """
    if not is_authorized(update.effective_user.id):
        return

    args = context.args if context.args else []
    if not args:
        await update.message.reply_text(
            "Cron Commands:\n\n"
            "/cron list — show all crons\n"
            "/cron add <crontab> | <name> | <prompt>\n"
            "  e.g. /cron add 3 7 * * * | Morning Briefing | Get weather and news\n"
            "/cron del <id> — delete a cron\n"
            "/cron toggle <id> — enable/disable\n"
            "/cron edit <id> schedule|name|prompt <value>\n"
            "/cron trigger <id> — fire now (for testing)\n"
            "\nCrontab format: min hour day month weekday\n"
            "Examples: '0 9 * * *' = 9am daily, '0 9 * * 1-5' = 9am weekdays"
        )
        return

    subcommand = args[0].lower()

    if subcommand == "list":
        try:
            client = _get_http_client()
            if True:
                resp = await client.get(f"{LISTEN_URL}/crons", timeout=10)
                if resp.status_code == 200:
                    data = resp.json()
                    crons = data.get("crons", [])
                    if not crons:
                        await update.message.reply_text("No crons set up yet. Use /cron add to create one.")
                        return
                    lines = []
                    for c in crons:
                        status = "ON" if c.get("enabled", True) else "OFF"
                        lines.append(
                            f"[{status}] {c['id']}: {c.get('name', '?')}\n"
                            f"  Schedule: {c.get('schedule', '?')} ({c.get('timezone', 'US/Central')})\n"
                            f"  Prompt: {c.get('prompt', '?')[:80]}{'...' if len(c.get('prompt', '')) > 80 else ''}"
                        )
                    await update.message.reply_text("\n\n".join(lines))
                else:
                    await update.message.reply_text(f"Error: {resp.status_code}")
        except Exception as e:
            await update.message.reply_text(f"Failed: {e}")

    elif subcommand == "add":
        # Parse: everything after "add" joined, split by |
        raw = " ".join(args[1:])
        parts = [p.strip() for p in raw.split("|")]
        if len(parts) < 3:
            await update.message.reply_text(
                "Usage: /cron add <crontab> | <name> | <prompt>\n"
                "Example: /cron add 3 7 * * * | Morning Briefing | Get weather and news for Austin TX"
            )
            return

        schedule = parts[0]
        name = parts[1]
        prompt = parts[2]

        try:
            client = _get_http_client()
            if True:
                resp = await client.post(
                    f"{LISTEN_URL}/cron",
                    json={"name": name, "schedule": schedule, "prompt": prompt},
                    timeout=10,
                )
                if resp.status_code == 200:
                    cron = resp.json()
                    await update.message.reply_text(
                        f"Cron created!\n"
                        f"ID: {cron['id']}\n"
                        f"Name: {cron['name']}\n"
                        f"Schedule: {cron['schedule']}\n"
                        f"Prompt: {cron['prompt'][:100]}"
                    )
                else:
                    error = resp.json().get("detail", resp.text)
                    await update.message.reply_text(f"Failed to create cron: {error}")
        except Exception as e:
            await update.message.reply_text(f"Error creating cron: {e}")

    elif subcommand == "del":
        if len(args) < 2:
            await update.message.reply_text("Usage: /cron del <id>")
            return
        cron_id = args[1]
        try:
            client = _get_http_client()
            if True:
                resp = await client.delete(f"{LISTEN_URL}/cron/{cron_id}", timeout=10)
                if resp.status_code == 200:
                    await update.message.reply_text(f"Cron {cron_id} deleted.")
                else:
                    await update.message.reply_text(f"Cron not found: {cron_id}")
        except Exception as e:
            await update.message.reply_text(f"Error: {e}")

    elif subcommand == "toggle":
        if len(args) < 2:
            await update.message.reply_text("Usage: /cron toggle <id>")
            return
        cron_id = args[1]
        try:
            client = _get_http_client()
            if True:
                # Get current state
                resp = await client.get(f"{LISTEN_URL}/cron/{cron_id}", timeout=10)
                if resp.status_code != 200:
                    await update.message.reply_text(f"Cron not found: {cron_id}")
                    return
                cron = resp.json()
                new_state = not cron.get("enabled", True)
                # Update
                resp = await client.put(
                    f"{LISTEN_URL}/cron/{cron_id}",
                    json={"enabled": new_state},
                    timeout=10,
                )
                if resp.status_code == 200:
                    state_str = "ON" if new_state else "OFF"
                    await update.message.reply_text(f"Cron {cron_id} ({cron.get('name', '?')}) is now {state_str}")
                else:
                    await update.message.reply_text(f"Failed to toggle: {resp.text}")
        except Exception as e:
            await update.message.reply_text(f"Error: {e}")

    elif subcommand == "edit":
        if len(args) < 4:
            await update.message.reply_text(
                "Usage: /cron edit <id> <field> <value>\n"
                "Fields: schedule, name, prompt"
            )
            return
        cron_id = args[1]
        field = args[2].lower()
        value = " ".join(args[3:])
        if field not in ("schedule", "name", "prompt", "timezone"):
            await update.message.reply_text(f"Unknown field: {field}. Use: schedule, name, prompt, timezone")
            return
        try:
            client = _get_http_client()
            if True:
                resp = await client.put(
                    f"{LISTEN_URL}/cron/{cron_id}",
                    json={field: value},
                    timeout=10,
                )
                if resp.status_code == 200:
                    cron = resp.json()
                    await update.message.reply_text(f"Updated {field} for cron {cron_id} ({cron.get('name', '?')})")
                else:
                    await update.message.reply_text(f"Failed: {resp.text}")
        except Exception as e:
            await update.message.reply_text(f"Error: {e}")

    elif subcommand == "trigger":
        if len(args) < 2:
            await update.message.reply_text("Usage: /cron trigger <id>")
            return
        cron_id = args[1]
        try:
            client = _get_http_client()
            if True:
                resp = await client.post(f"{LISTEN_URL}/cron/{cron_id}/trigger", timeout=10)
                if resp.status_code == 200:
                    await update.message.reply_text(f"Cron {cron_id} triggered! A job has been submitted.")
                    # Poll for the result
                    # We don't know the job ID here, but the cron will have fired it
                else:
                    await update.message.reply_text(f"Failed: {resp.text}")
        except Exception as e:
            await update.message.reply_text(f"Error: {e}")

    else:
        await update.message.reply_text(f"Unknown subcommand: {subcommand}. Try /cron for help.")


async def handle_reset(update, context):
    """Reset the system — stop all jobs, kill stale processes, and optionally reboot."""
    if not is_authorized(update.effective_user.id):
        return

    args = context.args if context.args else []
    mode = args[0].lower() if args else "soft"

    if mode == "hard":
        # Require confirmation before rebooting the machine
        chat_id = update.effective_chat.id
        confirmed = [False]
        event = asyncio.Event()
        _pending_confirms[chat_id] = ("reset hard (reboot machine)", event, confirmed)
        await update.message.reply_text(
            "WARNING: This will REBOOT the machine.\n\n"
            "Reply /confirm to proceed or /cancel to abort."
        )
        try:
            await asyncio.wait_for(event.wait(), timeout=60)
        except asyncio.TimeoutError:
            _pending_confirms.pop(chat_id, None)
            await update.message.reply_text("Confirmation timed out. Reboot cancelled.")
            return
        finally:
            _pending_confirms.pop(chat_id, None)

        if not confirmed[0]:
            await update.message.reply_text("Reboot cancelled.")
            return

        await update.message.reply_text("Rebooting the machine now... I'll be back in a minute or two.")

    try:
        client = _get_http_client()
        if True:
            resp = await client.post(f"{LISTEN_URL}/reset/{mode}", timeout=30)
            data = resp.json()
    except Exception as e:
        await update.message.reply_text(f"Reset failed: {e}")
        return

    if mode == "hard":
        return  # Already sent the reboot message

    lines = []
    lines.append(f"Stopped {data.get('jobs_stopped', 0)} running job(s)")
    lines.append(f"Killed {data.get('processes_killed', 0)} stale claude process(es)")
    lines.append(f"Cleaned up {data.get('sessions_killed', 0)} drive session(s)")

    await update.message.reply_text("Reset complete!\n\n" + "\n".join(lines))


async def handle_restart(update, context):
    """Restart agent services by killing process trees and re-launching scheduled tasks."""
    if not is_authorized(update.effective_user.id):
        return

    await update.message.reply_text("Restarting agent services... I'll be back in a moment.")

    try:
        repo_root = Path(__file__).resolve().parent.parent.parent
        script_path = repo_root / "restart-services.sh"

        restart_script = f"""#!/bin/bash
LOG="{repo_root}/restart.log"
TS=$(date '+%Y-%m-%d %H:%M:%S')
echo "[$TS] === Restart initiated ===" >> "$LOG"

# Kill listen server processes
LISTEN_PIDS=$(lsof -ti :7600 2>/dev/null)
if [ -n "$LISTEN_PIDS" ]; then
    echo "[$TS] Killing listen PIDs: $LISTEN_PIDS" >> "$LOG"
    echo "$LISTEN_PIDS" | xargs kill -9 2>/dev/null
fi

sleep 2

# Restart listen via launchctl if available
if launchctl list com.agent.listen &>/dev/null; then
    echo "[$TS] Restarting com.agent.listen via launchctl" >> "$LOG"
    launchctl kickstart -k "gui/$(id -u)/com.agent.listen" 2>&1 >> "$LOG"
else
    echo "[$TS] Starting listen directly" >> "$LOG"
    cd "{repo_root}/apps/listen" && nohup uv run python main.py >> "$LOG" 2>&1 &
fi

sleep 5

# Verify listen server is up
if curl -s http://localhost:7600/health > /dev/null 2>&1; then
    echo "[$TS] Listen server UP" >> "$LOG"
else
    echo "[$TS] Listen server NOT responding!" >> "$LOG"
fi

# Kill telegram bot processes
pkill -f 'apps/telegram' 2>/dev/null

sleep 2

# Restart telegram via launchctl if available
if launchctl list com.agent.telegram &>/dev/null; then
    echo "[$TS] Restarting com.agent.telegram via launchctl" >> "$LOG"
    launchctl kickstart -k "gui/$(id -u)/com.agent.telegram" 2>&1 >> "$LOG"
else
    echo "[$TS] Starting telegram directly" >> "$LOG"
    cd "{repo_root}/apps/telegram" && nohup uv run python main.py >> "$LOG" 2>&1 &
fi

echo "[$TS] === Restart complete ===" >> "$LOG"
"""

        script_path.write_text(restart_script)
        os.chmod(str(script_path), 0o755)

        log_out = open(repo_root / "restart-stderr.log", "w")
        subprocess.Popen(
            ["/bin/bash", str(script_path)],
            stdout=log_out,
            stderr=log_out,
            start_new_session=True,
            close_fds=True,
        )
    except Exception as e:
        await update.message.reply_text(f"Restart failed: {e}")


async def handle_text(update, context):
    """Handle plain text messages — treat as job prompts."""
    if not is_authorized(update.effective_user.id):
        return
    text = update.message.text
    if not text:
        return
    _save_chat_id(update.effective_chat.id)
    _log_chat("user", text)

    # Treat plain text as a job submission, with chat history context
    prompt_with_context = _build_prompt_with_context(text)
    user_name = update.effective_user.first_name or update.effective_user.username or "unknown"
    try:
        client = _get_http_client()
        if True:
            resp = await client.post(
                f"{LISTEN_URL}/job",
                json={"prompt": prompt_with_context, "submitted_by": user_name},
                timeout=30,
            )
            if resp.status_code == 200:
                data = resp.json()
                job_id = data.get("job_id", data.get("id", "unknown"))
                await update.message.reply_text(f"On it! (job {job_id})")
                asyncio.create_task(_poll_and_reply(update.effective_chat.id, job_id, context))
            elif resp.status_code == 429:
                await update.message.reply_text("All job slots are full right now. Try again in a minute.")
            else:
                await update.message.reply_text(f"Something went wrong (HTTP {resp.status_code}), try again.")
    except httpx.TimeoutException:
        await update.message.reply_text("The server is overloaded right now. Try again in a minute.")
    except Exception as e:
        logger.error(f"Failed to submit job: {type(e).__name__}: {e}")
        await update.message.reply_text(f"Failed to process ({type(e).__name__}). Try again.")


# ---------------------------------------------------------------------------
# Webhook notification server — instant job completion delivery
# ---------------------------------------------------------------------------

WEBHOOK_PORT = int(os.environ.get("TELEGRAM_WEBHOOK_PORT", "7601"))


async def _handle_webhook_connection(reader, writer):
    """Handle an incoming HTTP request from the listen server notifying job completion."""
    try:
        data = await asyncio.wait_for(reader.read(4096), timeout=5)
        request = data.decode("utf-8", errors="replace")

        # Parse the request line: POST /notify/<job_id> HTTP/1.1
        first_line = request.split("\r\n", 1)[0] if "\r\n" in request else request.split("\n", 1)[0]
        parts = first_line.split()
        job_id = None
        if len(parts) >= 2 and parts[1].startswith("/notify/"):
            job_id = parts[1].split("/notify/", 1)[1].strip("/")

        if job_id:
            logger.info(f"Webhook: received completion notification for job {job_id}")
            # Wake up the polling coroutine instantly
            event = _job_events.get(job_id)
            if event:
                event.set()
            else:
                # No active poller — trigger immediate delivery via recover
                logger.info(f"Webhook: no active poller for {job_id}, triggering recovery")
                chat_id_file = JOBS_DIR / ".chat_id"
                if chat_id_file.exists() and _webhook_bot:
                    chat_id = int(chat_id_file.read_text().strip())
                    asyncio.create_task(recover_undelivered(_webhook_bot, chat_id))

            response = b"HTTP/1.1 200 OK\r\nContent-Length: 2\r\nConnection: close\r\n\r\nOK"
        else:
            response = b"HTTP/1.1 400 Bad Request\r\nContent-Length: 11\r\nConnection: close\r\n\r\nBad Request"

        writer.write(response)
        await writer.drain()
    except Exception as e:
        logger.error(f"Webhook handler error: {e}")
    finally:
        writer.close()


async def start_webhook_server(bot):
    """Start the lightweight webhook server for instant job completion notifications.

    The listen server POSTs to http://localhost:{WEBHOOK_PORT}/notify/{job_id}
    when a job finishes, which wakes up the polling coroutine instantly.
    """
    global _webhook_bot
    _webhook_bot = bot
    try:
        server = await asyncio.start_server(
            _handle_webhook_connection, "127.0.0.1", WEBHOOK_PORT
        )
        logger.info(f"Webhook notification server listening on 127.0.0.1:{WEBHOOK_PORT}")
    except Exception as e:
        logger.warning(f"Could not start webhook server on port {WEBHOOK_PORT}: {e}")
        logger.warning("Falling back to polling-only delivery")
