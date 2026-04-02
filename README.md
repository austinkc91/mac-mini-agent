# Mac Mini Agent

macOS desktop automation for AI agents. Gives Claude Code (or any AI agent) full control of your Mac — GUI automation, terminal sessions, browser control, and remote access via Telegram.

**Ported from [windows-mini-agent](https://github.com/ausitnkc91/windows-mini-agent)** — same architecture, same CLI interface, native macOS APIs.

## What It Does

- **steer** — GUI automation: screenshots, click, type, scroll, window management, OCR, accessibility tree walking (via pyobjc/Quartz)
- **drive** — Terminal automation: create/manage tmux sessions, run commands, capture output
- **listen** — Job server: FastAPI on port 7600, parallel job queue (4 concurrent), cron scheduling
- **browser** — Browser automation: Playwright + Chrome for web tasks
- **telegram** — Remote control: submit jobs, get screenshots, run commands from your phone
- **workflow** — Multi-app orchestration engine
- **gmail** — Email management via IMAP/SMTP

## Quick Start

```bash
# Install everything
chmod +x install.sh && ./install.sh

# Start the job server
just listen

# In another terminal, submit a job
just send "Take a screenshot and describe what you see"

# Or start the Telegram bot for remote control
just telegram
```

## Requirements

- macOS 12+ (Monterey or later)
- Python 3.11+
- Google Chrome
- Accessibility permissions (System Settings → Privacy & Security → Accessibility)

## Key Differences from Windows Version

| Component | Windows | macOS |
|-----------|---------|-------|
| GUI automation | Win32 API (ctypes) | Quartz + AppKit (pyobjc) |
| Window management | user32.dll | AppleScript + Quartz |
| Terminal sessions | PowerShell processes | tmux |
| File locking | msvcrt | fcntl |
| Accessibility | uiautomation | AX API (ApplicationServices) |
| Service management | Task Scheduler / NSSM | launchd |
| Package manager | Chocolatey | Homebrew |
| Outlook email | COM (win32com) | Not ported (use gmail) |

## Auto-Start on Login

```bash
chmod +x install-launchd.sh && ./install-launchd.sh
```

This creates launchd agents that start the listen server and telegram bot at login.

## Architecture

```
mac-mini-agent/
├── apps/
│   ├── steer/      — GUI automation (pyobjc, pyautogui, mss)
│   ├── drive/      — Terminal automation (tmux)
│   ├── listen/     — Job server (FastAPI, port 7600)
│   ├── browser/    — Browser automation (Playwright)
│   ├── workflow/   — Multi-app workflows
│   ├── gmail/      — Gmail CLI
│   ├── direct/     — CLI client
│   └── telegram/   — Telegram bot
├── install.sh      — Automated installer
├── install-launchd.sh — Auto-start setup
└── justfile        — Task runner
```

## License

MIT
