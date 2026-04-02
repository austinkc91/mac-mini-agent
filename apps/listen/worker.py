"""Job worker — runs a Claude Code agent on macOS.

Spawns the claude CLI, then waits for completion via one of three signals
(checked in priority order every 2 seconds):

1. **Completion signal file** — written by POST /job/{id}/complete when the
   agent explicitly signals it's done. This is the primary, authoritative
   completion path. The job is already marked completed in the DB by the
   endpoint; the worker just needs to kill the CLI and exit.

2. **Process exit** — the claude CLI exited on its own. The worker marks the
   job completed/failed based on exit code and stdout.

3. **Overall timeout** — MAX_JOB_DURATION reached. Worker kills the process
   and marks the job as timed out.
"""

import os
import signal
import subprocess
import sys
import tempfile
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path

import db

MAX_JOB_DURATION = 4 * 3600  # 4 hours max per job
POLL_INTERVAL = 2  # seconds between checks

# Global state for signal handler
_job_id: str = ""
_start_time: float = 0.0
_process: subprocess.Popen | None = None
_shutdown_requested: bool = False


def _signal_file(job_id: str) -> Path:
    """Path to the completion signal file for a job."""
    return Path(tempfile.gettempdir()) / f"job-complete-{job_id}"


def _handle_sigterm(signum, frame):
    global _shutdown_requested
    _shutdown_requested = True


def _do_shutdown_cleanup():
    """Mark job as stopped and kill the process."""
    if _job_id:
        try:
            job = db.sync_get_job(_job_id)
            if job and job.get("status") == "running":
                duration = round(time.time() - _start_time)
                now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
                db.sync_update_job(
                    _job_id,
                    status="stopped",
                    exit_code=143,
                    duration_seconds=duration,
                    completed_at=now,
                )
        except Exception:
            pass

    _kill_process()
    sys.exit(143)


def _kill_process():
    """Terminate/kill the claude CLI process."""
    if _process and _process.poll() is None:
        try:
            _process.terminate()
            _process.wait(timeout=5)
        except Exception:
            try:
                _process.kill()
            except Exception:
                pass


def main():
    global _job_id, _start_time, _process

    if len(sys.argv) < 3:
        print("Usage: worker.py <job_id> <prompt>")
        sys.exit(1)

    job_id = sys.argv[1]
    prompt_file = Path(sys.argv[2])
    _job_id = job_id

    # Read prompt from temp file (avoids Windows CLI arg length limits)
    prompt = prompt_file.read_text(encoding="utf-8")
    prompt_file.unlink(missing_ok=True)

    job_data = db.sync_get_job(job_id)
    if not job_data:
        print(f"Job not found in database: {job_id}")
        sys.exit(1)

    try:
        signal.signal(signal.SIGTERM, _handle_sigterm)
    except (OSError, ValueError):
        pass

    repo_root = Path(__file__).parent.parent.parent
    sys_prompt_file = (
        repo_root / ".claude" / "agents" / "listen-drive-and-steer-system-prompt.md"
    )
    sys_prompt = sys_prompt_file.read_text().replace("{{JOB_ID}}", job_id)

    submitted_by = job_data.get("submitted_by")
    submitted_by_email = job_data.get("submitted_by_email")
    if submitted_by:
        user_context = f"\n\n# Submitted By\n\nThis job was submitted by **{submitted_by}**."
        if submitted_by_email:
            user_context += f"\nTheir email address is: {submitted_by_email}"
        user_context += (
            "\n\nPersonalize your response for this user. "
            "If they ask for reminders or emails, send to their email address. "
            "Address them by name in your summary."
        )
        sys_prompt += user_context

    temp_dir = tempfile.gettempdir()
    sys_prompt_tmp = os.path.join(temp_dir, f"sysprompt-{job_id}.txt")
    prompt_tmp = os.path.join(temp_dir, f"prompt-{job_id}.txt")
    stderr_file = os.path.join(temp_dir, f"claude-stderr-{job_id}.txt")
    completion_file = _signal_file(job_id)

    try:
        with open(sys_prompt_tmp, "w", encoding="utf-8") as f:
            f.write(sys_prompt)
        with open(prompt_tmp, "w", encoding="utf-8") as f:
            f.write(prompt)

        session_name = f"job-{job_id}"
        model = job_data.get("model")

        start_time = time.time()
        _start_time = start_time

        env_clean = {k: v for k, v in os.environ.items() if k != "CLAUDECODE"}

        exit_code = None
        error_msg = None
        max_attempts = 2

        for attempt in range(1, max_attempts + 1):
            Path(stderr_file).unlink(missing_ok=True)
            completion_file.unlink(missing_ok=True)

            try:
                claude_args = [
                    "claude",
                    "--dangerously-skip-permissions",
                    "-p",
                    "--append-system-prompt-file", sys_prompt_tmp,
                ]
                if model:
                    claude_args.extend(["--model", model])

                stderr_fh = open(stderr_file, "w", encoding="utf-8")
                prompt_fh = open(prompt_tmp, "r", encoding="utf-8")
                proc = subprocess.Popen(
                    claude_args,
                    stdin=prompt_fh,
                    stdout=subprocess.PIPE,
                    stderr=stderr_fh,
                    cwd=str(repo_root),
                    env=env_clean,
                    start_new_session=True,
                )
                prompt_fh.close()
                _process = proc
                db.sync_update_job(job_id, session=session_name)

                # -------------------------------------------------------
                # Main wait loop: check for 3 exit conditions every 2s
                # -------------------------------------------------------
                deadline = time.monotonic() + MAX_JOB_DURATION
                stdout = b""
                completed_by_signal = False

                while True:
                    if _shutdown_requested:
                        _do_shutdown_cleanup()

                    # 1. Check for completion signal file (agent called /complete)
                    if completion_file.exists():
                        print(f"Job {job_id}: completion signal received")
                        completed_by_signal = True
                        # Give the CLI a moment to exit naturally
                        try:
                            stdout, _ = proc.communicate(timeout=5)
                        except subprocess.TimeoutExpired:
                            print(f"Job {job_id}: CLI didn't exit after signal, killing")
                            proc.kill()
                            try:
                                stdout, _ = proc.communicate(timeout=5)
                            except Exception:
                                pass
                        break

                    # 2. Check if process exited on its own
                    ret = proc.poll()
                    if ret is not None:
                        stdout = proc.stdout.read() if proc.stdout else b""
                        break

                    # 3. Check overall timeout
                    if time.monotonic() > deadline:
                        proc.kill()
                        raise TimeoutError(f"Job timed out after {MAX_JOB_DURATION}s")

                    time.sleep(POLL_INTERVAL)

                exit_code = proc.returncode

                # If the /complete endpoint already handled everything,
                # the worker just needs to exit cleanly
                if completed_by_signal:
                    exit_code = exit_code if exit_code is not None else 0
                    break

            except TimeoutError:
                exit_code = 124
                error_msg = f"Job timed out after {MAX_JOB_DURATION}s"
                print(f"Job {job_id}: {error_msg}", file=sys.stderr)
                break
            except Exception as e:
                exit_code = 1
                error_msg = str(e)
                print(f"Worker error: {e}", file=sys.stderr)
            finally:
                # Always close the stderr file handle
                try:
                    stderr_fh.close()
                except Exception:
                    pass

            stderr_content = ""
            try:
                stderr_path = Path(stderr_file)
                if stderr_path.exists():
                    stderr_content = stderr_path.read_text().strip()[-500:]
            except Exception:
                pass

            if exit_code == 0:
                error_msg = None
                break

            elapsed = time.time() - start_time
            if attempt < max_attempts and elapsed < 120:
                print(f"Job {job_id}: attempt {attempt} failed (exit {exit_code}), retrying...", file=sys.stderr)
                if stderr_content:
                    print(f"  stderr: {stderr_content[:200]}", file=sys.stderr)
                time.sleep(1)
                continue
            else:
                if stderr_content and not error_msg:
                    error_msg = stderr_content
                elif stderr_content:
                    error_msg = f"{error_msg} | stderr: {stderr_content}"
                break

        # ---------------------------------------------------------------
        # Finalize job status
        # ---------------------------------------------------------------
        current = db.sync_get_job(job_id) or {}

        # If the /complete endpoint already marked this job, skip DB update
        if current.get("status") in ("completed", "failed", "stopped"):
            print(f"Job {job_id}: already finalized as {current['status']}, worker exiting")
        else:
            duration = round(time.time() - start_time)
            now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

            status = "completed" if exit_code == 0 else "failed"
            update_fields = {
                "status": status,
                "exit_code": exit_code,
                "duration_seconds": duration,
                "completed_at": now,
            }

            if not current.get("summary"):
                if exit_code == 0 and stdout:
                    stdout_text = stdout.decode("utf-8", errors="replace").strip()
                    if stdout_text:
                        update_fields["summary"] = stdout_text
                elif exit_code != 0:
                    update_fields["summary"] = (
                        f"Job failed (exit code {exit_code}). "
                        f"{error_msg or 'The agent process exited unexpectedly.'}"
                    )

            db.sync_update_job(job_id, **update_fields)

    finally:
        Path(sys_prompt_tmp).unlink(missing_ok=True)
        Path(prompt_tmp).unlink(missing_ok=True)
        Path(stderr_file).unlink(missing_ok=True)
        completion_file.unlink(missing_ok=True)


if __name__ == "__main__":
    main()
