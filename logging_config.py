"""
logging_config.py — centralised logging setup for enePath WebAdmin.

Controls:
    LOG_LEVEL=DEBUG   → full wire-protocol traces, request timing, session events
    LOG_LEVEL=INFO    → login/logout events, errors, startup messages  (default)
    LOG_LEVEL=WARNING → only warnings and errors
    LOG_LEVEL=ERROR   → silent except for failures

    LOG_FILE=/var/log/webadmin/app.log  → write to file as well as stdout
                                          (leave unset to stdout only)

Set DEV_MODE=true to get coloured console output and skip syslog.
"""

import logging
import logging.handlers
import os
import sys

# Public logger — import this everywhere:  from logging_config import logger
logger = logging.getLogger("webadmin")


def setup_logging() -> None:
    """Call once at startup (from main.py lifespan or module import)."""
    raw_level = os.environ.get("LOG_LEVEL", "INFO").upper()
    level = getattr(logging, raw_level, logging.INFO)

    dev_mode = os.environ.get("DEV_MODE", "").lower() in ("1", "true", "yes")
    log_file = os.environ.get("LOG_FILE", "")

    root = logging.getLogger()
    root.setLevel(level)

    # Remove any handlers that uvicorn or other libs already added
    root.handlers.clear()

    fmt_verbose = "%(asctime)s %(levelname)-8s %(name)s  %(message)s"
    fmt_simple  = "%(levelname)-8s %(message)s"

    # ── Console handler ───────────────────────────────────────────────────────
    console = logging.StreamHandler(sys.stdout)
    console.setLevel(level)
    if dev_mode:
        console.setFormatter(_ColourFormatter(fmt_verbose))
    else:
        console.setFormatter(logging.Formatter(fmt_verbose))
    root.addHandler(console)

    # ── File handler (optional) ───────────────────────────────────────────────
    if log_file:
        os.makedirs(os.path.dirname(log_file), exist_ok=True)
        fh = logging.handlers.RotatingFileHandler(
            log_file, maxBytes=10 * 1024 * 1024, backupCount=10
        )
        fh.setLevel(level)
        fh.setFormatter(logging.Formatter(fmt_verbose))
        root.addHandler(fh)

    # ── Syslog handler (production Linux only) ────────────────────────────────
    if not dev_mode and os.path.exists("/dev/log"):
        syslog = logging.handlers.SysLogHandler(address="/dev/log")
        syslog.setFormatter(logging.Formatter("AMP Web Admin: %(message)s"))
        syslog.setLevel(logging.INFO)  # don't flood syslog with DEBUG
        root.addHandler(syslog)

    # Quiet noisy third-party loggers unless we're in DEBUG
    if level > logging.DEBUG:
        logging.getLogger("uvicorn.access").setLevel(logging.WARNING)
        logging.getLogger("asyncio").setLevel(logging.WARNING)

    logger.info(
        "Logging initialised — level=%s file=%s syslog=%s",
        raw_level,
        log_file or "stdout only",
        "yes" if not dev_mode else "no (DEV_MODE)",
    )


# ── Optional: coloured output for dev machines ────────────────────────────────

_COLOURS = {
    "DEBUG":    "\033[36m",   # cyan
    "INFO":     "\033[32m",   # green
    "WARNING":  "\033[33m",   # yellow
    "ERROR":    "\033[31m",   # red
    "CRITICAL": "\033[35m",   # magenta
}
_RESET = "\033[0m"


class _ColourFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        colour = _COLOURS.get(record.levelname, "")
        record.levelname = f"{colour}{record.levelname}{_RESET}"
        return super().format(record)
