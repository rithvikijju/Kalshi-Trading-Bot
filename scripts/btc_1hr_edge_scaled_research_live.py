#!/usr/bin/env python3
"""Live runner for edge-gated $100 scaling research.

This keeps the deployed research signal unchanged. Sizing is current
risk-adjusted sizing unless a signal has at least 16c net edge on the NO side,
where the tested scale-up config can use up to five contracts.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

os.environ.setdefault("BTC_1HR_EXECUTOR_NAME", "btc_1hr_edge_scaled_research_live")
os.environ.setdefault("BTC_1HR_SIGNAL_STRATEGY", "research")
os.environ.setdefault("BTC_1HR_SIZING_POLICY", "risk_adjusted")
os.environ.setdefault("BTC_1HR_MAX_CONTRACTS_PER_TRADE", "5")
os.environ.setdefault("BTC_1HR_RISK_KELLY_FRACTION", "0.25")
os.environ.setdefault("BTC_1HR_RISK_EDGE_CONFIDENCE", "0.50")
os.environ.setdefault("BTC_1HR_RISK_MEDIUM_ENTRY_CAP", "0.75")
os.environ.setdefault("BTC_1HR_RISK_HIGH_ENTRY_CAP", "0.85")
os.environ.setdefault("BTC_1HR_RISK_NO_SIDE_CONTRACT_CAP", "3")
os.environ.setdefault("BTC_1HR_RISK_SCALE_MIN_EDGE_CENTS", "16.0")
os.environ.setdefault("BTC_1HR_RISK_SCALE_SIDE", "no")
os.environ.setdefault("BTC_1HR_RISK_BASE_MAX_CONTRACTS", "3")
os.environ.setdefault("BTC_1HR_RISK_BASE_MEDIUM_ENTRY_CAP", "0.55")
os.environ.setdefault("BTC_1HR_RISK_BASE_HIGH_ENTRY_CAP", "0.65")
os.environ.setdefault("BTC_1HR_RISK_BASE_NO_SIDE_CONTRACT_CAP", "3")

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
