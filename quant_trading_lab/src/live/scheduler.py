"""Minimal in-process scheduler.

For real production scheduling use cron, launchd, or APScheduler. This
module is the inside-the-notebook variant: one tick = pull latest data,
re-run signal, rebalance to targets, snapshot.
"""
from __future__ import annotations
import time
import traceback
from dataclasses import dataclass
from typing import Callable
import pandas as pd

from ..utils.logging import get_logger
log = get_logger("scheduler")


@dataclass
class Tick:
    ts: pd.Timestamp
    ok: bool
    detail: str


def loop_once(fn: Callable[[], dict]) -> Tick:
    try:
        out = fn() or {}
        return Tick(ts=pd.Timestamp.utcnow(), ok=True, detail=str(out)[:200])
    except Exception as e:                          # never crash the scheduler
        log.error("tick error: %s\n%s", e, traceback.format_exc())
        return Tick(ts=pd.Timestamp.utcnow(), ok=False, detail=str(e))


def loop_until(fn: Callable[[], dict], every_seconds: int, until: pd.Timestamp,
                on_tick: Callable[[Tick], None] | None = None) -> list[Tick]:
    ticks = []
    while pd.Timestamp.utcnow() < until:
        t = loop_once(fn)
        ticks.append(t)
        if on_tick:
            on_tick(t)
        time.sleep(every_seconds)
    return ticks
