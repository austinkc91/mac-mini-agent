#!/bin/bash
# Mac Mini Agent - Automated Installer
# Run: chmod +x install.sh && ./install.sh

set -e

echo "=== Mac Mini Agent Installer ==="
echo ""

# Colors
GREEN='\033[0;32m'
RED='\033[0;31m'
YELLOW='\033[1;33m'
NC='\033[0m'

ok() { echo -e "  ${GREEN}✓${NC} $1"; }
fail() { echo -e "  ${RED}✗${NC} $1"; }
warn() { echo -e "  ${YELLOW}!${NC} $1"; }

# Check macOS
if [[ "$(uname)" != "Darwin" ]]; then
    fail "This installer is for macOS only."
    exit 1
fi

# Check Homebrew
echo "Checking Homebrew..."
if ! command -v brew &>/dev/null; then
    warn "Homebrew not found. Installing..."
    /bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"
    # Add brew to PATH for Apple Silicon
    if [[ -f /opt/homebrew/bin/brew ]]; then
        eval "$(/opt/homebrew/bin/brew shellenv)"
    fi
fi
ok "Homebrew installed"

# Install system dependencies
echo ""
echo "Installing system dependencies..."

# Python 3.12
if ! command -v python3.12 &>/dev/null && ! python3 --version 2>/dev/null | grep -q "3.1[1-9]"; then
    brew install python@3.12
fi
ok "Python 3.11+ available"

# Tesseract OCR
if ! command -v tesseract &>/dev/null; then
    brew install tesseract
fi
ok "Tesseract OCR installed"

# tmux
if ! command -v tmux &>/dev/null; then
    brew install tmux
fi
ok "tmux installed"

# just (task runner)
if ! command -v just &>/dev/null; then
    brew install just
fi
ok "just installed"

# Google Chrome
if [[ ! -d "/Applications/Google Chrome.app" ]]; then
    warn "Google Chrome not found. Installing..."
    brew install --cask google-chrome
fi
ok "Google Chrome installed"

# uv (Python package manager)
echo ""
echo "Installing uv..."
if ! command -v uv &>/dev/null; then
    curl -LsSf https://astral.sh/uv/install.sh | sh
    export PATH="$HOME/.local/bin:$PATH"
fi
ok "uv installed"

# Sync Python dependencies
echo ""
echo "Syncing Python dependencies..."
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"

for app in steer drive listen browser workflow direct telegram gmail; do
    APP_DIR="$SCRIPT_DIR/apps/$app"
    if [[ -f "$APP_DIR/pyproject.toml" ]]; then
        echo "  Syncing $app..."
        (cd "$APP_DIR" && uv sync 2>&1 | tail -1)
        ok "$app"
    fi
done

# Install Playwright browsers
echo ""
echo "Installing Playwright browsers..."
(cd "$SCRIPT_DIR/apps/browser" && uv run playwright install chromium 2>&1 | tail -1)
ok "Playwright chromium installed"

# Create .env from sample if needed
if [[ ! -f "$SCRIPT_DIR/.env" ]]; then
    if [[ -f "$SCRIPT_DIR/.env.sample" ]]; then
        cp "$SCRIPT_DIR/.env.sample" "$SCRIPT_DIR/.env"
        warn "Created .env from .env.sample — edit it with your API keys"
    fi
fi

echo ""
echo "=== Installation Complete ==="
echo ""
echo "Quick start:"
echo "  cd $SCRIPT_DIR"
echo "  just listen          # Start the job server"
echo "  just telegram        # Start the Telegram bot"
echo "  just send 'hello'    # Submit a test job"
echo ""
echo "For auto-start on login:"
echo "  chmod +x install-launchd.sh && ./install-launchd.sh"
echo ""
