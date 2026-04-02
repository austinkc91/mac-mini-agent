#!/bin/bash
# Install launchd agents for auto-start on login
# Run: chmod +x install-launchd.sh && ./install-launchd.sh

set -e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
LAUNCH_AGENTS_DIR="$HOME/Library/LaunchAgents"
UV_PATH="$(which uv)"
PYTHON_PATH="$(which python3)"

mkdir -p "$LAUNCH_AGENTS_DIR"

echo "=== Installing Launch Agents ==="
echo "  Project: $SCRIPT_DIR"
echo "  uv: $UV_PATH"
echo ""

# Load .env file for TELEGRAM_BOT_TOKEN check
if [[ -f "$SCRIPT_DIR/.env" ]]; then
    source "$SCRIPT_DIR/.env"
fi

# --- Listen Server ---
LISTEN_PLIST="$LAUNCH_AGENTS_DIR/com.agent.listen.plist"
cat > "$LISTEN_PLIST" << EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>com.agent.listen</string>
    <key>ProgramArguments</key>
    <array>
        <string>${UV_PATH}</string>
        <string>run</string>
        <string>python</string>
        <string>main.py</string>
    </array>
    <key>WorkingDirectory</key>
    <string>${SCRIPT_DIR}/apps/listen</string>
    <key>RunAtLoad</key>
    <true/>
    <key>KeepAlive</key>
    <true/>
    <key>StandardOutPath</key>
    <string>${SCRIPT_DIR}/logs/listen-stdout.log</string>
    <key>StandardErrorPath</key>
    <string>${SCRIPT_DIR}/logs/listen-stderr.log</string>
    <key>EnvironmentVariables</key>
    <dict>
        <key>PATH</key>
        <string>/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:$HOME/.local/bin</string>
    </dict>
</dict>
</plist>
EOF

echo "  ✓ Created $LISTEN_PLIST"
launchctl unload "$LISTEN_PLIST" 2>/dev/null || true
launchctl load "$LISTEN_PLIST"
echo "  ✓ Loaded com.agent.listen"

# --- Telegram Bot (only if token is set) ---
if [[ -n "$TELEGRAM_BOT_TOKEN" ]]; then
    TELEGRAM_PLIST="$LAUNCH_AGENTS_DIR/com.agent.telegram.plist"
    cat > "$TELEGRAM_PLIST" << EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>com.agent.telegram</string>
    <key>ProgramArguments</key>
    <array>
        <string>${UV_PATH}</string>
        <string>run</string>
        <string>python</string>
        <string>main.py</string>
    </array>
    <key>WorkingDirectory</key>
    <string>${SCRIPT_DIR}/apps/telegram</string>
    <key>RunAtLoad</key>
    <true/>
    <key>KeepAlive</key>
    <true/>
    <key>StandardOutPath</key>
    <string>${SCRIPT_DIR}/logs/telegram-stdout.log</string>
    <key>StandardErrorPath</key>
    <string>${SCRIPT_DIR}/logs/telegram-stderr.log</string>
    <key>EnvironmentVariables</key>
    <dict>
        <key>PATH</key>
        <string>/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:$HOME/.local/bin</string>
        <key>TELEGRAM_BOT_TOKEN</key>
        <string>${TELEGRAM_BOT_TOKEN}</string>
    </dict>
</dict>
</plist>
EOF

    echo "  ✓ Created $TELEGRAM_PLIST"
    launchctl unload "$TELEGRAM_PLIST" 2>/dev/null || true
    launchctl load "$TELEGRAM_PLIST"
    echo "  ✓ Loaded com.agent.telegram"
else
    echo "  ! Skipping telegram (no TELEGRAM_BOT_TOKEN in .env)"
fi

# Create logs directory
mkdir -p "$SCRIPT_DIR/logs"

echo ""
echo "=== Done ==="
echo ""
echo "Check status:"
echo "  launchctl list | grep com.agent"
echo ""
echo "Manual control:"
echo "  launchctl start com.agent.listen"
echo "  launchctl stop com.agent.listen"
echo "  launchctl kickstart -k gui/\$(id -u)/com.agent.listen  # force restart"
echo ""
