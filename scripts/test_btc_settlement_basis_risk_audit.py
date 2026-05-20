#!/usr/bin/env python3
"""Safety checks for BTC settlement-basis risk diagnostics."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

import pandas as pd

from scripts import build_btc_settlement_basis_risk_audit as mod


class SettlementBasisRiskAuditTests(unittest.TestCase):
    def test_shadow_official_family_is_inferred_from_ledger_and_ticker(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            pd.DataFrame(
                [
                    {
                        "ledger": "btc15m_q1000_yes_shadow",
                        "event_ticker": "KXBTC15M-26MAY180530",
                        "market_ticker": "KXBTC15M-26MAY180530-30",
                        "created_at": "2026-05-18T09:19:48Z",
                        "official_status": "finalized",
                        "official_result": "yes",
                        "proxy_result": "yes",
                        "official_pnl": 0.52,
                        "proxy_pnl": 0.52,
                        "official_minus_proxy_spot": 54.36,
                        "proxy_close_minus_strike": 1.0,
                        "official_expiration_minus_strike": 2.0,
                        "entry_spot_distance_bps": 3.0,
                        "side": "yes",
                    },
                    {
                        "ledger": "btc1h_high_conf80_entry70_no_chase_shadow",
                        "event_ticker": "KXBTCD-26MAY1810",
                        "market_ticker": "KXBTCD-26MAY1810-T76699.99",
                        "created_at": "2026-05-18T13:43:15Z",
                        "official_status": "finalized",
                        "official_result": "yes",
                        "proxy_result": "yes",
                        "official_pnl": 0.30,
                        "proxy_pnl": 0.30,
                        "official_minus_proxy_spot": 57.88,
                        "proxy_close_minus_strike": 135.52,
                        "official_expiration_minus_strike": 193.40,
                        "entry_spot_distance_bps": 10.0,
                        "side": "yes",
                    },
                ]
            ).to_csv(root / "shadow_official_trades.csv", index=False)

            loaded = mod.load_btc1h(root)
            families = loaded.set_index("ledger")["family"].to_dict()
            self.assertEqual(families["btc15m_q1000_yes_shadow"], "BTC15M")
            self.assertEqual(families["btc1h_high_conf80_entry70_no_chase_shadow"], "BTC1H")


if __name__ == "__main__":
    unittest.main()
