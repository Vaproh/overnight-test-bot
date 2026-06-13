#!/usr/bin/env python3
"""Production Instagram Account Visibility Monitor Bot."""

import argparse
import signal
import sys
import threading

from telegram.ext import Application

from .checker import check_with_curl_cffi
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

    db = Database(config.database_path)
    monitor = Monitor(config, db)

    telegram_bot = None
    if config.telegram_token:
        telegram_bot = TelegramBot(config, db, monitor)
        telegram_bot.build()
        monitor.notify_fn = telegram_bot.notify

    def shutdown(signum, frame):
        monitor.stop()
        if telegram_bot and telegram_bot.app:
            telegram_bot.app.stop_running()

    signal.signal(signal.SIGINT, shutdown)
    signal.signal(signal.SIGTERM, shutdown)

    if telegram_bot and telegram_bot.app:
        app = telegram_bot.app
        app.run_polling(
            should_stop=lambda: not monitor.running,
            ready_callback=lambda: print("Bot is ready"),
        )
    else:
        monitor.start()

    db.close()


if __name__ == "__main__":
    main()
