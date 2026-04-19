"""
Structured per-account logging.
Each account gets its own log file; a combined log is also written.
"""

import logging
import os
from datetime import datetime
from pathlib import Path


def get_logger(account_id: str) -> logging.Logger:
    """Return a logger for the given account, writing to logs/<account_id>.log"""
    from core.paths import LOGS_DIR
    logs_dir = LOGS_DIR
    logs_dir.mkdir(parents=True, exist_ok=True)

    logger = logging.getLogger(account_id)
    if logger.handlers:
        return logger  # Already configured

    logger.setLevel(logging.DEBUG)
    fmt = logging.Formatter(
        fmt="%(asctime)s [%(name)s] %(levelname)s  %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    # Per-account file handler
    account_log = logs_dir / f"{account_id}.log"
    fh = logging.FileHandler(account_log, encoding="utf-8")
    fh.setLevel(logging.DEBUG)
    fh.setFormatter(fmt)
    logger.addHandler(fh)

    # Combined log file
    combined_log = logs_dir / "combined.log"
    ch = logging.FileHandler(combined_log, encoding="utf-8")
    ch.setLevel(logging.INFO)
    ch.setFormatter(fmt)
    logger.addHandler(ch)

    # Console output
    sh = logging.StreamHandler()
    sh.setLevel(logging.INFO)
    sh.setFormatter(fmt)
    logger.addHandler(sh)

    return logger
