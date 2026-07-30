"""Single shared logger. Console + rotating file under data/reports/runs/."""
from __future__ import annotations
import logging
import sys
from pathlib import Path
from logging.handlers import RotatingFileHandler

from ..config import REPORTS_DIR

_LOG_DIR = REPORTS_DIR / "runs"
_LOG_DIR.mkdir(parents=True, exist_ok=True)
_LOG_FILE = _LOG_DIR / "lab.log"


def get_logger(name: str = "lab") -> logging.Logger:
    log = logging.getLogger(name)
    if log.handlers:
        return log
    log.setLevel(logging.INFO)
    fmt = logging.Formatter("%(asctime)s %(levelname)s %(name)s | %(message)s")

    ch = logging.StreamHandler(sys.stdout)
    ch.setFormatter(fmt)
    log.addHandler(ch)

    fh = RotatingFileHandler(_LOG_FILE, maxBytes=5_000_000, backupCount=5)
    fh.setFormatter(fmt)
    log.addHandler(fh)
    log.propagate = False
    return log
