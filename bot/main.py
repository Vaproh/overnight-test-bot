#!/usr/bin/env python3
"""Production Instagram Account Visibility Monitor Bot."""

import argparse
import logging
import signal
import sys
import threading

from .config import Config
from .database import Database
from .logger import setup_logging
from .monitor import Monitor
from .telegram import TelegramBot


def main():
    parser = argparse.ArgumentParser(description="Instagram Monitor Bot")
    parser.add_argument("--config", default="config.yaml", help="Path to config file")
    args = parser.parse_args()

    config = Config.from_yaml(args.config)
    setup_logging(config.log_level, config.logs_dir)
    logger = logging.getLogger("monitor.main")

    db = Database(config.database_path)
    db.seed_admins(config.admins)

    db.cleanup_old_data(
        days=7,
        raw_dir=config.raw_responses_dir,
        screenshots_dir=config.screenshots_dir,
    )

    monitor = Monitor(config, db)

    telegram_bot = None
    if config.telegram_token:
        telegram_bot = TelegramBot(config, db, monitor)
        telegram_bot.build()
        monitor.notify_fn = telegram_bot.notify
        monitor.notify_photo_fn = telegram_bot.notify_photo
        monitor.notify_to_chat_ids = telegram_bot.notify_to_chat_ids
        monitor.notify_photo_to_chat_ids = telegram_bot.notify_photo_to_chat_ids

    def shutdown(signum, frame):
        logger.info(f"Received signal {signum}, shutting down...")
        monitor.stop()
        if telegram_bot:
            telegram_bot.shutdown_notify("🔴 <b>Bot stopped</b>")
        if telegram_bot and telegram_bot.app:
            telegram_bot.app.stop_running()

    signal.signal(signal.SIGINT, shutdown)
    signal.signal(signal.SIGTERM, shutdown)
    signal.signal(signal.SIGHUP, shutdown)

    try:
        if telegram_bot and telegram_bot.app:
            async def on_post_init(application):
                await telegram_bot.post_init(application)
                await telegram_bot._send_notification("🟢 <b>Bot started</b>")

            telegram_bot.app.post_init = on_post_init
            monitor_thread = threading.Thread(target=monitor.start, daemon=True)
            monitor_thread.start()
            telegram_bot.app.run_polling()
        else:
            monitor.start()
    except Exception as e:
        logger.critical(f"Fatal error: {e}", exc_info=True)
        sys.exit(1)
    finally:
        monitor.stop()
        db.close()


if __name__ == "__main__":
    main()
