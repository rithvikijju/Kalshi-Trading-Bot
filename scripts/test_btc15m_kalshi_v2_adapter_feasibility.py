from scripts.audit_btc15m_kalshi_v2_adapter_feasibility import evaluate_branch_adapter


def base_branch(**overrides):
    row = {
        "ref": "origin/example",
        "commit_date": "2026-05-30 00:00:00 +0000",
        "commit": "abc1234",
        "subject": "example",
        "has_kalshi_v2": True,
        "has_backtest": True,
        "has_backtest_sweep": False,
        "has_model": True,
        "has_strategy": True,
        "backtest_filters_dash_t": True,
        "backtest_parses_dash_t": True,
        "backtest_uses_dedup_tables": True,
        "backtest_uses_all_tables": True,
        "uses_tp_sl_time_exit": True,
        "uses_empirical_bank": True,
        "uses_robust_filter": True,
        "uses_rest_floor_fields": True,
        "fair_value_can_accept_unknown_as_cumulative": True,
    }
    row.update(overrides)
    return row


def base_capture(**overrides):
    row = {
        "has_ws_orderbook_top": True,
        "has_coinbase_ticker": True,
        "has_ws_lifecycle": True,
        "has_ws_orderbook_top_dedup": False,
        "has_coinbase_ticker_all": False,
        "has_ws_lifecycle_all": False,
        "dash_t_markets": 0,
        "btc15m_binary_markets": 17,
        "market_count": 17,
        "coinbase_raw_book_field_rate": 0.0,
    }
    row.update(overrides)
    return row


def base_rest(**overrides):
    row = {
        "captured_markets": 17,
        "matched_markets": 17,
        "floor_strike_markets": 17,
        "result_markets": 17,
    }
    row.update(overrides)
    return row


def test_current_btc15m_capture_blocks_original_v2_but_allows_research_adapter():
    result = evaluate_branch_adapter(base_branch(), base_capture(), base_rest())

    assert result["original_v2_direct_backtest_possible"] is False
    assert result["hold_to_settlement_adapter_possible"] is True
    assert result["verdict"] == "research_adapter_feasible_not_original_v2_deployable"
    assert "capture_has_no_cumulative_dash_t_markets" in result["blockers"]
    assert "map_btc15m_binary_market_to_floor_strike_from_rest" in result["adapter_requirements"]
    assert "tp_sl_time_exit_not_deployment_ready_without_live_exit_validation" in result["advisories"]


def test_adapter_waits_for_complete_rest_metadata():
    result = evaluate_branch_adapter(
        base_branch(),
        base_capture(),
        base_rest(floor_strike_markets=12),
    )

    assert result["binary_metadata_adapter_possible"] is False
    assert result["hold_to_settlement_adapter_possible"] is False
    assert result["verdict"] == "adapter_blocked_by_rest_metadata_coverage"
    assert "complete_rest_floor_strike_metadata" in result["adapter_requirements"]


def test_original_direct_backtest_possible_when_old_schema_and_dash_t_exist():
    result = evaluate_branch_adapter(
        base_branch(),
        base_capture(
            has_ws_orderbook_top_dedup=True,
            has_coinbase_ticker_all=True,
            has_ws_lifecycle_all=True,
            dash_t_markets=25,
        ),
        base_rest(),
    )

    assert result["original_v2_direct_backtest_possible"] is True
    assert result["verdict"] == "direct_v2_backtest_possible"
