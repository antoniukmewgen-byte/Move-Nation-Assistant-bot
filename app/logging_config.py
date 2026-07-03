"""Centralized logging configuration for the bot, API and background jobs.

Configures a console handler for all environments and, when ``LOG_DIR`` exists
(or can be created), a rotating file handler so operational logs survive
process restarts. Call :func:`setup_logging` once, as early as possible in
each entrypoint (currently only ``app/main.py``).
"""

from __future__ import annotations

import logging
import logging.handlers

from app.config import BASE_DIR

LOG_DIR = BASE_DIR / "logs"
LOG_FORMAT = "%(asctime)s | %(levelname)-8s | %(name)s | %(message)s"

# Third-party libraries are noisy at INFO/DEBUG; keep them at WARNING unless
# the user explicitly wants more detail via LOG_LEVEL=DEBUG.
_NOISY_LOGGERS = ("aiogram.event", "httpx", "telethon", "apscheduler")


def setup_logging(level: str = "INFO") -> None:
    """Configure root logging handlers exactly once per process."""
    root = logging.getLogger()
    if getattr(root, "_movenation_configured", False):
        return

    root.setLevel(level.upper())
    formatter = logging.Formatter(LOG_FORMAT)

    console_handler = logging.StreamHandler()
    console_handler.setFormatter(formatter)
    root.addHandler(console_handler)

    try:
        LOG_DIR.mkdir(parents=True, exist_ok=True)
        file_handler = logging.handlers.RotatingFileHandler(
            LOG_DIR / "bot.log", maxBytes=5 * 1024 * 1024, backupCount=5, encoding="utf-8"
        )
        file_handler.setFormatter(formatter)
        root.addHandler(file_handler)
    except OSError:
        logging.getLogger(__name__).warning("Не вдалося створити лог-файл у %s, пишу лише в консоль", LOG_DIR)

    for name in _NOISY_LOGGERS:
        logging.getLogger(name).setLevel(logging.WARNING)

    root._movenation_configured = True  # type: ignore[attr-defined]
