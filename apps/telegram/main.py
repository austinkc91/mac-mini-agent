"""Telegram bot entry point for Windows Agent remote control.

Usage:
    TELEGRAM_BOT_TOKEN=<your-token> uv run python main.py

Optional env vars:
    LISTEN_URL          - Listen server URL (default: http://localhost:7600)
    TELEGRAM_ALLOWED_USERS - Comma-separated list of authorized Telegram user IDs
                             (REQUIRED: all users are rejected if not set)
"""

import asyncio
import logging
import os
import sys
from pathlib import Path

from telegram import BotCommand
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    MessageHandler,
    filters,
)

from bot import (
    handle_start,
    handle_job,
    handle_jobs,
    handle_status,
    handle_stop,
    handle_screenshot,
    handle_steer,
    handle_drive,
    handle_shell,
    handle_confirm,
    handle_cancel,
    handle_cron,
    handle_reset,
    handle_restart,
    handle_photo,
    handle_voice,
    handle_audio,
    handle_document,
    handle_text,
    recover_undelivered,
    periodic_delivery_check,
    start_webhook_server,
    JOBS_DIR,
)

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)


def main():
    token = os.environ.get("TELEGRAM_BOT_TOKEN")
    if not token:
        print("Error: TELEGRAM_BOT_TOKEN environment variable is required.")
        print()
        print("To get a bot token:")
        print("  1. Open Telegram and message @BotFather")
        print("  2. Send /newbot and follow the prompts")
        print("  3. Copy the token and set it:")
        print("     export TELEGRAM_BOT_TOKEN='your-token-here'")
        print()
        print("Optional security:")
        print("  export TELEGRAM_ALLOWED_USERS='123456789,987654321'")
        print("  (Your Telegram user ID — send /start to the bot to see it)")
        sys.exit(1)

    CHAT_ID_FILE = JOBS_DIR / ".chat_id"

    async def post_init(application):
        """Run recovery and register bot command menu."""
        # Register slash command menu
        commands = [
            BotCommand("job", "Submit a job with a prompt"),
            BotCommand("jobs", "List recent jobs"),
            BotCommand("status", "Check job status (latest if no ID)"),
            BotCommand("stop", "Stop a running job"),
            BotCommand("screenshot", "Take a screenshot of the desktop"),
            BotCommand("steer", "Run a GUI automation command"),
            BotCommand("drive", "Run a terminal command"),
            BotCommand("shell", "Run a raw shell command"),
            BotCommand("cron", "Manage scheduled cron jobs"),
            BotCommand("reset", "Reset system (stop jobs, kill processes)"),
            BotCommand("restart", "Restart agent services (listen + telegram)"),
            BotCommand("help", "Show help and available commands"),
        ]
        await application.bot.set_my_commands(commands)
        logger.info("Registered bot command menu")

        # Recover undelivered jobs
        if CHAT_ID_FILE.exists():
            chat_id = int(CHAT_ID_FILE.read_text().strip())
            logger.info(f"Recovering undelivered jobs for chat {chat_id}...")
            await recover_undelivered(application.bot, chat_id)
        else:
            logger.info("No saved chat ID — recovery will run after first message")

        # Start periodic delivery check (catches cron-triggered jobs)
        asyncio.create_task(periodic_delivery_check(application.bot))

        # Start webhook server for instant job completion notifications
        asyncio.create_task(start_webhook_server(application.bot))

    app = ApplicationBuilder().token(token).post_init(post_init).build()

    # Command handlers
    app.add_handler(CommandHandler("start", handle_start))
    app.add_handler(CommandHandler("help", handle_start))
    app.add_handler(CommandHandler("job", handle_job))
    app.add_handler(CommandHandler("jobs", handle_jobs))
    app.add_handler(CommandHandler("status", handle_status))
    app.add_handler(CommandHandler("stop", handle_stop))
    app.add_handler(CommandHandler("screenshot", handle_screenshot))
    app.add_handler(CommandHandler("steer", handle_steer))
    app.add_handler(CommandHandler("drive", handle_drive))
    app.add_handler(CommandHandler("shell", handle_shell))
    app.add_handler(CommandHandler("confirm", handle_confirm))
    app.add_handler(CommandHandler("cancel", handle_cancel))
    app.add_handler(CommandHandler("cron", handle_cron))
    app.add_handler(CommandHandler("reset", handle_reset))
    app.add_handler(CommandHandler("restart", handle_restart))

    # Media handlers
    app.add_handler(MessageHandler(filters.PHOTO, handle_photo))
    app.add_handler(MessageHandler(filters.VOICE, handle_voice))
    app.add_handler(MessageHandler(filters.AUDIO, handle_audio))
    app.add_handler(MessageHandler(filters.Document.ALL, handle_document))

    # Plain text → treat as job prompt
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))

    logger.info("Telegram bot starting...")
    logger.info(f"Listen URL: {os.environ.get('LISTEN_URL', 'http://localhost:7600')}")
    if os.environ.get("TELEGRAM_ALLOWED_USERS"):
        logger.info(f"Authorized users: {os.environ['TELEGRAM_ALLOWED_USERS']}")
    else:
        logger.warning("No TELEGRAM_ALLOWED_USERS set — all users can control this bot!")

    app.run_polling()


if __name__ == "__main__":
    main()
