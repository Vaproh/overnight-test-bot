"""Logging configuration."""

import os
import sys
from logging import StreamHandler, FileHandler, Formatter, getLogger, INFO, DEBUG


def setup_logging(log_level: str = "INFO", logs_dir: str = "./data/logs"):
    os.makedirs(logs_dir, exist_ok=True)

    root = getLogger()
    root.setLevel(getattr(sys.modules["logging"], log_level.upper(), INFO))
    root.handlers.clear()

    console = StreamHandler(sys.stdout)
    console.setLevel(INFO)
    console.setFormatter(
        Formatter("[%(asctime)s] %(levelname)s %(message)s", datefmt="%H:%M:%S")
    )
    root.addHandler(console)

    file_handler = FileHandler(os.path.join(logs_dir, "monitor.log"), encoding="utf-8")
    file_handler.setLevel(DEBUG)
    file_handler.setFormatter(
        Formatter("[%(asctime)s] %(levelname)s [%(name)s] %(message)s")
    )
    root.addHandler(file_handler)
