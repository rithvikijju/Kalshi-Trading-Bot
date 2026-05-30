from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parent.parent
SCRIPTS = PROJECT_ROOT / "scripts"


def read_script(name: str) -> str:
    return (SCRIPTS / name).read_text(encoding="utf-8")


def test_remote_start_defaults_to_current_active_btc15m_targets_only():
    source = read_script("start_btc_remote_collectors.ps1")

    assert 'name = "btc15m_live_capture"; script = "scripts\\btc15m_live_capture.py"; args = @()' in source
    assert 'name = "btc15m_lowdd_forward_shadow"' in source
    assert 'script = "scripts\\btc15m_lowdd_live.py"' in source
    assert 'name = "btc15m_q250_qty500_firstskip_shadow"' not in source
    assert 'name = "btc15m_q250_qty500_firstskip_yes_shadow"' not in source
    assert 'name = "btc15m_q1000_yes_shadow"' not in source
    assert 'name = "btc1h_high_conf80_entry70_no_chase_shadow"' not in source


def test_remote_start_lowdd_target_is_paper_forward_shadow_not_live_defaults():
    source = read_script("start_btc_remote_collectors.ps1")

    for token in (
        '"--mode", "paper"',
        '"--env", "prod"',
        '"--strategy", "lowdd"',
        '"--shadow-bankroll", "100"',
        '"--trade-db-path"',
        '"btc15m_lowdd_forward_shadow_trades.db"',
        '"--capture-db-path"',
        '"btc15m_lowdd_forward_shadow_capture.duckdb"',
        '"--capture-writer", "persistent"',
    ):
        assert token in source

    assert '"btc15m_lowdd_live_trades.db"' not in source
    assert '"btc15m_live_capture.duckdb"' not in source
    assert '(@("-u", $target.script) + $targetArgs)' in source
    assert '(@("-u", `$script) + `$scriptArgs)' in source


def test_remote_stop_knows_current_active_lowdd_target_and_legacy_cleanup():
    source = read_script("stop_btc_remote_collectors.ps1")

    assert ".btc_kalshi_bot\\btc15m_lowdd_forward_shadow_capture.duckdb.status.json" in source
    assert '"btc15m_lowdd_forward_shadow.pid.json"' in source
    assert '"KalshiBTC_btc15m_lowdd_forward_shadow"' in source
    assert '"btc15m_lowdd_live.py"' in source

    # Keep legacy cleanup entries so old scheduled tasks/processes do not keep
    # writing stale q250/q1000/BTC1H evidence after the current-active restart.
    assert '"KalshiBTC_btc15m_q250_qty500_firstskip_shadow"' in source
    assert '"KalshiBTC_btc1h_high_conf80_entry70_no_chase_shadow"' in source


def test_remote_ensure_restarts_current_active_instead_of_blocking_on_legacy_start():
    source = read_script("ensure_btc_remote_collectors.ps1")

    assert 'restart_current_active_collectors' in source
    assert 'blocked_current_active_manual_restart_required' not in source
    assert 'would launch stale q250/q1000/BTC1H targets' not in source
