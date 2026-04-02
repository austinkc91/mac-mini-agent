set dotenv-load := true

# Default listen URL
listen_url := env("AGENT_SANDBOX_URL", "http://localhost:7600")

# Start the job server
listen:
    cd apps/listen && uv run python main.py

# Start Telegram bot
telegram:
    cd apps/telegram && uv run python main.py

# Submit a job
send prompt:
    cd apps/direct && uv run python main.py start "{{listen_url}}" "{{prompt}}"

# Submit a job from file
sendf file:
    cd apps/direct && uv run python main.py start "{{listen_url}}" "$(cat {{file}})"

# Check job status
job id:
    cd apps/direct && uv run python main.py get "{{listen_url}}" "{{id}}"

# List all jobs
jobs:
    cd apps/direct && uv run python main.py list "{{listen_url}}"

# Show latest N jobs
latest n="1":
    cd apps/direct && uv run python main.py latest "{{listen_url}}" "{{n}}"

# Stop a running job
stop id:
    cd apps/direct && uv run python main.py stop "{{listen_url}}" "{{id}}"

# Archive all jobs
clear:
    cd apps/direct && uv run python main.py clear "{{listen_url}}"

# Soft reset
reset:
    cd apps/direct && uv run python main.py reset "{{listen_url}}" soft

# Hard reset (reboot)
hard-reset:
    cd apps/direct && uv run python main.py reset "{{listen_url}}" hard

# Take screenshot
screenshot:
    cd apps/steer && uv run python main.py see --json

# Run OCR
ocr:
    cd apps/steer && uv run python main.py ocr --store --json

# Run installer
install:
    chmod +x install.sh && ./install.sh

# Quick install verification
install-check:
    @echo "Checking tools..."
    @which python3 > /dev/null 2>&1 && echo "  python3: OK" || echo "  python3: MISSING"
    @which uv > /dev/null 2>&1 && echo "  uv: OK" || echo "  uv: MISSING"
    @which tesseract > /dev/null 2>&1 && echo "  tesseract: OK" || echo "  tesseract: MISSING"
    @which tmux > /dev/null 2>&1 && echo "  tmux: OK" || echo "  tmux: MISSING"
    @which just > /dev/null 2>&1 && echo "  just: OK" || echo "  just: MISSING"
    @test -d "/Applications/Google Chrome.app" && echo "  chrome: OK" || echo "  chrome: MISSING"

# Install launchd agents
install-launchd:
    chmod +x install-launchd.sh && ./install-launchd.sh

# Service management via launchctl
start-services:
    launchctl start com.agent.listen
    launchctl start com.agent.telegram

stop-services:
    launchctl stop com.agent.listen
    launchctl stop com.agent.telegram

service-status:
    @launchctl list | grep com.agent || echo "No agent services found"
