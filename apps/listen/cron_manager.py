"""Persistent cron manager for the Listen job server.

Stores cron definitions in a YAML file and uses APScheduler to trigger
job submissions on schedule. Crons survive reboots because:
1. Definitions live in crons.yaml (persistent file)
2. The listen server (systemd service) loads them on startup
"""

import logging
import os
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

import httpx
import yaml
from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger

logger = logging.getLogger(__name__)
# Ensure cron_manager logs are visible in uvicorn output
logging.basicConfig(level=logging.INFO)

CRONS_FILE = Path(__file__).parent / "crons.yaml"
LISTEN_URL = os.environ.get("LISTEN_URL", "http://localhost:7600")

# misfire_grace_time=300 means if the scheduler checks up to 5 minutes late,
# the job still fires. Default of 1 second caused silent skips.
scheduler = BackgroundScheduler(job_defaults={
    'misfire_grace_time': 300,
    'coalesce': True,
    'max_instances': 1,
})


def _load_crons() -> list[dict]:
    """Load all cron definitions from disk."""
    if not CRONS_FILE.exists():
        return []
    with open(CRONS_FILE) as f:
        data = yaml.safe_load(f)
    return data.get("crons", []) if data else []


def _save_crons(crons: list[dict]):
    """Write cron definitions to disk atomically."""
    import tempfile as _tmpfile
    data = {"crons": crons}
    tmp_fd, tmp_path = _tmpfile.mkstemp(
        dir=CRONS_FILE.parent, suffix=".tmp", prefix="crons"
    )
    try:
        with os.fdopen(tmp_fd, "w") as f:
            yaml.dump(data, f, default_flow_style=False, sort_keys=False)
        os.replace(tmp_path, CRONS_FILE)
    except Exception:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        raise


def _fire_cron(cron_id: str, prompt: str) -> str | None:
    """Submit a job to the listen server when a cron fires. Returns job_id or None."""
    try:
        print(f"Cron firing: {cron_id} at {datetime.now()}")
        resp = httpx.post(
            f"{LISTEN_URL}/job",
            json={"prompt": prompt},
            timeout=10,
        )
        if resp.status_code == 200:
            job_data = resp.json()
            job_id = job_data.get("job_id")
            print(f"Cron {cron_id} fired → job {job_id or '?'}")
            logger.info(f"Cron {cron_id} fired → job {job_id or '?'}")
            return job_id
        else:
            print(f"Cron {cron_id} fire failed: {resp.status_code}")
            logger.error(f"Cron {cron_id} fire failed: {resp.status_code}")
    except Exception as e:
        print(f"Cron {cron_id} fire error: {e}")
        logger.error(f"Cron {cron_id} fire error: {e}")
    return None


def _schedule_cron(cron: dict):
    """Add a single cron to the APScheduler."""
    cron_id = cron["id"]
    trigger = CronTrigger.from_crontab(cron["schedule"], timezone=cron.get("timezone", "US/Central"))
    scheduler.add_job(
        _fire_cron,
        trigger=trigger,
        args=[cron_id, cron["prompt"]],
        id=cron_id,
        replace_existing=True,
        name=cron.get("name", cron_id),
    )
    logger.info(f"Scheduled cron: {cron.get('name', cron_id)} ({cron['schedule']})")


def start():
    """Load all crons from disk and start the scheduler."""
    crons = _load_crons()
    for cron in crons:
        if cron.get("enabled", True):
            try:
                _schedule_cron(cron)
            except Exception as e:
                logger.error(f"Failed to schedule cron {cron.get('id')}: {e}")
    scheduler.start()
    print(f"Cron scheduler started with {len(crons)} cron(s)")
    for job in scheduler.get_jobs():
        print(f"  Scheduled: {job.name} -> next fire: {job.next_run_time}")
    logger.info(f"Cron scheduler started with {len(crons)} cron(s)")


def stop():
    """Shutdown the scheduler."""
    if scheduler.running:
        scheduler.shutdown(wait=False)


def list_crons() -> list[dict]:
    """Return all cron definitions."""
    return _load_crons()


def get_cron(cron_id: str) -> dict | None:
    """Get a single cron by ID."""
    for cron in _load_crons():
        if cron["id"] == cron_id:
            return cron
    return None


def add_cron(name: str, schedule: str, prompt: str, timezone: str = "US/Central", enabled: bool = True) -> dict:
    """Create a new cron and schedule it.

    Args:
        name: Human-readable name (e.g. "Morning Briefing")
        schedule: Crontab expression (e.g. "3 7 * * *" for 7:03 AM)
        prompt: The job prompt to submit when the cron fires
        timezone: IANA timezone (default: US/Central)
        enabled: Whether the cron is active
    """
    cron_id = uuid4().hex[:8]

    # Validate the cron expression
    CronTrigger.from_crontab(schedule)

    now = datetime.now(tz=__import__('zoneinfo').ZoneInfo("UTC"))
    cron = {
        "id": cron_id,
        "name": name,
        "schedule": schedule,
        "prompt": prompt,
        "timezone": timezone,
        "enabled": enabled,
        "created_at": now.strftime("%Y-%m-%dT%H:%M:%SZ"),
    }

    crons = _load_crons()
    crons.append(cron)
    _save_crons(crons)

    if enabled:
        _schedule_cron(cron)

    return cron


def update_cron(cron_id: str, **kwargs) -> dict | None:
    """Update a cron's fields. Supported: name, schedule, prompt, timezone, enabled."""
    # Validate schedule before applying update
    if "schedule" in kwargs:
        try:
            CronTrigger.from_crontab(kwargs["schedule"])
        except (ValueError, KeyError) as e:
            raise ValueError(f"Invalid cron schedule '{kwargs['schedule']}': {e}")

    crons = _load_crons()
    for i, cron in enumerate(crons):
        if cron["id"] == cron_id:
            for key in ("name", "schedule", "prompt", "timezone", "enabled"):
                if key in kwargs:
                    cron[key] = kwargs[key]
            crons[i] = cron
            _save_crons(crons)

            # Reschedule or remove from scheduler
            try:
                scheduler.remove_job(cron_id)
            except Exception:
                pass
            if cron.get("enabled", True):
                _schedule_cron(cron)

            return cron
    return None


def delete_cron(cron_id: str) -> bool:
    """Delete a cron by ID."""
    crons = _load_crons()
    new_crons = [c for c in crons if c["id"] != cron_id]
    if len(new_crons) == len(crons):
        return False

    _save_crons(new_crons)
    try:
        scheduler.remove_job(cron_id)
    except Exception:
        pass
    return True


def trigger_cron(cron_id: str) -> tuple[bool, str | None]:
    """Manually trigger a cron. Returns (found, job_id)."""
    cron = get_cron(cron_id)
    if not cron:
        return False, None
    job_id = _fire_cron(cron_id, cron["prompt"])
    return True, job_id
