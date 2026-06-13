#!/usr/bin/env python3
"""Production Instagram Account Visibility Monitor Bot."""

import argparse
import asyncio
import signal
import sys
import threading

from .checker import check_with_curl_cffi, init_instagram_cookies
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

    if config.instagram_auth.enabled:
        init_instagram_cookies(config)

    db = Database(config.database_path)
    monitor = Monitor(config, db)

    telegram_bot = None
    if config.telegram_token:
        telegram_bot = TelegramBot(config, db, monitor)
        telegram_bot.build()
        monitor.notify_fn = telegram_bot.notify
        monitor.notify_photo_fn = telegram_bot.notify_photo

    def shutdown(signum, frame):
        monitor.stop()

    signal.signal(signal.SIGINT, shutdown)
    signal.signal(signal.SIGTERM, shutdown)

    if telegram_bot and telegram_bot.app:
        telegram_bot.app.post_init = telegram_bot.post_init
        monitor_thread = threading.Thread(target=monitor.start, daemon=True)
        monitor_thread.start()
        telegram_bot.app.run_polling()
    else:
        monitor.start()

    db.close()


if __name__ == "__main__":
    main()
