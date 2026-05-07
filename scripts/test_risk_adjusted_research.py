#!/usr/bin/env python3
"""Unit checks for the risk-adjusted research sizing overlay."""

from __future__ import annotations

import unittest

from scripts.risk_adjusted_research import RiskSizingConfig, choose_risk_adjusted_contracts


class RiskAdjustedResearchSizingTest(unittest.TestCase):
    def test_high_entry_favorite_is_capped_at_one_contract(self) -> None:
        cfg = RiskSizingConfig(starting_bankroll=20.0, max_contracts=3)
        decision = choose_risk_adjusted_contracts(
            entry_price=0.67,
            model_p_yes=0.83,
            side="yes",
            bankroll=20.0,
            available_cash=20.0,
            active_exposure=0.0,
            config=cfg,
        )
        self.assertEqual(decision.contracts, 1)
        self.assertEqual(decision.price_band_cap, 1)
        self.assertEqual(decision.reason, "price_band_cap")

    def test_medium_entry_contract_count_is_budget_limited(self) -> None:
        cfg = RiskSizingConfig(starting_bankroll=20.0, max_contracts=3)
        decision = choose_risk_adjusted_contracts(
            entry_price=0.60,
            model_p_yes=0.76,
            side="yes",
            bankroll=20.0,
            available_cash=20.0,
            active_exposure=0.0,
            config=cfg,
        )
        self.assertEqual(decision.price_band_cap, 2)
        self.assertEqual(decision.contracts, 1)
        self.assertGreater(decision.risk_budget, decision.cost)

    def test_no_contract_when_conservative_edge_is_negative(self) -> None:
        cfg = RiskSizingConfig(starting_bankroll=20.0, max_contracts=3)
        decision = choose_risk_adjusted_contracts(
            entry_price=0.67,
            model_p_yes=0.60,
            side="yes",
            bankroll=20.0,
            available_cash=20.0,
            active_exposure=0.0,
            config=cfg,
        )
        self.assertEqual(decision.contracts, 0)
        self.assertEqual(decision.reason, "kelly_budget")

    def test_half_kelly_shadow_can_scale_with_large_bankroll(self) -> None:
        cfg = RiskSizingConfig(
            starting_bankroll=1000.0,
            max_contracts=10000,
            kelly_fraction=0.50,
            medium_entry_cap=1.01,
            high_entry_cap=1.01,
        )
        decision = choose_risk_adjusted_contracts(
            entry_price=0.65,
            model_p_yes=0.90,
            side="yes",
            bankroll=1000.0,
            available_cash=1000.0,
            active_exposure=0.0,
            config=cfg,
            available_qty=10000,
        )
        self.assertGreater(decision.contracts, 3)
        self.assertLessEqual(decision.cost, decision.budget)
        self.assertEqual(decision.price_band_cap, 10000)
        self.assertAlmostEqual(decision.applied_kelly_fraction, 0.5 * decision.full_kelly_fraction)

    def test_half_kelly_shadow_can_scale_no_side_too(self) -> None:
        cfg = RiskSizingConfig(
            starting_bankroll=1000.0,
            max_contracts=10000,
            kelly_fraction=0.50,
            medium_entry_cap=1.01,
            high_entry_cap=1.01,
            no_side_contract_cap=10000,
        )
        decision = choose_risk_adjusted_contracts(
            entry_price=0.65,
            model_p_yes=0.10,
            side="no",
            bankroll=1000.0,
            available_cash=1000.0,
            active_exposure=0.0,
            config=cfg,
            available_qty=10000,
        )
        self.assertGreater(decision.contracts, 3)
        self.assertLessEqual(decision.cost, decision.budget)


if __name__ == "__main__":
    unittest.main()
