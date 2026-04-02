"""Multi-app workflow engine — Coordinate actions across apps on Windows.

Provides pre-built workflow templates and a step runner that orchestrates
actions across steer, drive, browser, outlook, and gmail. Workflows
are defined as YAML/JSON step sequences.

Usage:
    cd apps\\workflow && uv run python main.py <command> --json
"""

import json
import os
import re
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Any, Optional

import typer

app = typer.Typer(help="Multi-app workflow engine")

REPO_ROOT = Path(__file__).parent.parent.parent
APPS_DIR = REPO_ROOT / "apps"


def _output(data: dict, as_json: bool = False):
    if as_json:
        print(json.dumps(data, indent=2, default=str))
    else:
        for k, v in data.items():
            print(f"{k}: {v}")


def _run_app(app_name: str, args: list[str], timeout: int = 60) -> dict:
    """Run a command in one of the agent apps and return JSON result."""
    app_dir = APPS_DIR / app_name
    cmd = [sys.executable, str(app_dir / "main.py")] + args + ["--json"]

    try:
        proc = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            cwd=str(app_dir),
        )
        try:
            stdout, stderr = proc.communicate(timeout=timeout)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait(timeout=5)
            return {"ok": False, "error": f"Timeout after {timeout}s"}

        try:
            return json.loads(stdout)
        except json.JSONDecodeError:
            return {
                "ok": proc.returncode == 0,
                "stdout": stdout[:2000],
                "stderr": stderr[:500],
            }
    except Exception as e:
        return {"ok": False, "error": str(e)}


def _resolve_ref(context: dict, ref: str) -> Any:
    """Resolve a dotted reference like 'step1.subject' against the context dict.

    Supports nested keys: step0.emails.0.subject resolves to
    context["step0"]["emails"][0]["subject"].
    """
    parts = ref.strip().split(".")
    val: Any = context
    for part in parts:
        if isinstance(val, dict) and part in val:
            val = val[part]
        elif isinstance(val, (list, tuple)):
            try:
                val = val[int(part)]
            except (ValueError, IndexError):
                return ref  # unresolvable, return raw ref
        else:
            return ref  # unresolvable
    return val


def _interpolate(text: str, context: dict) -> str:
    """Replace all {{ref}} placeholders in *text* with values from context.

    If the resolved value is not a string, it is JSON-serialised so that
    complex objects (lists, dicts) can be passed between steps.
    """
    def _replacer(match: re.Match) -> str:
        ref = match.group(1).strip()
        val = _resolve_ref(context, ref)
        if val is ref:  # unresolved — keep original placeholder
            return match.group(0)
        if isinstance(val, str):
            return val
        return json.dumps(val, default=str)

    return re.sub(r"\{\{(.+?)\}\}", _replacer, text)


def _interpolate_args(args: list, context: dict) -> list[str]:
    """Interpolate every argument string in *args* against context."""
    return [_interpolate(str(a), context) for a in args]


# ---- Workflow Templates ----


@app.command()
def email_research(
    query: str = typer.Argument(..., help="Topic to research"),
    send_to: Optional[str] = typer.Option(None, help="Email results to this address"),
    json_output: bool = typer.Option(False, "--json", help="JSON output"),
):
    """Research a topic in the browser, then email the results.

    Steps:
    1. Launch Chrome and search for the topic
    2. Extract search results
    3. Send summary via Outlook (or Gmail if Outlook unavailable)
    """
    results = {"ok": True, "action": "email_research", "steps": []}

    # Step 1: Search
    step1 = _run_app("browser", ["goto", f"https://www.google.com/search?q={query}"])
    results["steps"].append({"name": "search", "result": step1})

    if not step1.get("ok"):
        results["ok"] = False
        results["error"] = "Failed to open browser for search"
        _output(results, json_output)
        return

    # Step 2: Wait for results
    time.sleep(2)

    # Step 3: Extract search results
    step2 = _run_app("browser", ["extract", "#search", "--all"], timeout=15)
    results["steps"].append({"name": "extract", "result": step2})

    search_text = ""
    if step2.get("ok"):
        extracted = step2.get("results", []) or [step2.get("text", "")]
        search_text = "\n".join(str(r) for r in extracted[:10])
    else:
        search_text = f"Search results for: {query} (extraction failed, manual review needed)"

    # Step 4: Send email with results
    if send_to:
        subject = f"Research: {query}"
        body = f"Here are the research results for: {query}\n\n{search_text[:3000]}"

        # Try Outlook first, fallback to Gmail
        step3 = _run_app("outlook", ["send", send_to, subject, body])
        if not step3.get("ok"):
            step3 = _run_app("gmail", ["send", "--to", send_to, "--subject", subject, "--body", body])

        results["steps"].append({"name": "send_email", "result": step3})

    results["summary"] = f"Researched '{query}'" + (f" and emailed results to {send_to}" if send_to else "")
    _output(results, json_output)


@app.command()
def email_to_calendar(
    entry_id: str = typer.Argument(..., help="Email EntryID containing meeting details"),
    json_output: bool = typer.Option(False, "--json", help="JSON output"),
):
    """Read an email and create a calendar event from its contents.

    Steps:
    1. Read the email via Outlook
    2. Extract date/time/location from body
    3. Create calendar event
    """
    results = {"ok": True, "action": "email_to_calendar", "steps": []}

    # Step 1: Read email
    step1 = _run_app("outlook", ["read", entry_id])
    results["steps"].append({"name": "read_email", "result": step1})

    if not step1.get("ok"):
        results["ok"] = False
        results["error"] = "Failed to read email"
        _output(results, json_output)
        return

    results["email_subject"] = step1.get("subject", "")
    results["email_body_preview"] = step1.get("body", "")[:500]

    # Step 2: Create calendar event from email contents
    subject = step1.get("subject", "Meeting")
    body = step1.get("body", "")

    # Use email subject as event title, default to 1 hour from now
    from datetime import datetime, timedelta
    default_start = (datetime.now() + timedelta(hours=1)).strftime("%Y-%m-%d %H:%M")

    create_args = ["create-event", subject, default_start, "--duration", "60"]
    if body:
        create_args += ["--body", body[:2000]]

    step2 = _run_app("outlook", create_args)
    results["steps"].append({"name": "create_event", "result": step2})

    if not step2.get("ok"):
        results["ok"] = False
        results["error"] = "Failed to create calendar event"

    _output(results, json_output)


@app.command()
def daily_digest(
    email: Optional[str] = typer.Option(None, help="Email address to send digest to"),
    json_output: bool = typer.Option(False, "--json", help="JSON output"),
):
    """Generate a daily digest: unread emails + calendar events.

    Steps:
    1. Fetch unread emails from Outlook
    2. Fetch today's calendar events
    3. Compile into summary
    4. Optionally email the digest
    """
    results = {"ok": True, "action": "daily_digest", "steps": []}

    # Step 1: Unread emails
    step1 = _run_app("outlook", ["inbox", "--unread", "--count", "10"])
    results["steps"].append({"name": "unread_emails", "result": step1})

    unread = step1.get("emails", []) if step1.get("ok") else []

    # Step 2: Today's calendar
    step2 = _run_app("outlook", ["calendar", "--days", "1"])
    results["steps"].append({"name": "calendar", "result": step2})

    events = step2.get("events", []) if step2.get("ok") else []

    # Step 3: Compile digest
    digest_lines = ["DAILY DIGEST", "=" * 40, ""]

    digest_lines.append(f"UNREAD EMAILS ({len(unread)})")
    digest_lines.append("-" * 20)
    for e in unread:
        digest_lines.append(f"  From: {e.get('from_name', e.get('from', '?'))}")
        digest_lines.append(f"  Subject: {e.get('subject', '?')}")
        digest_lines.append("")

    digest_lines.append(f"\nTODAY'S EVENTS ({len(events)})")
    digest_lines.append("-" * 20)
    for ev in events:
        digest_lines.append(f"  {ev.get('start', '?')} - {ev.get('subject', '?')}")
        if ev.get("location"):
            digest_lines.append(f"  Location: {ev['location']}")
        digest_lines.append("")

    digest = "\n".join(digest_lines)
    results["digest"] = digest

    # Step 4: Email if requested
    if email:
        step3 = _run_app("outlook", ["send", email, "Daily Digest", digest])
        if not step3.get("ok"):
            step3 = _run_app("gmail", ["send", "--to", email, "--subject", "Daily Digest", "--body", digest])
        results["steps"].append({"name": "send_digest", "result": step3})

    results["unread_count"] = len(unread)
    results["event_count"] = len(events)
    _output(results, json_output)


@app.command()
def screenshot_report(
    apps: str = typer.Argument(..., help="Comma-separated app names to screenshot"),
    save_dir: Optional[str] = typer.Option(None, help="Directory to save screenshots"),
    json_output: bool = typer.Option(False, "--json", help="JSON output"),
):
    """Take screenshots of multiple apps for a visual report.

    Steps:
    1. Focus each app
    2. Take screenshot
    3. Collect all screenshots
    """
    results = {"ok": True, "action": "screenshot_report", "steps": [], "screenshots": []}

    save_path = Path(save_dir) if save_dir else Path(tempfile.gettempdir()) / "workflow-screenshots"
    save_path.mkdir(exist_ok=True)

    for app_name in apps.split(","):
        app_name = app_name.strip()
        if not app_name:
            continue

        # Focus the app
        step_focus = _run_app("steer", ["apps", "activate", app_name])
        results["steps"].append({"name": f"focus_{app_name}", "result": step_focus})
        time.sleep(1)

        # Screenshot
        screenshot_file = str(save_path / f"{app_name}-{int(time.time())}.png")
        step_screenshot = _run_app("steer", ["see", "--app", app_name])
        results["steps"].append({"name": f"screenshot_{app_name}", "result": step_screenshot})

        if step_screenshot.get("ok") and step_screenshot.get("screenshot"):
            results["screenshots"].append({
                "app": app_name,
                "path": step_screenshot["screenshot"],
            })

    _output(results, json_output)


@app.command()
def open_apps(
    apps_list: str = typer.Argument(..., help="Comma-separated apps to open (e.g., 'outlook,chrome,excel')"),
    json_output: bool = typer.Option(False, "--json", help="JSON output"),
):
    """Open multiple applications and arrange them on screen.

    Steps:
    1. Launch each app
    2. Wait for each to be ready
    3. Tile windows
    """
    results = {"ok": True, "action": "open_apps", "steps": [], "launched": []}

    app_names = [a.strip() for a in apps_list.split(",") if a.strip()]

    for app_name in app_names:
        # Launch
        step = _run_app("steer", ["apps", "launch", app_name])
        results["steps"].append({"name": f"launch_{app_name}", "result": step})

        if step.get("ok"):
            results["launched"].append(app_name)

        # Wait for app to be ready
        _run_app("steer", ["wait", "--app", app_name, "--timeout", "10"])
        time.sleep(1)

    # Get screen info for tiling
    screens = _run_app("steer", ["screens"])
    if screens.get("ok") and screens.get("screens"):
        screen = screens["screens"][0]
        width = screen.get("width", 1920)
        height = screen.get("height", 1080)

        # Simple tiling: split screen evenly
        n = len(results["launched"])
        if n == 2:
            # Side by side
            for i, app_name in enumerate(results["launched"]):
                x = i * (width // 2)
                _run_app("steer", ["window", "move", app_name, "-x", str(x), "-y", "0"])
                _run_app("steer", ["window", "resize", app_name, "-w", str(width // 2), "-h", str(height)])
        elif n == 3:
            # Left half + two stacked on right
            _run_app("steer", ["window", "move", results["launched"][0], "-x", "0", "-y", "0"])
            _run_app("steer", ["window", "resize", results["launched"][0], "-w", str(width // 2), "-h", str(height)])
            for i, app_name in enumerate(results["launched"][1:]):
                y = i * (height // 2)
                _run_app("steer", ["window", "move", app_name, "-x", str(width // 2), "-y", str(y)])
                _run_app("steer", ["window", "resize", app_name, "-w", str(width // 2), "-h", str(height // 2)])
        elif n >= 4:
            # Quadrant layout
            positions = [(0, 0), (width // 2, 0), (0, height // 2), (width // 2, height // 2)]
            for i, app_name in enumerate(results["launched"][:4]):
                x, y = positions[i]
                _run_app("steer", ["window", "move", app_name, "-x", str(x), "-y", str(y)])
                _run_app("steer", ["window", "resize", app_name, "-w", str(width // 2), "-h", str(height // 2)])

    results["summary"] = f"Launched and tiled {len(results['launched'])} apps: {', '.join(results['launched'])}"
    _output(results, json_output)


@app.command()
def run_steps(
    steps_file: str = typer.Argument(..., help="Path to YAML/JSON file with workflow steps"),
    json_output: bool = typer.Option(False, "--json", help="JSON output"),
):
    """Execute a custom workflow from a YAML/JSON step file.

    Step file format (YAML):
        steps:
          - app: browser
            command: goto
            args: ["https://example.com"]
            timeout: 30
          - app: browser
            command: extract
            args: ["h1"]
          - app: outlook
            command: send
            args: ["user@example.com", "Page title", "{{step1.text}}"]

    Variable interpolation:
        Each step's JSON output is stored in the context as "stepN" (0-indexed).
        Subsequent steps can reference earlier results using {{stepN.key}} in
        their args. Nested access is supported: {{step0.emails.0.subject}}.
        Initial variables can be seeded via a top-level "vars" mapping in the
        workflow file, accessible as {{vars.key}}.

    Each step runs sequentially. If a step fails, the workflow stops
    unless 'continue_on_error: true' is set on that step.
    """
    import yaml

    steps_path = Path(steps_file)
    if not steps_path.exists():
        _output({"ok": False, "error": f"File not found: {steps_file}"}, json_output)
        return

    content = steps_path.read_text()
    if steps_path.suffix in (".yml", ".yaml"):
        workflow = yaml.safe_load(content)
    else:
        workflow = json.loads(content)

    steps = workflow.get("steps", [])

    # Context holds all step outputs and user-defined variables.
    context: dict[str, Any] = {}

    # Seed context with top-level "vars" from the workflow file.
    if "vars" in workflow and isinstance(workflow["vars"], dict):
        context["vars"] = workflow["vars"]

    results = {
        "ok": True,
        "action": "run_steps",
        "total_steps": len(steps),
        "completed": 0,
        "failed": 0,
        "steps": [],
    }

    for i, step in enumerate(steps):
        app_name = step.get("app", "steer")
        command = step.get("command", "")
        args = step.get("args", [])
        timeout = step.get("timeout", 60)
        continue_on_error = step.get("continue_on_error", False)
        delay = step.get("delay", 0)
        store_as = step.get("store_as", None)  # optional custom context key

        if delay:
            time.sleep(delay)

        # Interpolate variable references in command and args
        command = _interpolate(command, context)
        args = _interpolate_args(args, context)

        step_result = _run_app(app_name, [command] + args, timeout=timeout)

        # Store result in context under "stepN" and optional custom key
        context[f"step{i}"] = step_result
        if store_as:
            context[store_as] = step_result

        results["steps"].append({
            "index": i,
            "app": app_name,
            "command": command,
            "result": step_result,
        })

        if step_result.get("ok"):
            results["completed"] += 1
        else:
            results["failed"] += 1
            if not continue_on_error:
                results["ok"] = False
                results["error"] = f"Step {i} failed: {step_result.get('error', 'unknown')}"
                break

    results["context_keys"] = list(context.keys())
    _output(results, json_output)


@app.command()
def list_templates(
    json_output: bool = typer.Option(False, "--json", help="JSON output"),
):
    """List available workflow templates."""
    templates = [
        {
            "name": "email-research",
            "description": "Search a topic in Chrome, email results via Outlook",
            "command": "email-research <query> [--send-to EMAIL]",
        },
        {
            "name": "email-to-calendar",
            "description": "Read an email and extract calendar event details",
            "command": "email-to-calendar <entry_id>",
        },
        {
            "name": "daily-digest",
            "description": "Compile unread emails + calendar into a digest",
            "command": "daily-digest [--email ADDRESS]",
        },
        {
            "name": "screenshot-report",
            "description": "Screenshot multiple apps for a visual report",
            "command": "screenshot-report <app1,app2,...>",
        },
        {
            "name": "open-apps",
            "description": "Launch and tile multiple apps on screen",
            "command": "open-apps <app1,app2,...>",
        },
        {
            "name": "run-steps",
            "description": "Execute a custom YAML/JSON workflow step file",
            "command": "run-steps <steps_file.yaml>",
        },
    ]
    _output({
        "ok": True,
        "action": "list_templates",
        "templates": templates,
    }, json_output)


if __name__ == "__main__":
    app()
