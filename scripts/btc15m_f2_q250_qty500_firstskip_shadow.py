#!/usr/bin/env python3
"""Paper BTC15M q250 first-signal qty>=500 shadow.

This forward-tests the materialized research candidate exactly:

* run the frozen H02 q250-style signal
* if the first qualifying signal in an event has top visible qty < 500, skip
  the entire event instead of waiting for a later higher-qty tick
* paper mode only; never submits real orders
"""

from __future__ import annotations

import os
import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))


def main() -> None:
    data_dir = PROJECT_ROOT / ".codex_work" / "btc15m_f2_q250_qty500_firstskip_shadow"
    data_dir.mkdir(parents=True, exist_ok=True)
    os.environ["BTC15M_SIGNAL_STRATEGY"] = "h02"
    os.environ["BTC15M_H02_TTL_LO"] = "10.0"
    os.environ["BTC15M_H02_TTL_HI"] = "12.0"
    os.environ["BTC15M_H02_SPREAD_MAX_CENTS"] = "2.0"
    os.environ["BTC15M_H02_EDGE_THRESHOLD_CENTS"] = "12.0"
    os.environ["BTC15M_H02_ENTRY_MIN"] = "0.02"
    os.environ["BTC15M_H02_ENTRY_MAX"] = "0.50"
    os.environ["BTC15M_H02_MIN_SIDE_PROB"] = "0.60"
    os.environ["BTC15M_H02_MIN_VISIBLE_QTY"] = "250"
    os.environ["BTC15M_H02_FIRST_SIGNAL_MIN_VISIBLE_QTY"] = "500"
    os.environ["BTC15M_H02_ALLOWED_SIDE"] = ""
    os.environ["BTC15M_H02_MAX_CONTRACTS"] = "1"
    os.environ["BTC15M_MAX_REPRICE_WORSE_CENTS"] = "2.0"
    os.environ["BTC15M_ORDER_CHASE_COOLDOWN_SEC"] = "0.0"
    os.environ["BTC15M_PORTFOLIO_CACHE_TTL_SEC"] = "30.0"
    os.environ["BTC15M_PORTFOLIO_WARM_REFRESH_SEC"] = "15.0"

    from scripts import btc15m_lowdd_live

    sys.argv = [
        "btc15m_lowdd_live.py",
        "--mode",
        "paper",
        "--strategy",
        "h02",
        "--capture-db-path",
        str(data_dir / "btc15m_f2_q250_qty500_firstskip_shadow_capture.duckdb"),
        "--trade-db-path",
        str(data_dir / "btc15m_f2_q250_qty500_firstskip_shadow_trades.db"),
        "--shadow-bankroll",
        "100",
        "--refresh-sec",
        "10",
        "--health-sec",
        "30",
    ]
    btc15m_lowdd_live.main()


if __name__ == "__main__":
    main()
