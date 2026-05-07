#!/usr/bin/env python3
"""Paper-shadow runner for the js_guarded BTC 1-hour strategy.

This intentionally reuses btc_1hr_research_live.py's websocket/orderbook,
capture, event-lock, and bankroll-safety logic. Defaults are paper-only with a
$1000 mock bankroll and separate DB paths.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

os.environ.setdefault("BTC_1HR_EXECUTOR_NAME", "btc_1hr_js_guarded_shadow")
os.environ.setdefault("BTC_1HR_SIGNAL_STRATEGY", "js_guarded")
os.environ.setdefault("BTC_1HR_SIZING_POLICY", "risk_adjusted")
os.environ.setdefault("BTC_1HR_RISK_KELLY_FRACTION", "0.50")
os.environ.setdefault("BTC_1HR_RISK_MEDIUM_ENTRY_CAP", "1.01")
os.environ.setdefault("BTC_1HR_RISK_HIGH_ENTRY_CAP", "1.01")
os.environ.setdefault("BTC_1HR_RISK_NO_SIDE_CONTRACT_CAP", "10000")
os.environ.setdefault("BTC_1HR_MAX_CONTRACTS_PER_TRADE", "10000")

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from config.btc_1hr_config import CFG
from scripts import btc_1hr_research_live as live


def has_arg(name: str, argv: list[str]) -> bool:
    return any(arg == name or arg.startswith(f"{name}=") for arg in argv)


def main() -> None:
    user_args = sys.argv[1:]
    defaults: list[str] = []
    if not any(flag in user_args for flag in ("--paper", "--dry-run")):
        defaults.append("--paper")
    if not has_arg("--signal-strategy", user_args):
        defaults.extend(["--signal-strategy", "js_guarded"])
    if not has_arg("--sizing-policy", user_args):
        defaults.extend(["--sizing-policy", "risk_adjusted"])
    if not has_arg("--shadow-bankroll", user_args):
        defaults.extend(["--shadow-bankroll", "1000"])
    if not has_arg("--paper-report-sec", user_args):
        defaults.extend(["--paper-report-sec", "60"])
    if not has_arg("--db-path", user_args):
        defaults.extend(["--db-path", str(Path(CFG["db_dir"]).expanduser() / "js_guarded_shadow_trades.db")])
    if not has_arg("--capture-db-path", user_args):
        defaults.extend(["--capture-db-path", str(Path(CFG["db_dir"]).expanduser() / "js_guarded_shadow_capture.duckdb")])

    live.set_signal_strategy("js_guarded")
    live.set_sizing_policy("risk_adjusted")
    sys.argv = [sys.argv[0], *defaults, *user_args]
    live.main()


if __name__ == "__main__":
    main()
