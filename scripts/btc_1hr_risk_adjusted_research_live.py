#!/usr/bin/env python3
"""Live runner for the BTC 1-hour research strategy with risk-adjusted sizing.

This reuses btc_1hr_research_live.py's websocket orderbook, capture, event
locking, FOK order handling, and portfolio safety logic. The signal remains the
research strategy; only final order size is risk adjusted.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

os.environ.setdefault("BTC_1HR_EXECUTOR_NAME", "btc_1hr_risk_adjusted_research_live")
os.environ.setdefault("BTC_1HR_SIGNAL_STRATEGY", "research")
os.environ.setdefault("BTC_1HR_SIZING_POLICY", "risk_adjusted")

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from scripts import btc_1hr_research_live as live


def has_arg(name: str, argv: list[str]) -> bool:
    return any(arg == name or arg.startswith(f"{name}=") for arg in argv)


def main() -> None:
    user_args = sys.argv[1:]
    defaults: list[str] = []
    if not has_arg("--signal-strategy", user_args):
        defaults.extend(["--signal-strategy", "research"])
    if not has_arg("--sizing-policy", user_args):
        defaults.extend(["--sizing-policy", "risk_adjusted"])

    live.set_signal_strategy("research")
    live.set_sizing_policy("risk_adjusted")
    sys.argv = [sys.argv[0], *defaults, *user_args]
    live.main()


if __name__ == "__main__":
    main()
