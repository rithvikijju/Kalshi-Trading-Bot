"""Structured logger setup. Uses rich for pretty console output."""
from __future__ import annotations
import logging, sys
from pathlib import Path
from rich.logging import RichHandler


def setup_logger(name: str = "prediction_arb", level: str = "INFO",
                 logfile: str | None = "prediction_arb.log") -> logging.Logger:
    logger = logging.getLogger(name)
    if logger.handlers:  # avoid duplicate handlers on re-import
        return logger
    logger.setLevel(level)

    # Console: rich
    console_handler = RichHandler(rich_tracebacks=True, show_time=False, show_path=False)
    console_handler.setLevel(level)
    logger.addHandler(console_handler)

    # File: rotating
    if logfile:
        Path(logfile).parent.mkdir(parents=True, exist_ok=True)
        fh = logging.FileHandler(logfile)
        fh.setFormatter(logging.Formatter('%(asctime)s %(name)s %(levelname)s %(message)s'))
        fh.setLevel(level)
        logger.addHandler(fh)

    return logger
