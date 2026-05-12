#!/usr/bin/env python3
"""Live runner for the late-only BTC 1-hour research strategy.

This uses the same websocket/orderbook execution engine as
btc_1hr_research_live.py and changes only the deployment gates:

* current research signal
* websocket market data by default
* 5-20 minutes to close
* max three contracts per trade
"""

from __future__ import annotations

import os
import sys
from pathlib import Path


os.environ["BTC_1HR_EXECUTOR_NAME"] = "btc_1hr_late_only_research_live"
os.environ["BTC_1HR_SIGNAL_STRATEGY"] = "research"
os.environ["BTC_1HR_SIZING_POLICY"] = "risk_adjusted"
os.environ["BTC_1HR_MIN_TTL_MIN"] = "5"
os.environ["BTC_1HR_MAX_TTL_MIN"] = "20"
os.environ["BTC_1HR_MAX_CONTRACTS_PER_TRADE"] = "3"
os.environ["BTC_1HR_RISK_BASE_MAX_CONTRACTS"] = "3"
os.environ["BTC_1HR_RISK_NO_SIDE_CONTRACT_CAP"] = "3"
os.environ["BTC_1HR_RISK_BASE_NO_SIDE_CONTRACT_CAP"] = "3"
os.environ["BTC_1HR_RISK_SCALE_MIN_EDGE_CENTS"] = ""
os.environ["BTC_1HR_RISK_SCALE_SIDE"] = ""

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
