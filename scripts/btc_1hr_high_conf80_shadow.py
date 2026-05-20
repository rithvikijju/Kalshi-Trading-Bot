#!/usr/bin/env python3
"""Paper-shadow runner for the frozen BTC1H high_conf_80 candidate.

This intentionally defaults to paper mode.  It uses the same websocket engine
as the live 1H bot, but records simulated one-contract decisions for the
frozen high-confidence candidate instead of placing real orders.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path


STRATEGY = "high_conf_80"
LOCKED_ARGS = {
    "--signal-strategy",
    "--sizing-policy",
    "--db-path",
    "--capture-db-path",
}

os.environ["BTC_1HR_EXECUTOR_NAME"] = "btc_1hr_high_conf80_shadow"
os.environ["BTC_1HR_SIGNAL_STRATEGY"] = STRATEGY
os.environ["BTC_1HR_SIZING_POLICY"] = "flat_max"
os.environ["BTC_1HR_MIN_TTL_MIN"] = "5"
os.environ["BTC_1HR_MAX_TTL_MIN"] = "20"
os.environ["BTC_1HR_MAX_CONTRACTS_PER_TRADE"] = "1"

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from scripts import btc_1hr_research_live as live


def has_arg(name: str, argv: list[str]) -> bool:
    return any(arg == name or arg.startswith(f"{name}=") for arg in argv)


def build_forward_args(user_args: list[str]) -> list[str]:
    locked = sorted(name for name in LOCKED_ARGS if has_arg(name, user_args))
    if locked:
        raise SystemExit(
            "This shadow wrapper has locked identity/storage settings; remove overrides for "
            + ", ".join(locked)
        )
    defaults: list[str] = []
    if "--paper" not in user_args and "--dry-run" not in user_args:
        defaults.append("--paper")
    defaults.extend(["--signal-strategy", STRATEGY])
    defaults.extend(["--sizing-policy", "flat_max"])
    if not has_arg("--shadow-bankroll", user_args):
        defaults.extend(["--shadow-bankroll", "100"])
    if not has_arg("--paper-report-sec", user_args):
        defaults.extend(["--paper-report-sec", "300"])
    defaults.extend(["--db-path", str(Path.home() / ".btc_kalshi_bot" / "btc_1hr_high_conf80_shadow.db")])
    defaults.extend(
        [
            "--capture-db-path",
            str(Path.home() / ".btc_kalshi_bot" / "btc_1hr_high_conf80_shadow_capture.duckdb"),
        ]
    )
    return [*defaults, *user_args]


def main() -> None:
    user_args = sys.argv[1:]
    live.set_signal_strategy(STRATEGY)
    live.set_sizing_policy("flat_max")
    sys.argv = [sys.argv[0], *build_forward_args(user_args)]
    live.main()


if __name__ == "__main__":
    main()
