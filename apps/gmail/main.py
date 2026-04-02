#!/usr/bin/env python3
"""Gmail CLI tool for autonomous email management.

Provides IMAP reading and SMTP sending via GMAIL_USER env var.
Uses Google App Password authentication.

Usage:
    uv run python main.py inbox [--unread] [--limit N] [--json]
    uv run python main.py read <message_id> [--json]
    uv run python main.py search <query> [--limit N] [--json]
    uv run python main.py send <to> <subject> <body> [--json]
    uv run python main.py reply <message_id> <body> [--json]
    uv run python main.py labels [--json]
    uv run python main.py mark-read <message_id> [--json]
    uv run python main.py archive <message_id> [--json]
    uv run python main.py count [--json]
"""

import atexit
import click
import email
import email.utils
import imaplib
import json
import os
import smtplib
import sys
from datetime import datetime, timedelta
from email.header import decode_header
from email.mime.base import MIMEBase
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email import encoders
from pathlib import Path

# Load .env from project root
ENV_PATH = Path(__file__).parent.parent.parent / ".env"
if ENV_PATH.exists():
    for line in ENV_PATH.read_text().splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            key, _, value = line.partition("=")
            os.environ.setdefault(key.strip(), value.strip())

GMAIL_USER = os.environ.get("GMAIL_USER", "")
GMAIL_APP_PASSWORD = os.environ.get("GMAIL_APP_PASSWORD", "")

IMAP_HOST = "imap.gmail.com"
IMAP_PORT = 993
SMTP_HOST = "smtp.gmail.com"
SMTP_PORT = 587

# Module-level connection pool for reuse within a single process invocation.
_imap_conn = None
_smtp_conn = None


def _imap_connect():
    """Return a reusable IMAP connection, creating or reconnecting as needed."""
    global _imap_conn
    if not GMAIL_APP_PASSWORD:
        raise click.ClickException("GMAIL_APP_PASSWORD not set in .env")
    # Try to reuse existing connection
    if _imap_conn is not None:
        try:
            _imap_conn.noop()
            return _imap_conn
        except Exception:
            # Connection is stale, close and reconnect
            try:
                _imap_conn.logout()
            except Exception:
                pass
            _imap_conn = None
    conn = imaplib.IMAP4_SSL(IMAP_HOST, IMAP_PORT)
    conn.login(GMAIL_USER, GMAIL_APP_PASSWORD)
    _imap_conn = conn
    return conn


def _imap_close():
    """Close and discard the pooled IMAP connection."""
    global _imap_conn
    if _imap_conn is not None:
        try:
            _imap_conn.logout()
        except Exception:
            pass
        _imap_conn = None


def _smtp_connect():
    """Return a reusable SMTP connection, creating or reconnecting as needed."""
    global _smtp_conn
    if not GMAIL_APP_PASSWORD:
        raise click.ClickException("GMAIL_APP_PASSWORD not set in .env")
    # Try to reuse existing connection
    if _smtp_conn is not None:
        try:
            _smtp_conn.noop()
            return _smtp_conn
        except Exception:
            try:
                _smtp_conn.quit()
            except Exception:
                pass
            _smtp_conn = None
    server = smtplib.SMTP(SMTP_HOST, SMTP_PORT)
    server.ehlo()
    server.starttls()
    server.login(GMAIL_USER, GMAIL_APP_PASSWORD)
    _smtp_conn = server
    return server


def _smtp_close():
    """Close and discard the pooled SMTP connection."""
    global _smtp_conn
    if _smtp_conn is not None:
        try:
            _smtp_conn.quit()
        except Exception:
            pass
        _smtp_conn = None


def _cleanup_connections():
    """Close all pooled connections at process exit."""
    _imap_close()
    _smtp_close()


atexit.register(_cleanup_connections)


def _decode_header_value(value):
    """Decode an email header value."""
    if value is None:
        return ""
    decoded_parts = decode_header(value)
    result = []
    for part, charset in decoded_parts:
        if isinstance(part, bytes):
            result.append(part.decode(charset or "utf-8", errors="replace"))
        else:
            result.append(part)
    return " ".join(result)


def _extract_body(msg):
    """Extract plain text body from email message."""
    if msg.is_multipart():
        for part in msg.walk():
            content_type = part.get_content_type()
            content_disposition = str(part.get("Content-Disposition", ""))
            if content_type == "text/plain" and "attachment" not in content_disposition:
                payload = part.get_payload(decode=True)
                if payload:
                    charset = part.get_content_charset() or "utf-8"
                    return payload.decode(charset, errors="replace")
        # Fallback to HTML if no plain text
        for part in msg.walk():
            if part.get_content_type() == "text/html":
                payload = part.get_payload(decode=True)
                if payload:
                    charset = part.get_content_charset() or "utf-8"
                    return f"[HTML content]\n{payload.decode(charset, errors='replace')[:2000]}"
    else:
        payload = msg.get_payload(decode=True)
        if payload:
            charset = msg.get_content_charset() or "utf-8"
            return payload.decode(charset, errors="replace")
    return ""


def _parse_email(msg_data, msg_id):
    """Parse raw email data into a dict."""
    msg = email.message_from_bytes(msg_data)
    from_addr = _decode_header_value(msg.get("From", ""))
    to_addr = _decode_header_value(msg.get("To", ""))
    subject = _decode_header_value(msg.get("Subject", ""))
    date_str = msg.get("Date", "")
    message_id = msg.get("Message-ID", "")
    in_reply_to = msg.get("In-Reply-To", "")

    # Parse date
    date_tuple = email.utils.parsedate_tz(date_str)
    if date_tuple:
        timestamp = email.utils.mktime_tz(date_tuple)
        date_iso = datetime.fromtimestamp(timestamp).isoformat()
    else:
        date_iso = date_str

    return {
        "id": msg_id,
        "message_id": message_id,
        "from": from_addr,
        "to": to_addr,
        "subject": subject,
        "date": date_iso,
        "in_reply_to": in_reply_to,
        "body": _extract_body(msg),
        "_raw_msg": msg,
    }


def _fetch_emails(conn, msg_ids, include_body=False):
    """Fetch email metadata (and optionally body) for given message IDs."""
    results = []
    for mid in msg_ids:
        fetch_type = "(RFC822)" if include_body else "(BODY.PEEK[HEADER])"
        status, data = conn.fetch(mid, fetch_type)
        if status != "OK" or not data or not data[0]:
            continue
        raw = data[0][1] if isinstance(data[0], tuple) else data[0]
        if not isinstance(raw, bytes):
            continue
        parsed = _parse_email(raw, mid.decode() if isinstance(mid, bytes) else str(mid))
        if not include_body:
            parsed.pop("body", None)
        parsed.pop("_raw_msg", None)
        results.append(parsed)
    return results


@click.group()
def cli():
    """Gmail CLI tool for autonomous email management."""
    pass


@cli.command()
@click.option("--unread", is_flag=True, help="Show only unread emails")
@click.option("--limit", default=20, help="Number of emails to fetch")
@click.option("--json-output", "--json", "json_out", is_flag=True, help="JSON output")
def inbox(unread, limit, json_out):
    """List recent inbox emails."""
    conn = _imap_connect()
    conn.select("INBOX")
    criteria = "UNSEEN" if unread else "ALL"
    status, data = conn.search(None, criteria)
    if status != "OK":
        raise click.ClickException("Failed to search inbox")

    msg_ids = data[0].split()
    if not msg_ids:
        if json_out:
            click.echo(json.dumps({"emails": [], "count": 0}))
        else:
            click.echo("No emails found.")
        return

    # Get most recent N
    msg_ids = msg_ids[-limit:]
    msg_ids.reverse()  # newest first

    emails = _fetch_emails(conn, msg_ids, include_body=False)

    if json_out:
        click.echo(json.dumps({"emails": emails, "count": len(emails)}, indent=2))
    else:
        for e in emails:
            click.echo(f"[{e['id']}] {e['date'][:16]}  {e['from'][:40]:40s}  {e['subject'][:60]}")


@cli.command()
@click.argument("message_id")
@click.option("--json-output", "--json", "json_out", is_flag=True, help="JSON output")
def read(message_id, json_out):
    """Read a specific email by its IMAP ID."""
    conn = _imap_connect()
    conn.select("INBOX")
    emails = _fetch_emails(conn, [message_id.encode()], include_body=True)
    if not emails:
        raise click.ClickException(f"Email {message_id} not found")
    e = emails[0]
    if json_out:
        click.echo(json.dumps(e, indent=2))
    else:
        click.echo(f"From: {e['from']}")
        click.echo(f"To: {e['to']}")
        click.echo(f"Subject: {e['subject']}")
        click.echo(f"Date: {e['date']}")
        click.echo(f"Message-ID: {e['message_id']}")
        click.echo("-" * 60)
        click.echo(e.get("body", "(no body)"))


@cli.command()
@click.argument("query")
@click.option("--limit", default=20, help="Max results")
@click.option("--json-output", "--json", "json_out", is_flag=True, help="JSON output")
def search(query, limit, json_out):
    """Search emails using Gmail IMAP search.

    Examples:
        search "FROM john@example.com"
        search "SUBJECT meeting"
        search "SINCE 15-Mar-2026"
        search "FROM john SUBJECT proposal"
        search "UNSEEN SINCE 01-Mar-2026"
    """
    conn = _imap_connect()
    conn.select("INBOX")
    # If query looks like raw IMAP criteria, use as-is
    # Otherwise wrap in a text search
    if any(kw in query.upper() for kw in ["FROM", "TO", "SUBJECT", "SINCE", "BEFORE", "UNSEEN", "SEEN", "BODY"]):
        criteria = query
    else:
        criteria = f'(OR (SUBJECT "{query}") (FROM "{query}"))'

    status, data = conn.search(None, criteria)
    if status != "OK":
        raise click.ClickException(f"Search failed: {criteria}")

    msg_ids = data[0].split()
    if not msg_ids:
        if json_out:
            click.echo(json.dumps({"emails": [], "count": 0, "query": query}))
        else:
            click.echo(f"No emails matching: {query}")
        return

    msg_ids = msg_ids[-limit:]
    msg_ids.reverse()

    emails = _fetch_emails(conn, msg_ids, include_body=False)

    if json_out:
        click.echo(json.dumps({"emails": emails, "count": len(emails), "query": query}, indent=2))
    else:
        click.echo(f"Found {len(emails)} emails matching: {query}")
        for e in emails:
            click.echo(f"[{e['id']}] {e['date'][:16]}  {e['from'][:40]:40s}  {e['subject'][:60]}")


@cli.command()
@click.argument("to")
@click.argument("subject")
@click.argument("body")
@click.option("--cc", default=None, help="CC addresses (comma-separated)")
@click.option("--attach", multiple=True, help="File path(s) to attach (repeatable)")
@click.option("--json-output", "--json", "json_out", is_flag=True, help="JSON output")
def send(to, subject, body, cc, attach, json_out):
    """Send an email."""
    import mimetypes
    msg = MIMEMultipart()
    msg["From"] = GMAIL_USER
    msg["To"] = to
    msg["Subject"] = subject
    if cc:
        msg["Cc"] = cc
    msg.attach(MIMEText(body, "plain"))

    for filepath in attach:
        p = Path(filepath)
        if not p.exists():
            raise click.ClickException(f"Attachment not found: {filepath}")
        ctype, _ = mimetypes.guess_type(str(p))
        if ctype is None:
            ctype = "application/octet-stream"
        maintype, subtype = ctype.split("/", 1)
        with open(p, "rb") as f:
            part = MIMEBase(maintype, subtype)
            part.set_payload(f.read())
        encoders.encode_base64(part)
        part.add_header("Content-Disposition", "attachment", filename=p.name)
        msg.attach(part)

    server = _smtp_connect()
    recipients = [to]
    if cc:
        recipients.extend([a.strip() for a in cc.split(",")])
    server.sendmail(GMAIL_USER, recipients, msg.as_string())
    result = {"ok": True, "to": to, "subject": subject, "cc": cc, "attachments": [Path(a).name for a in attach]}
    if json_out:
        click.echo(json.dumps(result))
    else:
        click.echo(f"Sent email to {to}: {subject}")


@cli.command()
@click.argument("message_id")
@click.argument("body")
@click.option("--json-output", "--json", "json_out", is_flag=True, help="JSON output")
def reply(message_id, body, json_out):
    """Reply to an email by IMAP ID."""
    conn = _imap_connect()
    conn.select("INBOX")
    emails = _fetch_emails(conn, [message_id.encode()], include_body=True)
    if not emails:
        raise click.ClickException(f"Email {message_id} not found")
    original = emails[0]

    # Build reply
    reply_to = original["from"]
    # Extract just the email address
    if "<" in reply_to and ">" in reply_to:
        reply_addr = reply_to[reply_to.index("<") + 1:reply_to.index(">")]
    else:
        reply_addr = reply_to.strip()

    subject = original["subject"]
    if not subject.lower().startswith("re:"):
        subject = f"Re: {subject}"

    msg = MIMEMultipart()
    msg["From"] = GMAIL_USER
    msg["To"] = reply_addr
    msg["Subject"] = subject
    msg["In-Reply-To"] = original.get("message_id", "")
    msg["References"] = original.get("message_id", "")

    # Build reply body with quote
    orig_body = original.get("body", "")
    quoted = "\n".join(f"> {line}" for line in orig_body.split("\n")[:20])
    full_body = f"{body}\n\nOn {original['date']}, {original['from']} wrote:\n{quoted}"
    msg.attach(MIMEText(full_body, "plain"))

    server = _smtp_connect()
    server.sendmail(GMAIL_USER, [reply_addr], msg.as_string())
    result = {"ok": True, "to": reply_addr, "subject": subject, "in_reply_to": original.get("message_id", "")}
    if json_out:
        click.echo(json.dumps(result))
    else:
        click.echo(f"Replied to {reply_addr}: {subject}")


@cli.command("mark-read")
@click.argument("message_id")
@click.option("--json-output", "--json", "json_out", is_flag=True, help="JSON output")
def mark_read(message_id, json_out):
    """Mark an email as read."""
    conn = _imap_connect()
    conn.select("INBOX")
    status, _ = conn.store(message_id.encode(), "+FLAGS", "\\Seen")
    ok = status == "OK"
    if json_out:
        click.echo(json.dumps({"ok": ok, "id": message_id}))
    else:
        click.echo(f"Marked {message_id} as read" if ok else f"Failed to mark {message_id}")


@cli.command()
@click.argument("message_id")
@click.option("--json-output", "--json", "json_out", is_flag=True, help="JSON output")
def archive(message_id, json_out):
    """Archive an email (move out of inbox to All Mail)."""
    conn = _imap_connect()
    try:
        conn.select("INBOX")
        mid = message_id.encode()
        # Gmail IMAP archive: copy to All Mail, then remove from INBOX.
        # This removes the INBOX label while keeping the email in All Mail.
        copy_status, _ = conn.copy(mid, "[Gmail]/All Mail")
        if copy_status != "OK":
            # Some locales use different names; fall back to stripping INBOX
            # by just deleting+expunging (Gmail auto-keeps in All Mail)
            conn.store(mid, "+FLAGS", "\\Deleted")
            conn.expunge()
            ok = True
        else:
            # Successfully copied; now remove from INBOX
            conn.store(mid, "+FLAGS", "\\Deleted")
            conn.expunge()
            ok = True
        if json_out:
            click.echo(json.dumps({"ok": ok, "id": message_id, "action": "archived"}))
        else:
            click.echo(f"Archived {message_id}" if ok else f"Failed to archive {message_id}")
    except Exception as exc:
        if json_out:
            click.echo(json.dumps({"ok": False, "id": message_id, "error": str(exc)}))
        else:
            click.echo(f"Failed to archive {message_id}: {exc}")


@cli.command()
@click.option("--json-output", "--json", "json_out", is_flag=True, help="JSON output")
def count(json_out):
    """Count unread and total emails in inbox."""
    conn = _imap_connect()
    conn.select("INBOX")
    _, total_data = conn.search(None, "ALL")
    _, unread_data = conn.search(None, "UNSEEN")
    total = len(total_data[0].split()) if total_data[0] else 0
    unread = len(unread_data[0].split()) if unread_data[0] else 0
    if json_out:
        click.echo(json.dumps({"total": total, "unread": unread}))
    else:
        click.echo(f"Total: {total}  Unread: {unread}")


@cli.command()
@click.option("--json-output", "--json", "json_out", is_flag=True, help="JSON output")
def labels(json_out):
    """List Gmail labels/folders."""
    conn = _imap_connect()
    status, data = conn.list()
    if status != "OK":
        raise click.ClickException("Failed to list labels")
    label_list = []
    for item in data:
        if isinstance(item, bytes):
            # Parse IMAP list response
            parts = item.decode().split('"/"')
            if len(parts) >= 2:
                label_list.append(parts[-1].strip().strip('"'))
    if json_out:
        click.echo(json.dumps({"labels": label_list}))
    else:
        for label in label_list:
            click.echo(label)


if __name__ == "__main__":
    cli()
