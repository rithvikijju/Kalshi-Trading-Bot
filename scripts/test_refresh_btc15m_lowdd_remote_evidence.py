from scripts.refresh_btc15m_lowdd_remote_evidence import remote_join, win_quote


def test_remote_join_uses_windows_separators_without_duplicate_slashes():
    assert (
        remote_join(r"C:\Users\ClawService\Kalshi-Trading-Bot\\", r"\runtime", "codex_snapshots")
        == r"C:\Users\ClawService\Kalshi-Trading-Bot\runtime\codex_snapshots"
    )


def test_win_quote_leaves_python_launcher_unquoted():
    assert win_quote("python") == "python"


def test_win_quote_quotes_absolute_paths():
    assert win_quote(r"C:\Users\ClawService\Kalshi-Trading-Bot\.venv\Scripts\python.exe") == (
        r'"C:\Users\ClawService\Kalshi-Trading-Bot\.venv\Scripts\python.exe"'
    )
