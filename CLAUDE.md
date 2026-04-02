# Mac Mini Agent

macOS desktop automation for AI agents. Nine Python apps that give agents full GUI + terminal + browser + workflow control, with remote access via Telegram.

## Architecture

```
mac-mini-agent/
├── apps/
│   ├── steer/      — GUI automation (pyobjc, pyautogui, mss, pytesseract)
│   ├── drive/      — Terminal automation (tmux sessions)
│   ├── listen/     — Job server (FastAPI on port 7600, parallel job queue)
│   ├── browser/    — Browser automation (Playwright + Chrome)
│   ├── workflow/   — Multi-app workflow engine
│   ├── gmail/      — Gmail CLI (IMAP/SMTP, configured via .env)
│   ├── direct/     — CLI client for Listen
│   └── telegram/   — Telegram bot for remote control from mobile
├── justfile        — Task runner (just listen, just send, just telegram, etc.)
├── install.sh      — Automated installer for macOS
└── plists/         — launchd plist files for auto-start
```

## Setup

Run the installer:

```bash
chmod +x install.sh && ./install.sh
```

Or install manually:

```bash
# Homebrew (if not installed)
/bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"

# System dependencies
brew install python@3.12
brew install tesseract
brew install tmux
brew install just

# uv (Python package manager)
curl -LsSf https://astral.sh/uv/install.sh | sh

# Google Chrome (if not installed)
brew install --cask google-chrome

# Sync all Python app dependencies
for app in steer drive listen browser workflow direct telegram gmail; do
    cd apps/$app && uv sync && cd ../..
done

# Install Playwright browsers
cd apps/browser && uv run playwright install chromium && cd ../..
```

## Running

```bash
# Verify installation
just install-check

# Start the job server
just listen

# Start Telegram bot (needs TELEGRAM_BOT_TOKEN in .env)
just telegram

# Send a job
just send "Open Chrome and navigate to github.com"

# GUI automation
cd apps/steer && uv run python main.py see --json
cd apps/steer && uv run python main.py click -x 500 -y 300
cd apps/steer && uv run python main.py ocr --store --json

# Terminal automation
cd apps/drive && uv run python main.py session create my-session --detach --json
cd apps/drive && uv run python main.py run my-session "echo hello" --json

# Browser automation (Playwright + Chrome)
cd apps/browser && uv run python main.py launch "https://github.com" --json
cd apps/browser && uv run python main.py click "#search-input" --json
cd apps/browser && uv run python main.py fill "#search-input" "hello world" --submit --json

# Multi-app workflows
cd apps/workflow && uv run python main.py open-apps "chrome,terminal" --json

# Reset
just reset
```

## Apps

### steer — GUI Automation
`cd apps/steer && uv run python main.py <command> --json`

17 commands: `see`, `click`, `type`, `hotkey`, `scroll`, `drag`, `apps`, `screens`, `window`, `ocr`, `focus`, `find`, `clipboard`, `wait`, `dismiss`, `status`, `read-text`

Uses: pyautogui (mouse/keyboard), mss (screenshots), pyobjc (window management via Quartz/AppKit), pyperclip (clipboard), pytesseract (OCR), atomacos/pyobjc (accessibility)

### drive — Terminal Automation
`cd apps/drive && uv run python main.py <command> --json`

7 commands: `session`, `run`, `send`, `logs`, `poll`, `fanout`, `proc`

Uses tmux for session management. Each session is a tmux session with output capture.

### listen — Job Server (Parallel Queue)
`cd apps/listen && uv run python main.py`

FastAPI server on port 7600. Runs up to 4 jobs simultaneously.

### browser — Browser Automation (Playwright)
`cd apps/browser && uv run python main.py <command> --json`

16 commands: `launch`, `goto`, `click`, `fill`, `extract`, `screenshot`, `tabs`, `new-tab`, `close-tab`, `execute`, `wait-for`, `cookies`, `pdf`, `select`, `scroll-page`, `close`

### workflow — Multi-App Workflows
`cd apps/workflow && uv run python main.py <command> --json`

### gmail — Email Management
`cd apps/gmail && uv run python main.py <command> --json`

### direct — CLI Client
`cd apps/direct && uv run python main.py <command>`

### telegram — Remote Control Bot
`cd apps/telegram && uv run python main.py`

## Key Patterns

- **Observe-Act-Verify**: `steer see` -> action -> `steer see` again
- **Pre-flight check**: Run `steer status --json` before automation
- **Dialog safety**: Run `steer dismiss --json` before typing/clicking
- **Read text via accessibility, not OCR**: `steer read-text --json` reads from the accessibility tree
- **VIRTUAL_ENV fix**: When calling steer from a drive session, prefix with `VIRTUAL_ENV=` to suppress uv warnings
- **Sentinel Protocol**: Drive wraps commands with `__DONE_<token>:<exit_code>` markers
- **Element IDs**: B=button, T=text, S=static, O=OCR, etc. Valid within a snapshot only
- **JSON mode**: Always pass `--json` for structured output
- **One steer command per bash call**: Screen changes after every action
- **Parallel Jobs**: Up to 4 concurrent jobs, with automatic queuing
- **Browser vs Steer**: Use `browser` for web tasks, `steer` for native app GUI control

## Environment Variables

Copy `.env.sample` to `.env` and fill in:

| Variable | Required | Purpose |
|----------|----------|---------|
| `ANTHROPIC_API_KEY` | For Claude | Claude Code API key |
| `TELEGRAM_BOT_TOKEN` | For Telegram | From @BotFather |
| `TELEGRAM_ALLOWED_USERS` | Recommended | Comma-separated Telegram user IDs |
| `LISTEN_URL` | Optional | Listen server URL (default: http://localhost:7600) |

## System Requirements

- **macOS 12+** (Monterey or later)
- **Python 3.11+**
- **uv** (Python package manager)
- **Google Chrome** (for Playwright browser automation)
- **Tesseract OCR** (for text recognition)
- **tmux** (for terminal session management)
- **just** (task runner)

## Auto-Start on Boot

Uses launchd to run agents at user login:

```bash
# Install launch agents
./install-launchd.sh

# Check status
launchctl list | grep com.agent

# Manual control
launchctl start com.agent.listen
launchctl stop com.agent.listen
```
